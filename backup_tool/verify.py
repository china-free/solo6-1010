"""完整性校验模块"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from .hashing import hash_file, sha256_file
from .storage import FileRecord, SourceRecord, Storage


@dataclass
class VerifyResult:
    total_files: int = 0
    passed: int = 0
    failed: int = 0
    missing: int = 0
    errors: list[tuple[str, str]] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []

    @property
    def success(self) -> bool:
        return self.failed == 0 and self.missing == 0


@dataclass
class SourceConsistencyResult:
    snapshot_active_files: int = 0
    source_current_files: int = 0
    missing_in_source: int = 0
    new_in_source: int = 0
    modified_in_source: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def success(self) -> bool:
        return self.missing_in_source == 0 and self.new_in_source == 0 and self.modified_in_source == 0


class Verifier:
    """备份完整性校验器"""

    DEFAULT_IGNORE_DIRS = {
        ".git", ".svn", ".hg", ".backup_data", "__pycache__",
        "node_modules", ".venv", "venv", ".idea", ".vscode",
    }

    def __init__(self, backup_root: Path):
        self.backup_root = Path(backup_root)
        self.storage = Storage(self.backup_root)

    def verify_snapshot(
        self,
        snapshot_id: Optional[int] = None,
        algorithm: str = "sha256",
        verbose: bool = False,
    ) -> VerifyResult:
        """校验指定快照或最新快照的 blob 完整性"""
        if not self.storage.is_initialized():
            raise RuntimeError("未找到备份仓库")

        if snapshot_id is None:
            latest = self.storage.get_latest_snapshot()
            if not latest:
                raise RuntimeError("暂无备份快照")
            snapshot_id = latest.id

        snap = self.storage.get_snapshot(snapshot_id)
        if not snap:
            raise ValueError(f"快照 #{snapshot_id} 不存在")

        files = self.storage.get_files_by_snapshot(snapshot_id)
        result = VerifyResult(total_files=len(files))

        time_str = datetime.fromtimestamp(snap.created_at).strftime("%Y-%m-%d %H:%M:%S")
        print(f"🔍 开始校验快照 #{snapshot_id} ({snap.description or '无描述'})")
        print(f"   时间: {time_str}")
        print(f"   算法: {algorithm.upper()}")
        print(f"   文件数: {len(files)}\n")

        for f in files:
            if f.is_deleted:
                result.passed += 1
                if verbose:
                    print(f"  ⏭️  [已删除] {f.rel_path}")
                continue

            stored_path = self.storage.storage_dir / f.stored_path

            if not stored_path.exists():
                result.missing += 1
                result.errors.append((f.rel_path, "blob 文件丢失"))
                if verbose:
                    print(f"  ❌ [丢失] {f.rel_path}")
                continue

            try:
                actual_hash = hash_file(stored_path, algorithm)
                expected_hash = f.sha256 if algorithm.lower() == "sha256" else f.md5

                if actual_hash == expected_hash:
                    result.passed += 1
                    if verbose:
                        print(f"  ✅ [通过] {f.rel_path}")
                else:
                    result.failed += 1
                    result.errors.append(
                        (f.rel_path, f"哈希不匹配: 期望 {expected_hash[:16]}..., 实际 {actual_hash[:16]}...")
                    )
                    print(f"  ❌ [篡改] {f.rel_path}")
            except (OSError, PermissionError) as e:
                result.failed += 1
                result.errors.append((f.rel_path, f"读取错误: {e}"))
                print(f"  ❌ [错误] {f.rel_path}: {e}")

        self._print_summary(result, snapshot_id, algorithm)
        return result

    def verify_source_consistency(
        self,
        snapshot_id: Optional[int] = None,
        verbose: bool = False,
    ) -> SourceConsistencyResult:
        """校验快照文件列表与源目录当前状态的一致性"""
        if not self.storage.is_initialized():
            raise RuntimeError("未找到备份仓库")

        if snapshot_id is None:
            latest = self.storage.get_latest_snapshot()
            if not latest:
                raise RuntimeError("暂无备份快照")
            snapshot_id = latest.id

        snap = self.storage.get_snapshot(snapshot_id)
        if not snap:
            raise ValueError(f"快照 #{snapshot_id} 不存在")

        time_str = datetime.fromtimestamp(snap.created_at).strftime("%Y-%m-%d %H:%M:%S")
        print(f"🔗 开始校验快照 #{snapshot_id} 与源目录的一致性")
        print(f"   快照时间: {time_str}\n")

        result = SourceConsistencyResult()

        snapshot_files = self.storage.get_active_files_by_snapshot(snapshot_id)
        sources = self.storage.list_sources()
        sources_by_id: dict[int, SourceRecord] = {s.id: s for s in sources}

        snapshot_rel_paths_by_source: dict[int, dict[str, FileRecord]] = {}
        for f in snapshot_files:
            snapshot_rel_paths_by_source.setdefault(f.source_id, {})[f.rel_path] = f

        for source_id, snap_files_map in snapshot_rel_paths_by_source.items():
            src_record = sources_by_id.get(source_id)
            if not src_record:
                for rel_path in snap_files_map:
                    result.errors.append((rel_path, "备份源已移除，无法校验"))
                result.missing_in_source += len(snap_files_map)
                result.snapshot_active_files += len(snap_files_map)
                continue

            src_path = Path(src_record.path)
            if not src_path.exists():
                for rel_path in snap_files_map:
                    result.errors.append((rel_path, f"源目录不存在: {src_record.path}"))
                result.missing_in_source += len(snap_files_map)
                result.snapshot_active_files += len(snap_files_map)
                continue

            result.snapshot_active_files += len(snap_files_map)

            source_current: set[str] = set()
            base_dir = src_path if src_path.is_dir() else src_path.parent

            if src_path.is_dir():
                for root, dirs, filenames in os.walk(src_path):
                    dirs[:] = [d for d in dirs if d not in self.DEFAULT_IGNORE_DIRS]
                    for fname in filenames:
                        fpath = Path(root) / fname
                        rel = str(fpath.relative_to(base_dir))
                        source_current.add(rel)
            else:
                if src_path.is_file():
                    source_current.add(src_path.name)

            for rel_path, snap_file in snap_files_map.items():
                if rel_path not in source_current:
                    result.missing_in_source += 1
                    result.errors.append((rel_path, "源目录中已删除"))
                    print(f"  🗑️  [源已删除] {rel_path}")
                else:
                    full_path = base_dir / rel_path
                    try:
                        current_sha = sha256_file(full_path)
                        if current_sha != snap_file.sha256:
                            result.modified_in_source += 1
                            result.errors.append((rel_path, "源文件内容已变化"))
                            print(f"  ✏️  [源已修改] {rel_path}")
                        else:
                            if verbose:
                                print(f"  ✅ [一致] {rel_path}")
                    except (OSError, PermissionError):
                        result.modified_in_source += 1
                        result.errors.append((rel_path, "源文件无法读取"))
                        print(f"  ⚠️  [源无法读取] {rel_path}")

            for rel_path in source_current:
                if rel_path not in snap_files_map:
                    result.new_in_source += 1
                    result.errors.append((rel_path, "源目录中新增（未备份）"))
                    print(f"  📄 [源新增] {rel_path}")

            result.source_current_files += len(source_current)

        self._print_source_summary(result, snapshot_id)
        return result

    def _print_source_summary(self, result: SourceConsistencyResult, snapshot_id: int) -> None:
        print(f"\n📊 源目录一致性校验结果 - 快照 #{snapshot_id}:")
        print(f"   快照活跃文件:   {result.snapshot_active_files}")
        print(f"   源目录当前文件: {result.source_current_files}")
        if result.missing_in_source > 0:
            print(f"   源中已删除:     {result.missing_in_source}")
        if result.new_in_source > 0:
            print(f"   源中新增:       {result.new_in_source}")
        if result.modified_in_source > 0:
            print(f"   源中已修改:     {result.modified_in_source}")
        print()

        if result.success:
            print(f"✅ 快照文件列表与源目录当前状态完全一致！")
        else:
            total_issues = result.missing_in_source + result.new_in_source + result.modified_in_source
            print(f"⚠️  发现 {total_issues} 处不一致：")
            for path, reason in result.errors[:20]:
                print(f"   - {path}: {reason}")
            if len(result.errors) > 20:
                print(f"   ... 还有 {len(result.errors) - 20} 项")

    def _print_summary(self, result: VerifyResult, snapshot_id: int, algorithm: str) -> None:
        print(f"\n📊 校验结果 - 快照 #{snapshot_id}:")
        print(f"   总文件数:   {result.total_files}")
        print(f"   通过:       {result.passed}")
        if result.failed > 0:
            print(f"   哈希不匹配: {result.failed}")
        if result.missing > 0:
            print(f"   文件丢失:   {result.missing}")
        print()

        if result.success:
            print(f"✅ 所有文件通过 {algorithm.upper()} 完整性校验，备份完好无损！")
        else:
            print(f"⚠️  发现问题（{result.failed + result.missing} 个文件异常）：")
            for path, reason in result.errors[:20]:
                print(f"   - {path}: {reason}")
            if len(result.errors) > 20:
                print(f"   ... 还有 {len(result.errors) - 20} 个错误")

    def verify_all(self, algorithm: str = "sha256") -> dict[int, VerifyResult]:
        """校验所有快照"""
        if not self.storage.is_initialized():
            raise RuntimeError("未找到备份仓库")

        snapshots = self.storage.list_snapshots()
        if not snapshots:
            raise RuntimeError("暂无备份快照")

        results: dict[int, VerifyResult] = {}
        all_ok = True
        for snap in snapshots:
            print(f"\n{'='*60}")
            result = self.verify_snapshot(snap.id, algorithm, verbose=False)
            results[snap.id] = result
            if not result.success:
                all_ok = False

        print(f"\n{'='*60}")
        total = sum(r.total_files for r in results.values())
        passed = sum(r.passed for r in results.values())
        failed = sum(r.failed for r in results.values())
        missing = sum(r.missing for r in results.values())

        print(f"📋 全量校验汇总:")
        print(f"   快照数:     {len(snapshots)}")
        print(f"   总文件数:   {total}")
        print(f"   通过:       {passed}")
        print(f"   哈希不匹配: {failed}")
        print(f"   文件丢失:   {missing}")

        if all_ok:
            print(f"\n✅ 所有 {len(snapshots)} 个快照均通过 {algorithm.upper()} 校验！")
        else:
            bad_snaps = [sid for sid, r in results.items() if not r.success]
            print(f"\n⚠️  以下快照存在问题: {', '.join(f'#{s}' for s in bad_snaps)}")

        return results
