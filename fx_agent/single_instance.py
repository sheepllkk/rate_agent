"""单实例守护锁（心跳版）。

保证后台监控同时只有一个实例在跑：
- 用文件锁（msvcrt.locking）做互斥；
- 锁持有者每轮工作循环刷新心跳时间戳（data/bg_heartbeat.txt）；
- 新实例启动时若锁被占，先看持有者心跳：心跳还在（<180 秒）说明真有实例在干活，
  本实例安静退出；心跳停了说明持有者是僵尸（活着但不干活），按 pid 记录清掉它再接管。

背景：Windows 上进程可能"活着但不工作"（如被宿主环境挂起），
单靠文件锁会把整个系统卡死在一个僵尸手里，所以必须配心跳。
"""
import os
import time
from pathlib import Path

import msvcrt

ROOT = Path(__file__).resolve().parent.parent  # 项目根（本文件在 fx_agent/ 包内）
DATA = ROOT / "data"
LOCK_PATH = DATA / "bg_loop.lock"
PID_PATH = DATA / "bg_pid.txt"
HEARTBEAT_PATH = DATA / "bg_heartbeat.txt"
HEARTBEAT_STALE_SECONDS = 180


def _write_heartbeat() -> None:
    try:
        DATA.mkdir(parents=True, exist_ok=True)
        HEARTBEAT_PATH.write_text(str(time.time()), encoding="utf-8")
    except Exception:
        pass


def _heartbeat_fresh() -> bool:
    try:
        return (time.time() - float(HEARTBEAT_PATH.read_text(encoding="utf-8"))) \
            < HEARTBEAT_STALE_SECONDS
    except Exception:
        return False


def _kill_holder() -> None:
    """按 pid 记录清掉僵尸持有者。"""
    try:
        pid = int(PID_PATH.read_text(encoding="utf-8").strip())
    except Exception:
        return
    if pid == os.getpid():
        return
    try:
        os.kill(pid, 9)
    except Exception:
        pass


def acquire() -> bool:
    """尝试成为唯一实例。返回 True=拿到锁可以干活；False=已有健康实例在跑。"""
    DATA.mkdir(parents=True, exist_ok=True)
    f = open(LOCK_PATH, "a+")
    f.write("0")  # 垫一个字节：空文件上锁的是"不存在的字节"，Windows 不判冲突，锁形同虚设
    f.flush()
    f.seek(0)
    for attempt in range(2):
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            # 拿到锁：登记 pid，立刻报一次心跳
            f.seek(0)
            f.truncate()
            f.write(str(os.getpid()))
            f.flush()
            _write_heartbeat()
            return True
        except OSError:
            if attempt == 0 and not _heartbeat_fresh():
                _kill_holder()  # 僵尸持有者，清掉再试一次
                time.sleep(1.5)
                continue
            return False
    return False


def heartbeat() -> None:
    """工作循环每轮调用一次，证明"我还活着且在干活"。"""
    _write_heartbeat()
