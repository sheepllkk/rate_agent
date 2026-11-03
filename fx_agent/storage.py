"""存储层：SQLite 落盘汇率历史与预警记录。"""
import json
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "rates.db"


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            currency TEXT NOT NULL,
            code TEXT,
            rate REAL NOT NULL,          -- 单位报价（1 外币兑人民币）
            rate_hundred REAL,           -- 中行原始百元报价
            buy REAL, sell REAL,
            publish_time TEXT,
            source TEXT DEFAULT 'boc',   -- boc=真实抓取 / simulated=演示模拟
            fetched_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_rates_cur_time ON rates(currency, fetched_at);
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL,       -- Agent 决策 JSON
            currency TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS subscribers (
            email TEXT PRIMARY KEY,
            created_at TEXT NOT NULL
        );
        """)


def save_rates(records: list[dict]) -> int:
    with _conn() as c:
        c.executemany(
            """INSERT INTO rates (currency, code, rate, rate_hundred, buy, sell,
               publish_time, source, fetched_at)
               VALUES (:currency, :code, :rate, :rate_hundred, :buy, :sell,
               :publish_time, :source, :fetched_at)""",
            records,
        )
    return len(records)


def history(currency: str, limit: int = 500, source: str | None = None) -> list[dict]:
    """取某币种最近 limit 条记录（按时间正序）。

    source='boc' 只取真实抓取数据；None 不过滤（演示模式用）。
    """
    with _conn() as c:
        if source:
            rows = c.execute(
                """SELECT * FROM (SELECT * FROM rates WHERE currency=? AND source=?
                   ORDER BY id DESC LIMIT ?) ORDER BY id ASC""",
                (currency, source, limit),
            ).fetchall()
        else:
            rows = c.execute(
                """SELECT * FROM (SELECT * FROM rates WHERE currency=?
                   ORDER BY id DESC LIMIT ?) ORDER BY id ASC""",
                (currency, limit),
            ).fetchall()
    return [dict(r) for r in rows]


def clear_simulated() -> int:
    """清除历史模拟记录（每次演示注入前调用，保证真实数据纯净）。"""
    with _conn() as c:
        cur = c.execute("DELETE FROM rates WHERE source='simulated'")
        return cur.rowcount


def save_alert(payload: str, currency: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO alerts (payload, currency, created_at) VALUES (?,?,?)",
            (payload, currency, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        return cur.lastrowid


def alert_count() -> int:
    with _conn() as c:
        return c.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]


def recent_alert_exists(currency: str, minutes: int) -> bool:
    """冷却判定：该币种在最近 minutes 分钟内是否已发过预警。"""
    with _conn() as c:
        row = c.execute(
            "SELECT 1 FROM alerts WHERE currency=? "
            "AND created_at >= datetime('now', 'localtime', ?) LIMIT 1",
            (currency, f"-{int(minutes)} minutes"),
        ).fetchone()
        return row is not None


def last_alert_payload(currency: str) -> dict | None:
    """取该币种最近一次预警的决策内容（用于'状态未变不重发'判定）。"""
    with _conn() as c:
        row = c.execute(
            "SELECT payload FROM alerts WHERE currency=? ORDER BY id DESC LIMIT 1",
            (currency,),
        ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row[0])
    except Exception:
        return None


def last_alert_time(currency: str) -> str | None:
    """取该币种最近一次预警的时间（%Y-%m-%d %H:%M:%S），没有则 None。"""
    with _conn() as c:
        row = c.execute(
            "SELECT created_at FROM alerts WHERE currency=? ORDER BY id DESC LIMIT 1",
            (currency,),
        ).fetchone()
        return row[0] if row else None


def add_subscriber(email: str) -> bool:
    """注册预警邮箱订阅；已存在返回 False。"""
    with _conn() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO subscribers (email, created_at) VALUES (?,?)",
            (email, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        return cur.rowcount > 0


def get_subscribers() -> list[str]:
    with _conn() as c:
        return [r[0] for r in c.execute("SELECT email FROM subscribers")]
