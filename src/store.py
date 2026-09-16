"""主题级状态存储：记录每期主题是否已做，防止重复。"""
import sqlite3, os, datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    topic       TEXT PRIMARY KEY,
    title       TEXT DEFAULT '',
    status      TEXT DEFAULT 'dryrun',   -- dryrun|published|failed:...
    created_at  TEXT DEFAULT (datetime('now','localtime'))
);
"""

class Store:
    def __init__(self, db_path: str):
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(_SCHEMA)

    def is_topic_done(self, topic: str) -> bool:
        row = self.conn.execute(
            "SELECT status FROM topics WHERE topic=?", (topic,)).fetchone()
        return bool(row and row[0] == "published")

    def mark_topic(self, topic: str, title: str, status: str):
        self.conn.execute(
            "INSERT INTO topics (topic, title, status, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT(topic) DO UPDATE SET title=excluded.title, status=excluded.status",
            (topic, title, status,
             datetime.datetime.now().isoformat(timespec="seconds")))
        self.conn.commit()
