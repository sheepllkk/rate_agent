"""后台常驻启动器（开机自启用）。

与 run.py --loop 的区别：
- 由任务计划程序在登录时用 pythonw.exe（无窗口）拉起，看不到控制台；
- print/logging 输出重定向到 data/loop.log，方便回查运行情况；
- 复用 run.py 的 main()，业务逻辑零重复。

手动停止：运行项目根目录的 stop_bg_monitor.bat（按命令行特征精确结束本进程）。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

# 清掉可能残留的代理环境变量：监控程序直连中行官网与模型网关，
# 走代理反而可能连不上或被拦（曾实测导致请求挂起）。
for _k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
           "http_proxy", "https_proxy", "all_proxy"):
    os.environ.pop(_k, None)

LOG = ROOT / "data" / "loop.log"
LOG_MAX_BYTES = 5 * 1024 * 1024  # 超过 5MB 时重新开始记录，避免无限膨胀

if LOG.exists() and LOG.stat().st_size > LOG_MAX_BYTES:
    LOG.unlink()

# ---- 单实例守护：重复双击启动、或持有者是僵尸（活着不干活）时都能正确接管 ----
from fx_agent import single_instance

if not single_instance.acquire():
    # 已有健康实例在跑。不写日志（日志归现有实例管），安静退出。
    sys.exit(0)

LOG.parent.mkdir(parents=True, exist_ok=True)
logf = open(LOG, "a", encoding="utf-8", buffering=1)


class _Tee:
    """把 stdout/stderr 写进日志文件。pythonw 下原始流为 None，需兜底。"""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, text):
        for s in self._streams:
            if s is not None:
                try:
                    s.write(text)
                except Exception:
                    pass
        return len(text)

    def flush(self):
        for s in self._streams:
            if s is not None:
                try:
                    s.flush()
                except Exception:
                    pass


sys.stdout = _Tee(logf)
sys.stderr = _Tee(logf)

import run  # noqa: E402  （在重定向之后导入，logging 才会写入日志文件）

sys.argv = ["run.py", "--loop"]

# 数据服务（server.py）随监控一起启动：小程序的接口就靠它。
# 若 8788 已被单独运行的 server.py 占用，这里会失败但不影响监控本身。
try:
    import server as fx_server  # noqa: E402
    fx_server.start_server()
    print("[数据服务] API 已随监控启动：http://127.0.0.1:8788 （小程序用）")
except Exception as e:
    print(f"[数据服务] 随监控启动失败（可能已有 server.py 在跑，不影响监控）: {e}")

# 自愈：任何未捕获异常（网络抖动、磁盘占用冲突等）记录后等 30 秒重启循环，
# 保证"开机就在跑"，不需要人盯着救活。
while True:
    try:
        run.main()
    except KeyboardInterrupt:
        print("收到退出信号，后台监控停止。")
        break
    except Exception:
        import traceback
        traceback.print_exc()
        print("发生异常，30 秒后自动重启监控循环…")
    try:
        import time
        time.sleep(30)
    except Exception:
        pass
