"""变更集计算模块

输入：当前文件元数据列表 + 上次快照文件记录
输出：新增、修改、删除三类变更的统一结构
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

from .hashing import sha256_file
from .storage import FileRecord


class ChangeType(str, Enum):
    """变更类型"""
    NEW = "new"
    MODIFIED = "modified"
    DELETED = "deleted"
    UNCHANGED = "unchanged"


@dataclass
class FileMeta:
    """当前文件的元数据（由扫描器采集）"""
    abs_path: Path
    rel_path: str
    size: int
    mtime: float
    sha256: str = ""
    md5: str = ""

    def compute_hashes(self) -> None:
        """计算文件的 SHA256 和 MD5 哈希"""
        self.sha256 = sha256_file(self.abs_path)

    def compute_sha256(self) -> str:
        """只计算 SHA256（用于二次确认）"""
        if not self.sha256:
            self.sha256 = sha256_file(self.abs_path)
        return self.sha256


@dataclass
class ChangeItem:
    """单个变更项"""
    change_type: ChangeType
    rel_path: str
    current: Optional[FileMeta] = None
    previous: Optional[FileRecord] = None


@dataclass
class ChangeSet:
    """完整的变更集"""
    source_id: int
    source_path: str
    new: list[ChangeItem] = field(default_factory=list)
    modified: list[ChangeItem] = field(default_factory=list)
    deleted: list[ChangeItem] = field(default_factory=list)
    unchanged: list[ChangeItem] = field(default_factory=list)

    @property
    def all_items(self) -> list[ChangeItem]:
        """返回所有变更项（含未变化）"""
        return self.new + self.modified + self.deleted + self.unchanged

    @property
    def changed_count(self) -> int:
        """变更的文件总数（新增+修改+删除）"""
        return len(self.new) + len(self.modified) + len(self.deleted)

    @property
    def total_count(self) -> int:
        """文件总数（含未变化）"""
        return len(self.all_items)

    @property
    def total_size(self) -> int:
        """当前存在文件的总大小"""
        return sum(
            item.current.size
            for item in self.all_items
            if item.current is not None
        )


class ChangeSetCalculator:
    """变更集计算器

    负责对比当前扫描到的文件元数据与上次快照的记录，
    产生新增、修改、删除三类变更。
    """

    def calculate(
        self,
        source_id: int,
        source_path: str,
        current_files: list[FileMeta],
        previous_records: list[FileRecord],
    ) -> ChangeSet:
        """计算变更集

        变更判定逻辑：
        - 新增：当前有，上次没有（或上次已删除）
        - 删除：当前没有，上次有且未删除
        - 修改：当前有，上次有且未删除，且内容变化
                 先比较 size + mtime，不一致时再用 SHA256 二次确认
        - 未变化：当前有，上次有且未删除，内容完全一致
        """
        changeset = ChangeSet(source_id=source_id, source_path=source_path)

        prev_map: dict[str, FileRecord] = {
            r.rel_path: r for r in previous_records
        }
        current_map: dict[str, FileMeta] = {
            f.rel_path: f for f in current_files
        }

        for rel_path, curr in current_map.items():
            prev = prev_map.get(rel_path)

            if prev is None or prev.is_deleted:
                changeset.new.append(ChangeItem(
                    change_type=ChangeType.NEW,
                    rel_path=rel_path,
                    current=curr,
                    previous=prev,
                ))
                continue

            if self._is_modified(curr, prev):
                changeset.modified.append(ChangeItem(
                    change_type=ChangeType.MODIFIED,
                    rel_path=rel_path,
                    current=curr,
                    previous=prev,
                ))
            else:
                curr.sha256 = prev.sha256
                curr.md5 = prev.md5
                changeset.unchanged.append(ChangeItem(
                    change_type=ChangeType.UNCHANGED,
                    rel_path=rel_path,
                    current=curr,
                    previous=prev,
                ))

        for rel_path, prev in prev_map.items():
            if rel_path not in current_map and not prev.is_deleted:
                changeset.deleted.append(ChangeItem(
                    change_type=ChangeType.DELETED,
                    rel_path=rel_path,
                    current=None,
                    previous=prev,
                ))

        return changeset

    def _is_modified(self, current: FileMeta, previous: FileRecord) -> bool:
        """判断文件是否被修改

        快速判断：size 或 mtime 变化 → 认为修改
        若 size 和 mtime 都没变 → 计算 SHA256 做二次确认，防止漏检
        """
        if previous.size != current.size:
            return True
        if abs(previous.mtime - current.mtime) > 1e-6:
            return True

        try:
            current_sha = current.compute_sha256()
            return current_sha != previous.sha256
        except (OSError, PermissionError):
            return True
