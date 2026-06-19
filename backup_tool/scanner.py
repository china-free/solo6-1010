"""文件扫描与增量对比模块"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .storage import FileRecord, Storage


@dataclass
class ScannedFile:
    """扫描到的文件信息"""
    abs_path: Path
    rel_path: str
    size: int
    mtime: float
    sha256: str = ""
    md5: str = ""
    is_new: bool = False
    is_modified: bool = False
    prev_record: Optional[FileRecord] = None


@dataclass
class ScanResult:
    """扫描结果"""
    files: list[ScannedFile] = field(default_factory=list)
    total_files: int = 0
    total_size: int = 0
    changed_files: int = 0


class FileScanner:
    """文件扫描器"""

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
        """扫描单个备份源"""
        ignore = ignore_dirs if ignore_dirs is not None else self.DEFAULT_IGNORE_DIRS
        source = Path(source_path)
        result = ScanResult()

        all_files = self._iter_files(source, ignore)
        base_dir = source if source.is_dir() else source.parent

        for fpath in all_files:
            try:
                stat = fpath.stat()
            except (OSError, PermissionError):
                continue

            rel_path = str(fpath.relative_to(base_dir)) if source.is_dir() else fpath.name
            abs_path_str = str(fpath.resolve())

            scanned = ScannedFile(
                abs_path=fpath,
                rel_path=rel_path,
                size=stat.st_size,
                mtime=stat.st_mtime,
            )

            prev = self.storage.get_file_from_prev_snapshot(
                source_id, rel_path, snapshot_id
            )
            scanned.prev_record = prev

            if prev is None:
                scanned.is_new = True
            else:
                if prev.size != scanned.size or abs(prev.mtime - scanned.mtime) > 1e-6:
                    scanned.is_modified = True

            if scanned.is_new or scanned.is_modified:
                result.changed_files += 1

            result.files.append(scanned)
            result.total_files += 1
            result.total_size += scanned.size

        return result
