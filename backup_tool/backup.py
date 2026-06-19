"""核心备份逻辑"""

import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .hashing import md5_file, sha256_file
from .scanner import FileScanner, ScanResult, ScannedFile
from .storage import Storage


@dataclass
class BackupResult:
    snapshot_id: int
    created_at: float
    total_files: int
    total_size: int
    changed_files: int
    copied_files: int
    skipped_files: int


class BackupManager:
    """备份管理器"""

    def __init__(self, backup_root: Path):
        self.backup_root = Path(backup_root)
        self.storage = Storage(self.backup_root)
        self.scanner = FileScanner(self.storage)

    def init(self) -> bool:
        """初始化备份仓库"""
        if self.storage.is_initialized():
            print(f"错误: 备份仓库已存在于 {self.backup_root}", file=sys.stderr)
            return False
        self.storage.initialize()
        print(f"✅ 已在 {self.backup_root} 初始化备份仓库")
        return True

    def _hash_and_store(self, src: Path, sha256: str, md5: str) -> str:
        """计算文件哈希并存储到备份目录，返回存储的相对路径"""
        rel_store_path = f"blobs/{sha256[:2]}/{sha256[2:4]}/{sha256}"
        dest_path = self.storage.storage_dir / rel_store_path

        if dest_path.exists():
            existing_sha = sha256_file(dest_path)
            if existing_sha == sha256:
                return rel_store_path

        dest_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest_path)
        return rel_store_path

    def backup(self, description: str = "") -> Optional[BackupResult]:
        """执行增量备份"""
        if not self.storage.is_initialized():
            print("错误: 未找到备份仓库，请先运行 init 命令", file=sys.stderr)
            return None

        sources = self.storage.list_sources()
        if not sources:
            print("错误: 未添加任何备份源，请先添加要备份的目录或文件", file=sys.stderr)
            return None

        snapshot_id = self.storage.create_snapshot(description)
        print(f"📦 创建快照 #{snapshot_id} ({description or '无描述'})")

        total_files = 0
        total_size = 0
        changed_files = 0
        copied_files = 0
        skipped_files = 0

        for src in sources:
            src_path = Path(src.path)
            if not src_path.exists():
                print(f"  ⚠️  备份源不存在，跳过: {src.path}")
                continue

            scan_result: ScanResult = self.scanner.scan_source(
                src.id, src.path, snapshot_id
            )
            total_files += scan_result.total_files
            total_size += scan_result.total_size
            changed_files += scan_result.changed_files

            print(f"  📂 扫描 {src.path}: {scan_result.total_files} 个文件, "
                  f"{scan_result.changed_files} 个变化")

            for sf in scan_result.files:
                stored_path = ""

                if sf.is_new or sf.is_modified:
                    try:
                        sha256 = sha256_file(sf.abs_path)
                        md5 = md5_file(sf.abs_path)
                        sf.sha256 = sha256
                        sf.md5 = md5

                        dup = self.storage.find_file_by_sha256(sha256)
                        if dup and (Path(self.storage.storage_dir / dup.stored_path)).exists():
                            stored_path = dup.stored_path
                            skipped_files += 1
                            print(f"    📋 去重跳过 (SHA256相同): {sf.rel_path}")
                        else:
                            stored_path = self._hash_and_store(sf.abs_path, sha256, md5)
                            copied_files += 1
                            change_type = "新增" if sf.is_new else "修改"
                            print(f"    ✅ [{change_type}] {sf.rel_path} "
                                  f"({self._format_size(sf.size)})")
                    except (OSError, PermissionError) as e:
                        print(f"    ❌ 无法备份 {sf.rel_path}: {e}", file=sys.stderr)
                        continue
                else:
                    if sf.prev_record:
                        stored_path = sf.prev_record.stored_path
                        sf.sha256 = sf.prev_record.sha256
                        sf.md5 = sf.prev_record.md5
                        skipped_files += 1
                        print(f"    ⏭️  未变化: {sf.rel_path}")

                if stored_path:
                    self.storage.add_file(
                        snapshot_id=snapshot_id,
                        source_id=src.id,
                        rel_path=sf.rel_path,
                        abs_path=str(sf.abs_path.resolve()),
                        size=sf.size,
                        mtime=sf.mtime,
                        md5=sf.md5,
                        sha256=sf.sha256,
                        stored_path=stored_path,
                        is_new=sf.is_new,
                        is_modified=sf.is_modified,
                    )

        self.storage.update_snapshot_stats(
            snapshot_id, total_files, total_size, changed_files
        )

        result = BackupResult(
            snapshot_id=snapshot_id,
            created_at=time.time(),
            total_files=total_files,
            total_size=total_size,
            changed_files=changed_files,
            copied_files=copied_files,
            skipped_files=skipped_files,
        )

        print(f"\n🎉 快照 #{snapshot_id} 备份完成:")
        print(f"   总文件数: {total_files}  ({self._format_size(total_size)})")
        print(f"   变化文件: {changed_files}")
        print(f"   实际拷贝: {copied_files}")
        print(f"   去重跳过: {skipped_files}")
        print(f"   时间: {datetime.fromtimestamp(result.created_at).strftime('%Y-%m-%d %H:%M:%S')}")
        return result

    @staticmethod
    def _format_size(size: int) -> str:
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"
