"""波动分析层：基于 pandas 的时间序列统计（赛题明确允许）。"""
import pandas as pd

from . import storage


def analyze(currency: str, threshold_pct: float, source: str | None = "boc") -> dict | None:
    """分析某币种当前波动情况。

    波动基准：当日最早一条记录（相当于"开盘价"），
    这是银行牌价监测最自然的口径。
    source='boc'（默认）只分析真实数据，演示模式传 None 读全部。
    返回 None 表示数据不足（当天首采）。
    """
    hist = storage.history(currency, source=source)
    if not hist:
        return None
    df = pd.DataFrame(hist)
    df["fetched_at"] = pd.to_datetime(df["fetched_at"])
    df = df.sort_values("fetched_at")

    current = df.iloc[-1]
    day = current["fetched_at"].date()
    today = df[df["fetched_at"].dt.date == day]
    base = float(today.iloc[0]["rate"])
    rate = float(current["rate"])

    # 全部转 Python 原生类型（numpy 类型无法 JSON 序列化，LLM 层会炸）
    change_pct = float((rate - base) / base * 100.0)
    return {
        "currency": str(current["currency"]),
        "code": str(current.get("code", "")),
        "rate": rate,
        "base_rate": base,
        "change_pct": round(change_pct, 4),
        "abs_change_pct": round(abs(change_pct), 4),
        "threshold_pct": float(threshold_pct),
        "triggered": bool(abs(change_pct) >= threshold_pct),
        "day_high": float(today["rate"].max()),
        "day_low": float(today["rate"].min()),
        "samples_today": int(len(today)),
        "publish_time": str(current.get("publish_time", "")),
        "source": str(current.get("source", "boc")),
    }


def summary_line(a: dict) -> str:
    """把分析结果压成一行人话，供界面/日志/LLM 使用。"""
    arrow = "↑" if a["change_pct"] > 0 else ("↓" if a["change_pct"] < 0 else "→")
    return (f"{a['currency']}({a['code']}) 现价 {a['rate']:.4f} "
            f"{arrow} {a['change_pct']:+.2f}% "
            f"(日内 {a['day_low']:.4f}~{a['day_high']:.4f}, 阈值 {a['threshold_pct']}%, "
            f"{'已触发' if a['triggered'] else '未触发'})")
