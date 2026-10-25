"""互斥锁测试（OS 级字节锁协议）：内核原子性、死亡自动释放、多进程竞争。

与旧协议测试的差异：不再有过期接管/心跳/PID 存活判定——进程死亡由内核
自动释放；挂死进程阻塞生产（正确性优先），靠锁内容里的 pid 人工 taskkill。
"""
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from src.singleinstance import InstanceLock, LockBusy

WORKER = Path(__file__).parent / "lock_worker.py"


def _spawn_worker(lock_path, hold=0.5):
    return subprocess.Popen(
        [sys.executable, str(WORKER), str(lock_path), str(hold)],
        stdout=subprocess.PIPE, text=True)


def test_acquire_release(tmp_path):
    lock = InstanceLock(tmp_path / ".lock")
    assert lock.acquire()
    assert not InstanceLock(tmp_path / ".lock").acquire()   # 持有期间他人不可取
    lock.release()
    assert InstanceLock(tmp_path / ".lock").acquire()       # 释放后可取


def test_concurrent_threads_single_winner(tmp_path):
    """同进程 10 线程并发：只有 1 个赢家。"""
    lock_path = tmp_path / ".lock"
    winners = []
    barrier = threading.Barrier(10)

    def worker():
        barrier.wait()
        lock = InstanceLock(lock_path)
        if lock.acquire():
            winners.append(lock)

    ts = [threading.Thread(target=worker) for _ in range(10)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(winners) == 1
    winners[0].release()


def test_concurrent_processes_single_winner(tmp_path):
    """真·多进程并发（8 个独立解释器）：只有 1 个 WON。"""
    lock_path = tmp_path / ".lock"
    ps = [_spawn_worker(lock_path, hold=0.3) for _ in range(8)]
    outs = []
    for p in ps:
        out, _ = p.communicate(timeout=60)
        outs.append(out.strip())
    assert outs.count("WON") == 1, outs
    assert outs.count("BUSY") == 7, outs


def test_holder_death_releases_immediately(tmp_path):
    """持有者进程被杀 → 内核自动释放，另一进程立即可取（无过期等待）。"""
    lock_path = tmp_path / ".lock"
    child = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys, time; sys.path.insert(0, r'{Path(__file__).parents[1]}');"
         f"from src.singleinstance import InstanceLock;"
         f"l = InstanceLock(r'{lock_path}'); l.acquire(); time.sleep(60)"])
    time.sleep(2)                     # 等子进程取到锁
    assert not InstanceLock(lock_path).acquire()            # 被持有
    child.kill()
    child.wait()
    t0 = time.time()
    parent = InstanceLock(lock_path)
    assert parent.acquire()         # 死亡即释放，无需任何过期判断
    assert time.time() - t0 < 5
    parent.release()


def test_hung_holder_blocks_production(tmp_path):
    """挂死进程（活着但不工作）阻塞生产——正确性优先于可用性，
    即使 mtime 很旧也不被接管；处置：按锁内容 taskkill。"""
    lock_path = tmp_path / ".lock"
    import os
    child = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys, time; sys.path.insert(0, r'{Path(__file__).parents[1]}');"
         f"from src.singleinstance import InstanceLock;"
         f"l = InstanceLock(r'{lock_path}'); l.acquire(); time.sleep(30)"])
    time.sleep(2)
    old = time.time() - 48 * 3600
    os.utime(lock_path, (old, old))   # 心跳/时长不再是接管依据
    try:
        assert not InstanceLock(lock_path).acquire()
        info = InstanceLock(lock_path).holder_info()
        assert f'"pid": {child.pid}' in info    # sidecar 可读，供人工 taskkill
    finally:
        child.kill()
        child.wait()
    assert InstanceLock(lock_path).acquire()    # 杀掉后立即可取
    InstanceLock(lock_path).release()


def test_leftover_content_rewritten_by_next_owner(tmp_path):
    """上一任残留内容（含死 pid）不妨碍获取；新持有者 sidecar 记录自己，释放后清理。"""
    lock_path = tmp_path / ".lock"
    lock_path.write_text('{"pid": 999999999, "host": "dead"}')
    lock = InstanceLock(lock_path)
    assert lock.acquire()
    info = json.loads(Path(str(lock_path) + ".holder").read_text(encoding="utf-8"))
    assert info["pid"] == lock._pid
    lock.release()
    assert not Path(str(lock_path) + ".holder").exists()


def test_corrupt_leftover_still_acquirable(tmp_path):
    """残留垃圾内容不阻碍获取（内容只是诊断信息，互斥在内核）。"""
    lock_path = tmp_path / ".lock"
    lock_path.write_bytes(b"corrupt leftover bytes")
    lock = InstanceLock(lock_path)
    assert lock.acquire()
    lock.release()


def test_release_twice_idempotent(tmp_path):
    lock_path = tmp_path / ".lock"
    lock = InstanceLock(lock_path)
    assert lock.acquire()
    lock.release()
    lock.release()                    # 幂等，不抛错
    lock_path.unlink(missing_ok=True)
    lock.release()                    # 文件已删后再释放：不抛错


def test_context_manager_releases_on_exception(tmp_path):
    lock_path = tmp_path / ".lock"
    with pytest.raises(RuntimeError):
        with InstanceLock(lock_path):
            raise RuntimeError("boom")
    assert InstanceLock(lock_path).acquire()


def test_context_manager_raises_when_busy(tmp_path):
    lock_path = tmp_path / ".lock"
    holder = InstanceLock(lock_path)
    assert holder.acquire()
    with pytest.raises(LockBusy):
        with InstanceLock(lock_path):
            pass
    holder.release()
