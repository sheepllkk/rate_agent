"""数据感知层：抓取中国银行外汇牌价（已实测无反爬，纯静态 HTML）。"""
import re
import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)

BOC_URL = "https://www.boc.cn/sourcedb/whpj/"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
}
_TAG = re.compile(r"<[^>]+>")

# 赛题要求的币种 -> 标准代码映射（用于 currency_pair 输出）
CURRENCY_CODE = {
    "美元": "USD", "欧元": "EUR", "港币": "HKD", "英镑": "GBP",
    "日元": "JPY", "澳大利亚元": "AUD", "加拿大元": "CAD",
    "新加坡元": "SGD", "瑞士法郎": "CHF", "澳门元": "MOP",
    "韩元": "KRW", "泰铢": "THB", "新台币": "TWD",
    "俄罗斯卢布": "RUB", "南非兰特": "ZAR", "新西兰元": "NZD",
}


def fetch_boc(timeout: int = 15) -> list[dict]:
    """抓取中行外汇牌价页，返回结构化记录列表。

    中行牌价单位为「100 外币兑人民币」，本函数保留原始百元报价
    （rate_hundred），并换算出单位报价（rate = 报价/100）。
    采用「中行折算价」作为监测基准价，同时保留现汇/现钞买卖价。
    """
    resp = requests.get(BOC_URL, headers=HEADERS, timeout=timeout)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    html = resp.text

    records = []
    fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"\s+", "", _TAG.sub("", c)) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        if len(cells) < 7 or cells[0] not in CURRENCY_CODE:
            continue
        try:
            name = cells[0]
            buy = float(cells[1])       # 现汇买入价
            buy_cash = float(cells[2])  # 现钞买入价
            sell = float(cells[3])      # 现汇卖出价
            sell_cash = float(cells[4]) # 现钞卖出价
            mid = float(cells[5])       # 中行折算价
        except (ValueError, IndexError):
            continue
        publish_time = cells[6] if len(cells) > 6 else ""
        records.append({
            "currency": name,
            "code": CURRENCY_CODE[name],
            "rate_hundred": mid,          # 中行折算价（100 外币）
            "rate": mid / 100.0,          # 单位报价
            "buy": buy / 100.0,
            "sell": sell / 100.0,
            "publish_time": publish_time,
            "source": "boc",
            "fetched_at": fetched_at,
        })
    logger.info("中行牌价抓取成功：%d 个币种", len(records))
    return records
