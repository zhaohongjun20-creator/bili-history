"""产线互斥锁：原子获取 + PID/时间戳 + 心跳 + 过期接管。

旧实现"检查 exists 再 write"存在竞态（两个进程同时通过检查各自写锁），
这里改用 O_CREAT|O_EXCL 原子创建，由操作系统保证唯一持有者。

锁文件内容为 JSON：{pid, host, started_at}，供人工排查残留；
正常路径在 finally 释放，异常退出由 stale_seconds 过期接管兜底。
"""
import json
import os
import socket
import time
from pathlib import Path

STALE_SECONDS = 6 * 3600      # 无 pid 信息的锁按时长判定
HEARTBEAT_STALE = 3600        # 有 pid 但心跳（mtime）超过 1h 未刷新 → 视为挂死


class LockBusy(RuntimeError):
    """另一实例持有锁。"""


def _pid_alive(pid: int) -> bool:
    """进程是否真正存活。Windows 下有两个坑：
    - os.kill(pid, 0) 会真的杀进程，不能用；
    - 已退出的进程在句柄未回收时 OpenProcess 仍能打开，
      必须再查 GetExitCodeProcess != STILL_ACTIVE。"""
    if not pid or pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        h = k.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if h:
            ec = ctypes.c_ulong()
            ok = k.GetExitCodeProcess(h, ctypes.byref(ec))
            k.CloseHandle(h)
            if ok and ec.value != STILL_ACTIVE:
                return False                     # 已退出（任意退出码）
            return True                          # 活着或状态不可判（保守视为活）
        return k.GetLastError() == 5             # ERROR_ACCESS_DENIED：受保护进程=存在
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _lock_pid(text: str) -> int | None:
    """锁文件内容 → pid（JSON 新格式 / 纯数字旧格式 / 解析失败 None）。"""
    try:
        return int(json.loads(text).get("pid", 0))
    except (ValueError, AttributeError):
        pass
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return None


class InstanceLock:
    def __init__(self, path: str | Path, stale_seconds: float = STALE_SECONDS):
        self.path = Path(path)
        self.stale_seconds = stale_seconds
        self._fd = None

    # ── 获取/释放 ──────────────────────────────────────────
    def acquire(self, log=None) -> bool:
        """原子获取；被占用时检查过期，过期则接管。失败返回 False。"""
        for _ in range(3):                      # 接管后允许重试一次创建
            try:
                self._fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                if not self._takeover_stale(log):
                    return False
        else:
            return False
        info = {"pid": os.getpid(), "host": socket.gethostname(),
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        os.write(self._fd, json.dumps(info, ensure_ascii=False).encode("utf-8"))
        os.close(self._fd)
        self._fd = None
        return True

    def release(self):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def heartbeat(self):
        """长任务阶段间刷新 mtime，防止运行中超时被误判死锁。"""
        try:
            os.utime(self.path, None)
        except OSError:
            pass

    def _takeover_stale(self, log) -> bool:
        """判断能否接管已有锁：
        - pid 已退出 → 崩溃残留，立即接管（不限时长）；
        - pid 存活 + 心跳(mtime)未超时 → 真的在跑，绝不接管；
        - pid 存活 + 心跳超 1h 未刷新 → 挂死（阶段最长 30min），接管；
        - 无 pid（损坏/旧格式）→ 退回时长规则（stale_seconds）。"""
        try:
            age = time.time() - self.path.stat().st_mtime
        except OSError:
            return True                          # 恰好被释放：下轮 O_EXCL 争抢
        holder = ""
        try:
            holder = self.path.read_text(encoding="utf-8")[:120]
        except OSError:
            pass
        pid = _lock_pid(holder)
        if pid is not None:
            if _pid_alive(pid):
                if age < HEARTBEAT_STALE:
                    return False                 # 活着且在心跳
                if log:
                    log.warning("锁持有者(pid=%d)存活但心跳 %.1fh 未刷新，判定挂死接管",
                                pid, age / 3600)
            else:
                if log:
                    log.warning("发现崩溃残留锁(pid=%d 已退出, %.1fh)，回收接管",
                                pid, age / 3600)
        else:
            if age < self.stale_seconds:
                return False                     # 无 pid 信息：按时长保守处理
            if log:
                log.warning("发现无主残留锁(%.1fh>%.1fh, %r)，回收接管",
                            age / 3600, self.stale_seconds / 3600, holder[:40])
        try:
            self.path.rename(self.path.with_suffix(".stale"))   # 原子让位
            self.path.with_suffix(".stale").unlink(missing_ok=True)
        except OSError:
            return False
        return True

    # ── 上下文管理：释放必然执行 ──────────────────────────
    def __enter__(self):
        if not self.acquire():
            raise LockBusy(str(self.path))
        return self

    def __exit__(self, *exc):
        self.release()
        return False
