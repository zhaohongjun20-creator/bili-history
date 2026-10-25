"""锁竞争子进程 worker：argv[1]=锁路径 argv[2]=持锁秒数；stdout 输出 WON/BUSY。"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.singleinstance import InstanceLock

lock = InstanceLock(sys.argv[1])
if lock.acquire():
    try:
        time.sleep(float(sys.argv[2]) if len(sys.argv) > 2 else 0.5)
    finally:
        lock.release()
    print("WON")
else:
    print("BUSY")
