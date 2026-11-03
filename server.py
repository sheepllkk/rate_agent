"""移动端数据服务：把监控系统的 SQLite 数据开放为 HTTP 接口，供小程序拉取。

运行：python server.py   （与 gui.py/run.py 共用同一数据库，只读）
接口：
  GET /api/latest            各监控币种最新快照（现价/波动/是否触发）
  GET /api/rates?cur=美元&n=50   某币种最近 n 条历史
  GET /api/alerts?n=20       最近 n 条预警记录
"""
import json
import sqlite3
import sys
import socket
import threading
from datetime import datetime, date, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fx_agent import fetcher, analyzer, agent, alerter, storage  # noqa: E402

CFG = json.loads((Path(__file__).resolve().parent / "config.json").read_text(encoding="utf-8"))

_refresh_lock = threading.Lock()
_FMT = "%Y-%m-%d %H:%M:%S"


def maybe_refresh() -> None:
    """数据过期时自动跑一轮完整工作流（感知-思考-行动）。

    有了它，只开 server.py 整个系统就是活的：小程序一刷新，
    数据服务自己抓新数据、跑 Agent 决策、必要时发预警。
    """
    interval = CFG.get("poll_interval_seconds", 60)
    with _refresh_lock:
        try:
            with sqlite3.connect(storage.DB_PATH) as c:
                row = c.execute("SELECT MAX(fetched_at) FROM rates").fetchone()
            now = datetime.now()
            if row and row[0]:
                age = (now - datetime.strptime(row[0], _FMT)).total_seconds()
                if age < interval:
                    return
            records = fetcher.fetch_boc()
            storage.save_rates(records)
            print(f"[自动刷新] {now.strftime('%H:%M:%S')} 抓到 {len(records)} 币种")
            thresholds = CFG["thresholds_pct"]
            analyses = []
            for cur in CFG["currencies"]:
                th = thresholds.get(cur, thresholds.get("默认", 0.1))
                a = analyzer.analyze(cur, th)
                if a:
                    analyses.append(a)
            if analyses:
                decisions = agent.decide(analyses, CFG)
                alerter.dispatch(decisions, CFG, storage)
        except Exception as e:
            print("[自动刷新] 失败:", e)


def latest_snapshot() -> list[dict]:
    thresholds = CFG["thresholds_pct"]
    out = []
    for cur in CFG["currencies"]:
        th = thresholds.get(cur, thresholds.get("默认", 0.1))
        a = analyzer.analyze(cur, th, source="boc")  # 对外接口只吐真实数据
        if a:
            out.append({
                "currency": a["currency"], "code": a["code"],
                "rate": round(a["rate"], 4), "change_pct": a["change_pct"],
                "day_high": round(a["day_high"], 4), "day_low": round(a["day_low"], 4),
                "threshold_pct": a["threshold_pct"], "triggered": a["triggered"],
                "publish_time": a["publish_time"], "source": a["source"],
            })
    return out


def rates_history(cur: str, n: int, days: int = 1) -> list[dict]:
    """最近 n 条历史，限定最近 days 天（1=当日）。走势图只画真实数据。"""
    hist = storage.history(cur, n, source="boc")
    start = (date.today() - timedelta(days=days - 1)).isoformat()
    return [{"rate": h["rate"], "fetched_at": h["fetched_at"], "source": h["source"]}
            for h in hist if h["fetched_at"][:10] >= start]


def recent_alerts(n: int = 100, include_simulated: bool = False) -> list[dict]:
    """真实预警记录；include_simulated=True 时才包含演示预警。"""
    with sqlite3.connect(storage.DB_PATH) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT payload, created_at FROM alerts ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
    out = []
    for r in rows:
        try:
            d = json.loads(r["payload"])
        except json.JSONDecodeError:
            continue
        if d.get("simulated") and not include_simulated:
            continue  # 服务端过滤：预警记录列表永远只返回真实数据
        d["created_at"] = r["created_at"]
        out.append(d)
    return out


