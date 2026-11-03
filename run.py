"""汇率监测预警智能体 - 主入口

用法：
  python run.py --once        单轮采集+分析+决策（验收/调试用）
  python run.py --loop        常驻轮询模式（正式运行）
  python run.py --simulate    演示模式：注入模拟急跌行情，触发完整预警链路
"""
import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fx_agent import fetcher, storage, analyzer, agent, alerter, simulate
import json

CFG_PATH = Path(__file__).resolve().parent / "config.json"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
logging.getLogger("urllib3").setLevel(logging.WARNING)


def load_cfg() -> dict:
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))


def cycle(cfg: dict, monitor_currencies: list[str], fetch: bool = True) -> int:
    """完整跑一轮：感知 -> 思考 -> 行动。返回触发预警数。"""
    # 心跳：向单实例守护证明"后台监控活着且在干活"
    try:
        from fx_agent.single_instance import heartbeat
        heartbeat()
    except Exception:
        pass
    # 感知
    if fetch:
        records = fetcher.fetch_boc()
        n = storage.save_rates(records)
        print(f"[感知] 抓到 {n} 个币种牌价，落库完成")
    else:
        print("[感知] 演示模式：跳过真实抓取，使用注入行情")

    # 分析 + 思考
    thresholds = cfg["thresholds_pct"]
    src = "boc" if fetch else None  # 演示模式读全部（含模拟），正常模式只读真实
    analyses = []
    print("\n[分析] 当日波动概览：")
    for cur in monitor_currencies:
        th = thresholds.get(cur, thresholds.get("默认", 0.1))
        a = analyzer.analyze(cur, th, source=src)
        if a is None:
            print(f"  {cur}: 当日首批数据，暂无基准，跳过")
            continue
        print(f"  {analyzer.summary_line(a)}")
        analyses.append(a)

    if not analyses:
        print("[思考] 暂无可分析数据")
        return 0
    decisions = agent.decide(analyses, cfg)
    if not fetch:  # 演示模式：打上模拟标记，不进入真实预警记录
        for d in decisions:
            d["simulated"] = True
    print(f"\n[思考] Agent 决策 {len(decisions)} 条：")
    for d in decisions:
        print(f"  {d['currency_pair']}: {d['agent_action']} ({d.get('severity') or '-'}) "
              f"引擎={d.get('engine')}")

    # 行动
    sent = alerter.dispatch(decisions, cfg, storage)
    print(f"\n[行动] 预警下发 {sent} 条；累计预警记录 {storage.alert_count()} 条")
    return sent


def main():
    ap = argparse.ArgumentParser(description="实时汇率监测预警智能体")
    ap.add_argument("--once", action="store_true", help="单轮采集决策")
    ap.add_argument("--loop", action="store_true", help="常驻轮询")
    ap.add_argument("--simulate", action="store_true", help="演示模式（注入模拟急跌）")
    args = ap.parse_args()

    cfg = load_cfg()
    storage.init_db()
    monitor = cfg["currencies"]

    if args.simulate:
        print("== 演示模式：注入美元急跌 3.2% 的模拟行情 ==")
        final = simulate.inject_shock()
        print(f"模拟注入完成，美元现价 {final:.4f}\n")
        cycle(cfg, ["美元"], fetch=False)
        return

    if args.once:
        cycle(cfg, monitor)
        return

    if args.loop:
        interval = cfg.get("poll_interval_seconds", 60)
        print(f"== 常驻模式：每 {interval} 秒轮询一次，Ctrl+C 退出 ==")
        while True:
            try:
                cycle(cfg, monitor)
            except Exception as e:
                logging.error("本轮异常（将在下轮重试）: %s", e)
            time.sleep(interval)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
