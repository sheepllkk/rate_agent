"""思考层：大模型 Agent 决策（感知-思考-行动 中的"思考"）。

支持多引擎依次降级：配置中的 llm 为主引擎，llm.fallback（如有）为备用引擎，
全部失败或未配置时降级为规则决策，保证系统在离线/评审环境也能完整演示。
各引擎的输出 JSON 结构完全一致，engine 字段标注实际使用的引擎与模型。
"""
import json
import logging
from datetime import datetime, timezone

import requests as http

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是外汇监控系统的决策 Agent。你会收到各币种的实时分析数据（现价、日内波动率、阈值）。
请对每个币种独立决策，严格输出 JSON 数组，每个元素包含字段：
- agent_action: "trigger_alert"（达到阈值需要预警）或 "observe"（继续观察）
- severity: "紧急" | "警告"（仅 trigger_alert 时填写；这是机械判定：abs(fluctuation) 达到 threshold 的 2 倍及以上填"紧急"，否则填"警告"。不要自行裁量，不要讨论规则本身）
- currency_pair: 如 "USD/CNY"
- current_rate: 当前价（数字）
- fluctuation: 如 "0.35%"（带符号）
- threshold_set: 如 "0.10%"
- timestamp: 当前 UTC 时间，格式 "2026-04-28T11:18:00Z"
- reason: 只写一句给用户看的结论，格式示例："英镑兑人民币日内升值0.37%，突破0.30%预警阈值，建议关注换汇时机。"

reason 的硬性要求：不出现任何思考过程、犹豫、自我修正或对规则的解释；
禁止出现"修正""可能""或者""一般理解""模糊"等字样。
规则：abs(fluctuation) >= threshold_set 时必须 trigger_alert。
只输出 JSON 数组，不要输出其他任何文字。"""

PAIR = {"USD": "USD/CNY", "EUR": "EUR/CNY", "HKD": "HKD/CNY", "GBP": "GBP/CNY",
        "JPY": "JPY/CNY", "AUD": "AUD/CNY", "CAD": "CAD/CNY", "SGD": "SGD/CNY"}


def _fallback_reason(a: dict) -> str:
    if a["triggered"]:
        sev = "紧急" if a["abs_change_pct"] >= a["threshold_pct"] * 2 else "警告"
        direction = "升值" if a["change_pct"] > 0 else "贬值"
        return (f"{a['currency']}兑人民币日内{direction} {a['change_pct']:+.2f}%，"
                f"已突破 {a['threshold_pct']}% 预警阈值，建议关注持仓或换汇时机。")
    return (f"{a['currency']}日内波动 {a['change_pct']:+.2f}%，"
            f"在阈值 {a['threshold_pct']}% 以内，继续观察。")


_META_MARKS = ("修正", "模糊", "一般理解", "让我", "等等", "首先", "规则说", "可能是指")


def _sanitize_reason(d: dict, analysis: dict | None):
    """把混入 reason 的思考过程替换成干净的模板结论。

    大模型偶尔会在 reason 里自我修正、讨论规则口径（如"修正：…题目描述模糊…"），
    这类文字会原样展示给用户。检测到元话语或超长文本时，改用数据分析层的模板结论。
    """
    r = str(d.get("reason", ""))
    if not any(m in r for m in _META_MARKS) and len(r) <= 120:
        return
    a = analysis
    if a:
        d["reason"] = _fallback_reason(a)
    else:
        pair = d.get("currency_pair", "?")
        if d.get("agent_action") == "trigger_alert":
            d["reason"] = (f"{pair} 日内波动 {d.get('fluctuation', '?')}，"
                           f"突破预警阈值 {d.get('threshold_set', '?')}，触发{d.get('severity', '')}预警。")
        else:
            d["reason"] = (f"{pair} 日内波动 {d.get('fluctuation', '?')}，"
                           f"在阈值 {d.get('threshold_set', '?')} 以内，继续观察。")


def _rule_decision(a: dict) -> dict:
    triggered = a["triggered"]
    sev = "紧急" if a["abs_change_pct"] >= a["threshold_pct"] * 2 else "警告"
    pair = PAIR.get(a["code"], f"{a['code']}/CNY")
    return {
        "agent_action": "trigger_alert" if triggered else "observe",
        "severity": sev if triggered else "",
        "data_source": "中国银行",
        "currency_pair": pair,
        "current_rate": round(a["rate"], 4),
        "fluctuation": f"{a['change_pct']:+.2f}%",
        "threshold_set": f"{a['threshold_pct']:.2f}%",
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "reason": _fallback_reason(a),
        "engine": "rule",  # 决策引擎标记：rule / llm
    }


def _llm_call(provider: dict, analyses: list[dict]) -> list[dict] | None:
    """调用单个 OpenAI 兼容引擎；任何异常返回 None 由上层尝试下一个引擎。"""
    if not provider.get("api_key"):
        return None
    url = provider["api_base"].rstrip("/") + "/chat/completions"
    # 鉴权方式：raw = 直接放 key（联通云网关文档写法）；默认 Bearer（DeepSeek/OpenAI 风格）
    if provider.get("auth") == "raw":
        auth_header = provider["api_key"]
    else:
        auth_header = f"Bearer {provider['api_key']}"
    body = {
        "model": provider.get("model", "deepseek-chat"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(analyses, ensure_ascii=False, default=str)},
        ],
        "temperature": 0.2,
        # 思考型模型（如 Qwen3.6）的思维链会计入 max_tokens，留足余量防止 JSON 被截断
        "max_tokens": 3000,
    }
    try:
        r = http.post(url, json=body,
                      headers={"Authorization": auth_header},
                      timeout=30)
        r.raise_for_status()
        text = r.json()["choices"][0]["message"]["content"].strip()
        text = text[text.find("["): text.rfind("]") + 1]  # 剥掉可能的 ```json 包裹
        out = json.loads(text)
        by_pair = {PAIR.get(a["code"], f"{a['code']}/CNY"): a for a in analyses}
        for d in out:
            d.setdefault("data_source", "中国银行")
            # 时间戳以本机 UTC 为准：模型可能照抄提示词中的示例时间
            d["timestamp"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            d["engine"] = f"llm:{provider.get('model', '?')}"
            # 输出清洗：模型若把思考过程写进 reason（自我修正/讨论规则），用模板结论替换
            _sanitize_reason(d, by_pair.get(d.get("currency_pair", "")))
        return out
    except Exception as e:
        logger.warning("LLM 引擎 %s 决策失败: %s", provider.get("model"), e)
        return None


def _providers(cfg: dict) -> list[dict]:
    """主引擎在前，备用引擎（cfg.llm.fallback，如有）在后。"""
    primary = dict(cfg.get("llm", {}))
    providers = [primary] if primary.get("api_key") else []
    fb = primary.get("fallback")
    if isinstance(fb, dict) and fb.get("api_key"):
        providers.append(fb)
    return providers


def decide(analyses: list[dict], cfg: dict) -> list[dict]:
    """对一批分析结果做决策。按配置顺序依次尝试各 LLM 引擎，全部失败则规则兜底。"""
    for provider in _providers(cfg):
        out = _llm_call(provider, analyses)
        if out is not None:
            logger.info("Agent 决策引擎：LLM（%s）", provider.get("model"))
            return out
    logger.info("Agent 决策引擎：规则兜底（未配置 LLM 或全部调用失败）")
    return [_rule_decision(a) for a in analyses]
