"""核心备份逻辑"""

import shutil
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from .changeset import ChangeItem, ChangeSet, ChangeSetCalculator, ChangeType
from .hashing import md5_file, sha256_file
from .scanner import FileScanner, ScanResult
from .storage import FileRecord, Storage


@dataclass
class BackupResult:
    snapshot_id: int
    created_at: float
    total_files: int
    total_size: int
    changed_files: int
    copied_files: int
    skipped_files: int
    deleted_files: int


class BackupManager:
    """备份管理器"""

    def __init__(self, backup_root: Path):
        self.backup_root = Path(backup_root)
        self.storage = Storage(self.backup_root)
        self.scanner = FileScanner(self.storage)
        self.changeset_calc = ChangeSetCalculator()

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
        deleted_count = 0

        for src in sources:
            src_path = Path(src.path)
            if not src_path.exists():
                print(f"  ⚠️  备份源不存在，跳过: {src.path}")
                continue

            scan_result: ScanResult = self.scanner.scan_source(
                src.id, src.path, snapshot_id
            )

            changeset: ChangeSet = self.changeset_calc.calculate(
                source_id=src.id,
                source_path=src.path,
                current_files=scan_result.current_files,
                previous_records=scan_result.previous_records,
            )

            total_files += changeset.total_count
            total_size += changeset.total_size
            changed_files += changeset.changed_count
            deleted_count += len(changeset.deleted)

            self._print_changeset_summary(src.path, changeset)

            for item in changeset.all_items:
                if item.change_type in (ChangeType.NEW, ChangeType.MODIFIED):
                    self._store_changed_file(snapshot_id, src.id, item)
                    if item.change_type == ChangeType.NEW:
                        print(f"    ✅ [新增] {item.rel_path}")
                    else:
                        print(f"    ✅ [修改] {item.rel_path}")
                    copied_files += 1

                elif item.change_type == ChangeType.UNCHANGED:
                    self._record_unchanged_file(snapshot_id, src.id, item)
                    print(f"    ⏭️  未变化: {item.rel_path}")
                    skipped_files += 1

                elif item.change_type == ChangeType.DELETED:
                    self._record_deleted_file(snapshot_id, src.id, item)
                    print(f"    🗑️  [删除] {item.rel_path}")

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
            deleted_files=deleted_count,
        )

        self._print_backup_summary(result)
        return result

    def _print_changeset_summary(self, src_path: str, changeset: ChangeSet) -> None:
        parts = [
            f"{changeset.total_count} 个文件",
            f"{changeset.changed_count} 个变化",
        ]
        if changeset.new:
            parts.append(f"{len(changeset.new)} 个新增")
        if changeset.modified:
            parts.append(f"{len(changeset.modified)} 个修改")
        if changeset.deleted:
            parts.append(f"{len(changeset.deleted)} 个删除")
        print(f"  📂 扫描 {src_path}: {', '.join(parts)}")

    def _store_changed_file(self, snapshot_id: int, source_id: int, item: ChangeItem) -> None:
        """统一的存储写入流程：新增或修改走这个流程"""
        assert item.current is not None

        try:
            sha256 = sha256_file(item.current.abs_path)
            md5 = md5_file(item.current.abs_path)
            item.current.sha256 = sha256
            item.current.md5 = md5
            dup = self.storage.find_file_by_sha256(sha256)
            if dup and (Path(self.storage.storage_dir / dup.stored_path)).exists():
                stored_path = dup.stored_path
                print(f"    📋 去重跳过 (SHA256相同): {item.rel_path}")
            else:
                stored_path = self._hash_and_store(item.current.abs_path, sha256, md5)

        except (OSError, PermissionError) as e:
            raise RuntimeError(f"无法备份 {item.rel_path}: {e}")

        abs_path_str = str(item.current.abs_path.resolve()) if item.current.abs_path.exists() else item.current.abs_path

        self.storage.add_file(
            snapshot_id=snapshot_id,
            source_id=source_id,
            rel_path=item.rel_path,
            abs_path=abs_path_str,
            size=item.current.size,
            mtime=item.current.mtime,
            md5=md5,
            sha256=sha256,
            stored_path=stored_path,
            is_new=(item.change_type == ChangeType.NEW),
            is_modified=(item.change_type == ChangeType.MODIFIED),
            is_deleted=False,
        )

    def _record_unchanged_file(self, snapshot_id: int, source_id: int, item: ChangeItem) -> None:
        """记录未变化的文件，复用之前的存储路径"""
        assert item.current is not None
        assert item.previous is not None

        self.storage.add_file(
            snapshot_id=snapshot_id,
            source_id=source_id,
            rel_path=item.rel_path,
            abs_path=str(item.current.abs_path.resolve()),
            size=item.current.size,
            mtime=item.current.mtime,
            md5=item.current.md5,
            sha256=item.current.sha256,
            stored_path=item.previous.stored_path,
            is_new=False,
            is_modified=False,
            is_deleted=False,
        )

    def _record_deleted_file(self, snapshot_id: int, source_id: int, item: ChangeItem) -> None:
        """记录已删除的文件"""
        assert item.previous is not None

        self.storage.add_file(
            snapshot_id=snapshot_id,
            source_id=source_id,
            rel_path=item.rel_path,
            abs_path=item.previous.abs_path,
            size=item.previous.size,
            mtime=item.previous.mtime,
            md5=item.previous.md5,
            sha256=item.previous.sha256,
            stored_path=item.previous.stored_path,
            is_new=False,
            is_modified=False,
            is_deleted=True,
        )

    def _print_backup_summary(self, result: BackupResult) -> None:
        print(f"\n🎉 快照 #{result.snapshot_id} 备份完成:")
        print(f"   总文件数: {result.total_files}  ({self._format_size(result.total_size)})")
        print(f"   变化文件: {result.changed_files}")
        print(f"   实际拷贝: {result.copied_files}")
        print(f"   去重跳过: {result.skipped_files}")
        if result.deleted_files > 0:
            print(f"   已删除:   {result.deleted_files}")
        print(f"   时间: {datetime.fromtimestamp(result.created_at).strftime('%Y-%m-%d %H:%M:%S')}")

    @staticmethod
    def _format_size(size: int) -> str:
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} PB"
