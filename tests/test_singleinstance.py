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
    import subprocess, sys, json as _json, os, time as _t
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    lock_path = tmp_path / ".lock"
    lock_path.write_text(_json.dumps({"pid": p.pid}))
    old = _t.time() - 48 * 3600
    os.utime(lock_path, (old, old))
    lock_path.touch()               # 持有者心跳刷新 mtime（内容不变）
    lock = InstanceLock(lock_path, stale_seconds=6 * 3600)
    try:
        assert not lock.acquire()   # 活着且在心跳：48h 也不接管
    finally:
        p.kill()
        p.wait()


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


def test_release_after_takeover_keeps_new_owner_lock(tmp_path):
    """锁被接管后，旧持有者恢复运行并 release——不得误删新持有者的锁。"""
    import json as _json
    lock_path = tmp_path / ".lock"
    old = InstanceLock(lock_path)
    assert old.acquire()
    old_pid = old._pid
    # 模拟接管者：写入自己的锁内容（接管流程 = 改名旧锁 + O_EXCL 新建）
    import subprocess, sys
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    new_content = _json.dumps({"pid": p.pid, "host": "other"})
    lock_path.write_text(new_content, encoding="utf-8")
    old.release()                   # 旧持有者（实际 pid 已非文件所有者）
    old._pid = old_pid
    assert lock_path.exists()       # 新持有者的锁还在
    assert lock_path.read_text(encoding="utf-8") == new_content
    # 新持有者视角：锁仍有效，无人可再取
    assert not InstanceLock(lock_path).acquire()
    p.kill()
    p.wait()
    # 新持有者释放（内容 pid 是自己）→ 正常删除
    new = InstanceLock(lock_path)
    new.acquire()
    new.release()
    assert not lock_path.exists()


def test_release_when_lock_file_missing(tmp_path):
    lock = InstanceLock(tmp_path / ".lock")
    assert lock.acquire()
    (tmp_path / ".lock").unlink()   # 外部删除
    lock.release()                  # 不抛错


def test_heartbeat_stops_after_ownership_lost(tmp_path):
    import json as _json, subprocess, sys, time as _t
    lock_path = tmp_path / ".lock"
    lock = InstanceLock(lock_path)
    assert lock.acquire()
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3)"])
    lock_path.write_text(_json.dumps({"pid": p.pid}), encoding="utf-8")
    old_mtime = lock_path.stat().st_mtime
    _t.sleep(0.02)
    lock.heartbeat()                # 所有权已丢：不应刷新别人的锁
    assert lock_path.stat().st_mtime == old_mtime
    p.kill()
    p.wait()


def test_takeover_race_single_winner(tmp_path):
    """两进程同时接管同一把过期锁：rename 原子让位，只有一个能新建成功。"""
    import json as _json, subprocess, sys, threading
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()                        # 已退出的 pid → 可接管
    lock_path = tmp_path / ".lock"
    lock_path.write_text(_json.dumps({"pid": p.pid}), encoding="utf-8")
    import os, time as _t
    old = _t.time() - 7 * 3600
    os.utime(lock_path, (old, old))
    winners = []
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        lock = InstanceLock(lock_path)
        if lock.acquire():
            winners.append(lock)

    ts = [threading.Thread(target=worker) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(winners) == 1
    winners[0].release()


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
