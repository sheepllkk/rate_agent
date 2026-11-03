"""行动层：多渠道预警下发。桌面弹窗 / 邮件 / 报警记录存档。"""
import json
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

logger = logging.getLogger(__name__)


def desktop_toast(title: str, message: str) -> bool:
    """Windows 原生系统通知。失败只记日志，不中断主流程。"""
    try:
        from winotify import Notification, audio
        n = Notification(app_id="汇率预警智能体", title=title, msg=message[:250], duration="long")
        n.set_audio(audio.Default, loop=False)
        n.show()
        return True
    except Exception as e:
        logger.warning("桌面通知失败: %s", e)
        return False


def send_email(cfg: dict, subject: str, body: str, override_to: str | None = None) -> bool:
    mail = cfg.get("alert", {}).get("email", {})
    if not mail.get("enabled"):
        return False
    to_addrs = [override_to] if override_to else list(mail.get("to", []))
    if not to_addrs:
        return False
    try:
        msg = MIMEMultipart()
        msg["Subject"] = subject
        msg["From"] = mail["user"]
        msg["To"] = ", ".join(to_addrs)
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP_SSL(mail["smtp_host"], mail.get("smtp_port", 465), timeout=15) as s:
            s.login(mail["user"], mail["password"])
            s.sendmail(mail["user"], to_addrs, msg.as_string())
        return True
    except Exception as e:
        logger.warning("邮件发送失败(%s): %s", override_to or "config", e)
        return False


def _fluct_value(s) -> float | None:
    """从 "0.35%"/"+0.3650%" 之类字符串里取波动幅度（绝对值）。"""
    try:
        return abs(float(str(s).replace("%", "").replace("+", "")))
    except (TypeError, ValueError):
        return None


def _should_suppress(d: dict, cfg: dict, storage) -> str | None:
    """防重复下发判定。返回抑制原因（None = 正常下发）。

    两层规则，判定基准只用客观波动率，不采信模型给的等级
    （思考型模型的 severity 在临界值附近会摇摆，用它判状态会被噪声骗）：
    1. 时间冷却：cooldown_minutes 内不重发，除非波动率较上次大幅变化 >=0.5 个百分点（真恶化）；
    2. 状态判定：冷却期外，同动作且波动幅度差 <0.2 个百分点 = 状态无实质变化，不重发。
    """
    pair = d["currency_pair"]
    cooldown = int(cfg.get("alert", {}).get("cooldown_minutes", 30) or 0)
    last = storage.last_alert_payload(pair)
    if last is None:
        return None  # 首次预警，直接放行

    # 事件周期：上一次预警太久远（默认 24 小时）就视为新事件，
    # 不参与状态比较——否则一周前的旧状态会把今天的新行情永远压住。
    ep_hours = int(cfg.get("alert", {}).get("episode_hours", 24) or 0)
    if ep_hours > 0:
        last_ts = storage.last_alert_time(pair)
        if last_ts:
            try:
                from datetime import datetime as _dt
                age_h = (_dt.now() - _dt.strptime(last_ts, "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
                if age_h >= ep_hours:
                    return None
            except ValueError:
                pass

    cur_f = _fluct_value(d.get("fluctuation"))
    last_f = _fluct_value(last.get("fluctuation"))
    if cur_f is None or last_f is None:
        return None  # 波动率缺失时不做状态判定，宁可多发不可漏发
    moved_big = abs(cur_f - last_f) >= 0.3

    if cooldown > 0 and storage.recent_alert_exists(pair, cooldown):
        if moved_big:
            return None  # 冷却期内行情大幅恶化，立刻再报
        return f"{cooldown} 分钟内已预警过且波动无显著变化"
    if (d.get("agent_action") == last.get("agent_action")
            and abs(cur_f - last_f) < 0.2):
        return "行情状态与上次预警相比无实质变化"
    return None


def dispatch(decisions: list[dict], cfg: dict, storage) -> int:
    """把 Agent 决策落成真实预警动作。返回发出的预警数。"""
    sent = 0
    for d in decisions:
        if d.get("agent_action") != "trigger_alert":
            continue
        # 防重复：时间冷却 + 状态判定，双重抑制预警轰炸
        reason = _should_suppress(d, cfg, storage)
        if reason:
            print(f"\n>>> 冷却抑制：{d['currency_pair']} —— {reason}，本次不再下发")
            continue
        payload = json.dumps(d, ensure_ascii=False, indent=2)
        title = f"【{d.get('severity', '预警')}】{d['currency_pair']} 波动预警"
        message = f"现价 {d['current_rate']}，波动 {d['fluctuation']}（阈值 {d['threshold_set']}）\n{d.get('reason', '')}"
        storage.save_alert(payload, d["currency_pair"])
        print(f"\n>>> 预警触发 [{d.get('engine', '?')} 引擎]")
        print(payload)
        if cfg.get("alert", {}).get("desktop_toast", True):
            desktop_toast(title, message)
            sent += 1
        # 发给配置邮箱 + 所有订阅用户（小程序"开启预警"注册的邮箱）
        to_list = list(cfg.get("alert", {}).get("email", {}).get("to", []))
        try:
            to_list += [e for e in storage.get_subscribers() if e not in to_list]
        except Exception:
            pass
        for addr in to_list:
            if send_email(cfg, title, message, override_to=addr):
                sent += 1
    return sent
