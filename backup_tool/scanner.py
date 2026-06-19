"""文件扫描模块 - 只负责文件遍历和元数据采集

变更判断逻辑已抽至 changeset.py
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .changeset import FileMeta
from .storage import FileRecord, Storage


@dataclass
class ScanResult:
    """扫描结果"""
    source_id: int
    source_path: str
    current_files: list[FileMeta] = field(default_factory=list)
    previous_records: list[FileRecord] = field(default_factory=list)
    base_dir: Path = field(default_factory=Path)

    @property
    def total_files(self) -> int:
        return len(self.current_files)

    @property
    def total_size(self) -> int:
        return sum(f.size for f in self.current_files)


class FileScanner:
    """文件扫描器 - 仅负责遍历文件系统和采集元数据"""

    DEFAULT_IGNORE_DIRS = {
        ".git", ".svn", ".hg", ".backup_data", "__pycache__",
        "node_modules", ".venv", "venv", ".idea", ".vscode",
    }

    def __init__(self, storage: Storage):
        self.storage = storage

    def _iter_files(self, path: Path, ignore_dirs: set[str]) -> list[Path]:
        """遍历目录获取所有文件"""
        files: list[Path] = []
        if path.is_file():
            return [path]

        for root, dirs, filenames in os.walk(path):
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for fname in filenames:
                fpath = Path(root) / fname
                files.append(fpath)
        return files

    def scan_source(self, source_id: int, source_path: str,
                    snapshot_id: int,
                    ignore_dirs: Optional[set[str]] = None) -> ScanResult:
        """扫描单个备份源，采集当前文件元数据和上次快照记录

        仅负责数据采集，变更判断由 changeset.py 处理。
        """
        ignore = ignore_dirs if ignore_dirs is not None else self.DEFAULT_IGNORE_DIRS
        source = Path(source_path)
        base_dir = source if source.is_dir() else source.parent

        all_files = self._iter_files(source, ignore)
        current_files: list[FileMeta] = []

        for fpath in all_files:
            try:
                stat = fpath.stat()
            except (OSError, PermissionError):
                continue

            rel_path = str(fpath.relative_to(base_dir)) if source.is_dir() else fpath.name

            current_files.append(FileMeta(
                abs_path=fpath,
                rel_path=rel_path,
                size=stat.st_size,
                mtime=stat.st_mtime,
            ))

        previous_records = self.storage.get_files_from_prev_snapshot(
            source_id, snapshot_id
        )

        return ScanResult(
            source_id=source_id,
            source_path=source_path,
            current_files=current_files,
            previous_records=previous_records,
            base_dir=base_dir,
        )
