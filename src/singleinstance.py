"""产线互斥锁：OS 级文件锁——内核保证原子性，进程死亡自动释放。

协议演进（第四轮整改）：放弃"检查过期→改名接管"协议。检查与改名不是原子操作，
接管瞬间持有者复活/第三进程争抢的多进程交错无法用测试穷尽证明。新协议把
互斥性完全交给内核：

- acquire = 打开锁文件 + 对首字节加**非阻塞独占 OS 锁**
  （Windows: msvcrt.locking；POSIX: fcntl.flock）。内核保证跨进程原子；
  **进程死亡时内核自动释放**——崩溃恢复不再需要任何过期/接管/心跳逻辑。
- 进程活着但挂死仍持有锁 → 生产停摆（正确性优先于可用性）。锁内容记录
  pid/主机/启动时间供排查：人工确认挂死后 `taskkill /PID <pid>` 即恢复，
  下一次 acquire 立即成功。
- release = 解锁 + 关闭 + 尽力删除文件。删除失败（后来者正打开）无害：
  文件只是锁的载体，真正的互斥体是 OS 锁，后来者会先取 OS 锁再重写内容。
- 同进程内再加 per-path threading.Lock：不依赖 msvcrt 字节锁的进程内语义，
  线程/进程两层的互斥都显式成立。
"""
import json
import os
import socket
import threading
import time
from pathlib import Path


class LockBusy(RuntimeError):
    """另一实例持有锁。"""


_guards: dict[str, threading.Lock] = {}
_guards_meta = threading.Lock()


def _guard_for(path: Path) -> threading.Lock:
    """同一路径一把进程内互斥（不同路径互不阻塞）。"""
    key = str(path.resolve())
    with _guards_meta:
        return _guards.setdefault(key, threading.Lock())


class InstanceLock:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._guard = _guard_for(self.path)
        self._fd = None
        self._owned = False
        self._pid = 0

    # ── 获取/释放 ──────────────────────────────────────────
    def acquire(self, log=None) -> bool:
        """取锁：OS 字节锁非阻塞独占。成功 True；被持有 False（fail-closed）。"""
        with self._guard:
            if self._owned:
                return True
            for _ in range(3):                   # POSIX inode 校验失败重试
                fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
                try:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    os.close(fd)                 # 别的进程持有：内核裁决，直接退出
                    if log:
                        log.info("另一实例持有锁（%s），本次退出", self.holder_info())
                    return False
                if os.name != "nt" and not self._same_inode(fd):
                    os.close(fd)                 # POSIX：锁到的是被 unlink 的旧 inode
                    continue
                break
            else:
                return False
            self._fd = fd
            self._pid = os.getpid()
            try:
                os.ftruncate(fd, 0)
                os.lseek(fd, 0, 0)
                info = {"pid": self._pid, "host": socket.gethostname(),
                        "started_at": time.strftime("%Y-%m-%d %H:%M:%S")}
                os.write(fd, json.dumps(info, ensure_ascii=False).encode("utf-8"))
            except OSError:
                pass                             # 内容写入失败不影响互斥（锁在内核）
            self._owned = True
            try:                 # 诊断 sidecar：字节锁会拒绝他人读本文件，pid 信息放旁文件
                self._holder_path.write_text(
                    json.dumps({"pid": self._pid, "host": socket.gethostname(),
                                "started_at": time.strftime("%Y-%m-%d %H:%M:%S")},
                               ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
            return True

    def release(self):
        """解锁 + 关闭 + 尽力删文件。删除失败无害（OS 锁才是互斥体）。"""
        with self._guard:
            if not self._owned:
                return
            self._owned = False
            fd, self._fd = self._fd, None
            if fd is None:
                return
            try:
                if os.name == "nt":
                    import msvcrt
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(fd)
            except OSError:
                pass
            try:
                self.path.unlink()
            except OSError:
                pass                             # 被并发打开/已删：无害
            try:
                self._holder_path.unlink(missing_ok=True)
            except OSError:
                pass

    def heartbeat(self):
        """OS 锁下无需心跳——进程存活即锁有效（死亡内核自动释放）。保留接口兼容。"""

    @property
    def _holder_path(self) -> Path:
        return self.path.with_name(self.path.name + ".holder")

    def holder_info(self) -> str:
        """持有者信息（sidecar 优先），供日志/人工排查（挂死处置：taskkill /PID <pid>）。"""
        for p in (self._holder_path, self.path):
            try:
                return p.read_text(encoding="utf-8")[:120]
            except OSError:
                continue
        return "（锁文件不存在）"

    def _same_inode(self, fd) -> bool:
        """POSIX：确认锁到的 inode 仍是 path 指向的文件（防 unlink 竞态双锁）。"""
        try:
            return os.fstat(fd).st_ino == os.stat(self.path).st_ino
        except OSError:
            return False

    # ── 上下文管理：释放必然执行 ──────────────────────────
    def __enter__(self):
        if not self.acquire():
            raise LockBusy(str(self.path))
        return self

    def __exit__(self, *exc):
        self.release()
        return False
