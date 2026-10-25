"""互斥锁测试：并发只有一个赢家、上下文释放、过期接管（方案 P1-2）。"""
import threading

import pytest

from src.singleinstance import InstanceLock, LockBusy


def test_acquire_release(tmp_path):
    lock = InstanceLock(tmp_path / ".lock")
    assert lock.acquire()
    assert lock.path.exists()
    assert not InstanceLock(tmp_path / ".lock").acquire()
    lock.release()
    assert not lock.path.exists()
    assert InstanceLock(tmp_path / ".lock").acquire()


def test_concurrent_acquire_single_winner(tmp_path):
    """并发启动 10 次：同一时刻只有 1 个能领取（方案 P1-2 验收）。"""
    lock_path = tmp_path / ".lock"
    winners = []
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        lock = InstanceLock(lock_path)
        if lock.acquire():
            winners.append(lock)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(winners) == 1
    winners[0].release()


def test_stale_lock_takeover(tmp_path):
    import os, time
    lock_path = tmp_path / ".lock"
    lock_path.write_text('{"pid": 123456}')
    old = time.time() - 7 * 3600
    os.utime(lock_path, (old, old))
    lock = InstanceLock(lock_path, stale_seconds=6 * 3600)
    assert lock.acquire()          # 残留锁被回收接管
    lock.release()


def test_alive_pid_lock_not_taken(tmp_path):
    """锁内 pid 存活 + 心跳新鲜 → 无论锁多老都不接管（长任务不靠时长猜测）。"""
    import os, time
    lock_path = tmp_path / ".lock"
    lock_path.write_text('{"pid": %d}' % os.getpid())
    old = time.time() - 48 * 3600
    os.utime(lock_path, (old, old))
    lock = InstanceLock(lock_path, stale_seconds=6 * 3600)
    lock.heartbeat()               # 模拟持有者仍在心跳
    assert not lock.acquire()      # 活着且在心跳：48h 也不接管


def test_alive_but_hung_lock_taken(tmp_path):
    """pid 存活但心跳超 1h 未刷新 → 判定挂死，接管。"""
    import os, time
    lock_path = tmp_path / ".lock"
    lock_path.write_text('{"pid": %d}' % os.getpid())
    old = time.time() - 2 * 3600   # 2h 无心跳（阶段最长 30min）
    os.utime(lock_path, (old, old))
    lock = InstanceLock(lock_path, stale_seconds=6 * 3600)
    assert lock.acquire()
    lock.release()


def test_dead_pid_takeover_even_fresh(tmp_path):
    """锁内 pid 已退出 → 崩溃残留，即使刚写的锁也立即接管。"""
    import subprocess, sys, json
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    lock_path = tmp_path / ".lock"
    lock_path.write_text(json.dumps({"pid": p.pid}))
    lock = InstanceLock(lock_path, stale_seconds=6 * 3600)
    assert lock.acquire()          # 进程已死：不限时长接管
    lock.release()


def test_fresh_lock_not_taken(tmp_path):
    """无 pid 信息（损坏/空锁）+ 未过期：保守不接管。"""
    lock_path = tmp_path / ".lock"
    lock_path.write_text("???")
    lock = InstanceLock(lock_path, stale_seconds=6 * 3600)
    assert not lock.acquire()      # 未过期：不接管


def test_context_manager_releases_on_exception(tmp_path):
    lock_path = tmp_path / ".lock"
    with pytest.raises(RuntimeError):
        with InstanceLock(lock_path):
            raise RuntimeError("boom")
    assert not lock_path.exists()
    assert InstanceLock(lock_path).acquire()


def test_context_manager_raises_when_busy(tmp_path):
    lock_path = tmp_path / ".lock"
    holder = InstanceLock(lock_path)
    assert holder.acquire()
    with pytest.raises(LockBusy):
        with InstanceLock(lock_path):
            pass
    holder.release()
