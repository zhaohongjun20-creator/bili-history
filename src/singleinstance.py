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
        self._owned = False       # 是否仍持有所有权（被接管后置 False，释放不再删文件）
        self._pid = 0

    # ── 获取/释放 ──────────────────────────────────────────
    def acquire(self, log=None) -> bool:
        """原子获取；被占用时检查过期，过期则接管。失败返回 False。"""
        for _ in range(3):                      # 接管后允许重试一次创建
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except FileExistsError:
                if not self._takeover_stale(log):
                    return False
        else:
            return False
        self._pid = os.getpid()
        info = {"pid": self._pid, "host": socket.gethostname(),
                "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
        os.write(fd, json.dumps(info, ensure_ascii=False).encode("utf-8"))
        os.close(fd)
        self._owned = True
        return True

    def _is_mine(self) -> bool:
        """锁文件是否仍归本进程所有（防接管后旧进程误删新持有者的锁）。"""
        try:
            content = self.path.read_text(encoding="utf-8")
        except OSError:
            return False                         # 文件没了：所有权自然消失
        pid = _lock_pid(content)
        return pid is None or pid == self._pid   # 解析失败视为自己写的（写入是原子的）

    def release(self):
        """释放锁。仅在仍持有时删除；锁已被他人接管/重建时只放弃所有权，
        绝不删除别人的锁文件（方案 P1：接管后旧持有者延迟释放的竞态）。"""
        if not self._owned:
            return
        if not self._is_mine():
            self._owned = False                  # 已被接管：放弃，不动文件
            return
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass
        self._owned = False

    def heartbeat(self):
        """长任务阶段间刷新 mtime，防止运行中被判挂死接管。
        所有权丢失（被接管）后停止刷新，不再碰别人的锁。"""
        if not self._owned:
            return
        if not self._is_mine():
            self._owned = False                  # 已被接管：放弃所有权
            return
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
        # TOCTOU 收口：接管判定与改名之间若持有者心跳刷新过，放弃接管
        try:
            if time.time() - self.path.stat().st_mtime > age + 0.05:
                return False
            self.path.rename(self.path.with_suffix(".stale"))   # 原子让位
        except OSError:
            return False                         # 已被并发接管/释放
        self.path.with_suffix(".stale").unlink(missing_ok=True)
        return True

    # ── 上下文管理：释放必然执行 ──────────────────────────
    def __enter__(self):
        if not self.acquire():
            raise LockBusy(str(self.path))
        return self

    def __exit__(self, *exc):
        self.release()
        return False
