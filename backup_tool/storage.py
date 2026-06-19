"""SQLite 元数据存储模块"""

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Optional

BACKUP_DIR_NAME = ".backup_data"
DB_FILENAME = "metadata.db"
STORAGE_DIR_NAME = "storage"


@dataclass
class SourceRecord:
    id: int
    path: str
    type: str
    created_at: float


@dataclass
class SnapshotRecord:
    id: int
    created_at: float
    description: str
    total_files: int
    total_size: int
    changed_files: int


@dataclass
class FileRecord:
    id: int
    snapshot_id: int
    source_id: int
    rel_path: str
    abs_path: str
    size: int
    mtime: float
    md5: str
    sha256: str
    stored_path: str
    is_new: bool
    is_modified: bool


class Storage:
    """元数据存储管理器"""

    def __init__(self, backup_root: Path):
        self.backup_root = Path(backup_root)
        self.meta_dir = self.backup_root / BACKUP_DIR_NAME
        self.db_path = self.meta_dir / DB_FILENAME
        self.storage_dir = self.meta_dir / STORAGE_DIR_NAME

    @classmethod
    def find_backup_root(cls, start_path: Path) -> Optional[Path]:
        """从 start_path 向上查找备份仓库根目录"""
        current = Path(start_path).resolve()
        while True:
            if (current / BACKUP_DIR_NAME).is_dir():
                return current
            parent = current.parent
            if parent == current:
                return None
            current = parent

    def is_initialized(self) -> bool:
        """检查是否已初始化"""
        return self.db_path.exists()

    def initialize(self) -> None:
        """初始化备份仓库"""
        self.meta_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            self._create_tables(conn)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _create_tables(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                type TEXT NOT NULL CHECK(type IN ('file', 'directory')),
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at REAL NOT NULL,
                description TEXT DEFAULT '',
                total_files INTEGER NOT NULL DEFAULT 0,
                total_size INTEGER NOT NULL DEFAULT 0,
                changed_files INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id INTEGER NOT NULL,
                source_id INTEGER NOT NULL,
                rel_path TEXT NOT NULL,
                abs_path TEXT NOT NULL,
                size INTEGER NOT NULL,
                mtime REAL NOT NULL,
                md5 TEXT DEFAULT '',
                sha256 TEXT DEFAULT '',
                stored_path TEXT NOT NULL,
                is_new INTEGER NOT NULL DEFAULT 0,
                is_modified INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY (snapshot_id) REFERENCES snapshots(id),
                FOREIGN KEY (source_id) REFERENCES sources(id)
            );

            CREATE INDEX IF NOT EXISTS idx_files_snapshot ON files(snapshot_id);
            CREATE INDEX IF NOT EXISTS idx_files_sha256 ON files(sha256);
            CREATE INDEX IF NOT EXISTS idx_snapshots_created ON snapshots(created_at);
            """
        )

    # ---------- Source 相关 ----------

    def add_source(self, path: str, source_type: str) -> int:
        """添加备份源，返回 source id"""
        path = str(Path(path).resolve())
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT id FROM sources WHERE path = ?", (path,)
            )
            row = cur.fetchone()
            if row:
                return row["id"]
            cur = conn.execute(
                "INSERT INTO sources (path, type, created_at) VALUES (?, ?, ?)",
                (path, source_type, time.time()),
            )
            return cur.lastrowid

    def list_sources(self) -> list[SourceRecord]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM sources ORDER BY created_at").fetchall()
            return [SourceRecord(**dict(r)) for r in rows]

    # ---------- Snapshot 相关 ----------

    def create_snapshot(self, description: str = "") -> int:
        """创建新快照，返回 snapshot id"""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO snapshots (created_at, description) VALUES (?, ?)",
                (time.time(), description),
            )
            return cur.lastrowid

    def update_snapshot_stats(
        self, snapshot_id: int, total_files: int, total_size: int, changed_files: int
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE snapshots SET total_files=?, total_size=?, changed_files=? WHERE id=?",
                (total_files, total_size, changed_files, snapshot_id),
            )

    def get_snapshot(self, snapshot_id: int) -> Optional[SnapshotRecord]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)
            ).fetchone()
            return SnapshotRecord(**dict(row)) if row else None

    def list_snapshots(
        self,
        start_time: Optional[float] = None,
        end_time: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> list[SnapshotRecord]:
        """查询快照，可选按时间范围过滤"""
        sql = "SELECT * FROM snapshots WHERE 1=1"
        params: list[Any] = []
        if start_time is not None:
            sql += " AND created_at >= ?"
            params.append(start_time)
        if end_time is not None:
            sql += " AND created_at <= ?"
            params.append(end_time)
        sql += " ORDER BY created_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [SnapshotRecord(**dict(r)) for r in rows]

    def get_latest_snapshot(self) -> Optional[SnapshotRecord]:
        snaps = self.list_snapshots(limit=1)
        return snaps[0] if snaps else None

    # ---------- File 相关 ----------

    def add_file(self, snapshot_id: int, source_id: int, rel_path: str,
                 abs_path: str, size: int, mtime: float, md5: str, sha256: str,
                 stored_path: str, is_new: bool, is_modified: bool) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT INTO files
                   (snapshot_id, source_id, rel_path, abs_path, size, mtime,
                    md5, sha256, stored_path, is_new, is_modified)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, source_id, rel_path, abs_path, size, mtime,
                 md5, sha256, stored_path, int(is_new), int(is_modified)),
            )
            return cur.lastrowid

    def get_files_by_snapshot(self, snapshot_id: int) -> list[FileRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM files WHERE snapshot_id = ? ORDER BY rel_path",
                (snapshot_id,),
            ).fetchall()
            records: list[FileRecord] = []
            for r in rows:
                d = dict(r)
                d["is_new"] = bool(d["is_new"])
                d["is_modified"] = bool(d["is_modified"])
                records.append(FileRecord(**d))
            return records

    def find_file_by_sha256(self, sha256: str) -> Optional[FileRecord]:
        """按哈希查找已存储的相同文件（用于去重）"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM files WHERE sha256 = ? LIMIT 1", (sha256,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["is_new"] = bool(d["is_new"])
            d["is_modified"] = bool(d["is_modified"])
            return FileRecord(**d)

    def get_file_from_prev_snapshot(self, source_id: int, rel_path: str,
                                    current_snapshot_id: int) -> Optional[FileRecord]:
        """查找上一个快照中相同路径的文件"""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT f.* FROM files f
                   JOIN snapshots s ON f.snapshot_id = s.id
                   WHERE f.source_id = ? AND f.rel_path = ? AND s.id < ?
                   ORDER BY s.created_at DESC LIMIT 1""",
                (source_id, rel_path, current_snapshot_id),
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["is_new"] = bool(d["is_new"])
            d["is_modified"] = bool(d["is_modified"])
            return FileRecord(**d)