def recent_demo_alert(minutes: int = 5) -> dict | None:
    """minutes 分钟内的最新演示预警（供小程序蓝色「演示」横幅）。"""
    rows = recent_alerts(20, include_simulated=True)
    now = datetime.now()
    for d in rows:
        if not d.get("simulated"):
            continue
        try:
            ts = datetime.strptime(d["created_at"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, ValueError):
            continue
        if (now - ts).total_seconds() <= minutes * 60:
            return d
    return None


def all_latest() -> list[dict]:
    """全部币种最新行情（最近一批真实采集），供"全部币种"浏览模式。"""
    with sqlite3.connect(storage.DB_PATH) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT MAX(fetched_at) FROM rates WHERE source='boc'").fetchone()
        if not row or not row[0]:
            return []
        rows = c.execute("SELECT * FROM rates WHERE fetched_at=? AND source='boc'",
                         (row[0],)).fetchall()
    out = []
    for r in rows:
        cur = r["currency"]
        th = CFG["thresholds_pct"].get(cur, CFG["thresholds_pct"].get("默认", 0.1))
        a = analyzer.analyze(cur, th, source="boc")
        out.append({
            "currency": cur, "code": r["code"],
            "rate": round(r["rate"], 4),
            "change_pct": a["change_pct"] if a else 0.0,
            "monitored": cur in CFG["currencies"],
        })
    out.sort(key=lambda x: (not x["monitored"], x["code"]))
    return out


def get_settings() -> dict:
    """当前监控配置 + 可选币种全集（供小程序设置页）。"""
    return {
        "currencies": CFG["currencies"],
        "thresholds_pct": CFG["thresholds_pct"],
        "all_currencies": sorted(fetcher.CURRENCY_CODE.keys()),
    }


def save_settings(currencies: list, thresholds: dict) -> dict:
    """校验并保存小程序提交的监控配置，写回 config.json。"""
    currencies = [c for c in currencies if c in fetcher.CURRENCY_CODE]
    if not currencies:
        raise ValueError("至少保留一个监控币种")
    clean = {}
    for k, v in (thresholds or {}).items():
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        if 0 < fv <= 100:
            clean[k] = fv
    merged = dict(CFG.get("thresholds_pct", {}))
    default = merged.get("默认", 0.1)
    merged = {k: merged.get(k, default) for k in currencies}
    merged.update({k: v for k, v in clean.items() if k in currencies})
    merged["默认"] = default
    CFG["currencies"] = currencies
    CFG["thresholds_pct"] = merged
    p = Path(__file__).resolve().parent / "config.json"
    p.write_text(json.dumps(CFG, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"currencies": currencies, "thresholds_pct": merged}


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            maybe_refresh()  # 数据过期则自动跑一轮完整工作流
            # HTTP 请求行默认按 latin-1 解码，中文参数（如 cur=美元）需还原为 UTF-8
            try:
                self.path = self.path.encode("latin-1").decode("utf-8")
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path == "/api/settings":
                self._send(200, {"ok": True, "data": get_settings()})
            elif u.path == "/api/all":
                self._send(200, {"ok": True, "data": all_latest()})
            elif u.path == "/api/latest":
                self._send(200, {"ok": True, "data": latest_snapshot(),
                                 "demo_alert": recent_demo_alert()})
            elif u.path == "/api/rates":
                cur = q.get("cur", ["美元"])[0]
                n = min(int(q.get("n", ["50"])[0]), 3000)
                days = min(int(q.get("days", ["1"])[0]), 30)
                self._send(200, {"ok": True, "data": rates_history(cur, n, days)})
            elif u.path == "/api/alerts":
                n = min(int(q.get("n", ["20"])[0]), 100)
                self._send(200, {"ok": True, "data": recent_alerts(n)})
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        """小程序提交预警订阅：POST /api/subscribe {"email": "..."}"""
        try:
            if urlparse(self.path).path == "/api/subscribe":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                email = (body.get("email") or "").strip()
                import re
                if not re.fullmatch(r"[\w.+-]+@[\w-]+\.[\w.]+", email):
                    self._send(400, {"ok": False, "error": "邮箱格式不对"})
                    return
                added = storage.add_subscriber(email)
                self._send(200, {
                    "ok": True,
                    "added": added,
                    "msg": "订阅成功！触发预警时会发邮件到您的邮箱，退出小程序也照常收到。"
                           if added else "这个邮箱已经订阅过了。",
                })
            elif urlparse(self.path).path == "/api/settings":
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                data = save_settings(body.get("currencies", []), body.get("thresholds", {}))
                self._send(200, {"ok": True, "data": data,
                                 "msg": "保存成功，下一轮采集即生效"})
            else:
                self._send(404, {"ok": False, "error": "not found"})
        except Exception as e:
            self._send(500, {"ok": False, "error": str(e)})

    def log_message(self, *a):
        pass  # 安静模式


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def start_server(port: int = 8788):
    """启动 API 数据服务（供 bg_loop 开机自启以线程方式拉起，也可独立运行）。"""
    storage.init_db()
    httpd = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True,
                     name="fx-api-server").start()
    return httpd


if __name__ == "__main__":
    httpd = start_server()
    port = httpd.server_address[1]
    ip = lan_ip()
    print(f"数据服务已启动：")
    print(f"  本机访问   http://127.0.0.1:{port}/api/latest")
    print(f"  手机/小程序 http://{ip}:{port}/api/latest   （需同一局域网）")
    httpd.serve_forever()
