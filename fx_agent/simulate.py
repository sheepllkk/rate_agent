"""演示模式：注入合成行情，解决"真实汇率日内到不了 3%"的演示难题。

原理：向数据库写入一段模拟的美元急跌行情（源标记为 simulated），
随后走与真实模式完全相同的 分析 -> Agent 决策 -> 预警 链路。
"""
import logging
import random
from datetime import datetime, timedelta

from . import storage


def inject_shock(currency: str = "美元", code: str = "USD",
                 base: float = 6.72, drop_pct: float = 3.2, n: int = 12) -> float:
    """写入 n 条模拟历史 + 1 条触发行情，返回最终权益价。

    注入前先清掉上一次演示残留的模拟记录，保证库中真实数据不被污染。
    """
    cleared = storage.clear_simulated()
    if cleared:
        logging.getLogger(__name__).info("已清除 %d 条历史模拟记录", cleared)
    now = datetime.now()
    records = []
    # 当日更早时候的平稳行情（作为波动基准"开盘价"）
    for i in range(n):
        t = (now - timedelta(minutes=30 - i)).strftime("%Y-%m-%d %H:%M:%S")
        rate = base + random.uniform(-0.0003, 0.0003)
        records.append(_rec(currency, code, rate, t))
    storage.save_rates(records)

    # 当前行情：急跌 drop_pct
    final = base * (1 - drop_pct / 100.0)
    storage.save_rates([_rec(currency, code, final, now.strftime("%Y-%m-%d %H:%M:%S"))])
    return final


def _rec(currency: str, code: str, rate: float, ts: str) -> dict:
    hundred = round(rate * 100, 2)
    return {
        "currency": currency, "code": code,
        "rate": rate, "rate_hundred": hundred,
        "buy": rate * 0.998, "sell": rate * 1.002,
        "publish_time": ts, "source": "simulated", "fetched_at": ts,
    }
