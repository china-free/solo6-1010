"""备份历史查询模块"""

from datetime import datetime
from pathlib import Path
from typing import Optional

from .backup import BackupManager
from .storage import FileRecord, SnapshotRecord, Storage


class HistoryManager:
    """备份历史管理器"""

    def __init__(self, backup_root: Path):
        self.backup_root = Path(backup_root)
        self.storage = Storage(self.backup_root)

    def list_snapshots(
        self,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        limit: Optional[int] = None,
        verbose: bool = False,
    ) -> int:
        """列出快照，支持时间范围过滤"""
        if not self.storage.is_initialized():
            print("错误: 未找到备份仓库")
            return 1

        start_ts = self._parse_time(start_time) if start_time else None
        end_ts = self._parse_time(end_time) if end_time else None

        snapshots = self.storage.list_snapshots(start_ts, end_ts, limit)

        if not snapshots:
            print("暂无备份快照")
            return 0

        print(f"共找到 {len(snapshots)} 个备份快照:\n")
        print(f"{'ID':<5} {'时间':<20} {'描述':<20} {'文件数':<10} {'大小':<12} {'变化数':<8}")
        print("-" * 80)

        for snap in snapshots:
            time_str = datetime.fromtimestamp(snap.created_at).strftime("%Y-%m-%d %H:%M:%S")
            size_str = BackupManager._format_size(snap.total_size)
            desc = snap.description or "-"
            if len(desc) > 18:
                desc = desc[:16] + "..."
            print(f"{snap.id:<5} {time_str:<20} {desc:<20} {snap.total_files:<10} "
                  f"{size_str:<12} {snap.changed_files:<8}")

            if verbose:
                self._print_snapshot_files(snap.id)

        return 0

    def show_snapshot(self, snapshot_id: int) -> int:
        """显示单个快照的详细信息"""
        if not self.storage.is_initialized():
            print("错误: 未找到备份仓库")
            return 1

        snap = self.storage.get_snapshot(snapshot_id)
        if not snap:
            print(f"错误: 快照 #{snapshot_id} 不存在")
            return 1

        time_str = datetime.fromtimestamp(snap.created_at).strftime("%Y-%m-%d %H:%M:%S")
        size_str = BackupManager._format_size(snap.total_size)

        print(f"快照详情:")
        print(f"  ID:          #{snap.id}")
        print(f"  时间:        {time_str}")
        print(f"  描述:        {snap.description or '-'}")
        print(f"  总文件数:    {snap.total_files}")
        print(f"  总大小:      {size_str}")
        print(f"  变化文件数:  {snap.changed_files}")
        print()

        self._print_snapshot_files(snapshot_id)
        return 0

    def _print_snapshot_files(self, snapshot_id: int) -> None:
        files = self.storage.get_files_by_snapshot(snapshot_id)
        if not files:
            print("  (无文件记录)")
            return

        print(f"  文件列表 ({len(files)} 个):")
        print(f"  {'状态':<6} {'大小':<12} {'SHA256':<16} {'路径'}")
        print(f"  {'-'*6} {'-'*12} {'-'*16} {'-'*40}")

        for f in files:
            if f.is_deleted:
                status = "[删除]"
            elif f.is_new:
                status = "[新增]"
            elif f.is_modified:
                status = "[修改]"
            else:
                status = "      "
            size_str = BackupManager._format_size(f.size)
            hash_short = f.sha256[:12] if f.sha256 else "-"
            print(f"  {status:<6} {size_str:<12} {hash_short:<16} {f.rel_path}")

    def list_sources(self) -> int:
        """列出所有备份源"""
        if not self.storage.is_initialized():
            print("错误: 未找到备份仓库")
            return 1

        sources = self.storage.list_sources()
        if not sources:
            print("未添加任何备份源")
            return 0

        print(f"备份源列表 ({len(sources)} 个):\n")
        print(f"{'ID':<5} {'类型':<10} {'时间':<20} {'路径'}")
        print("-" * 80)
        for s in sources:
            time_str = datetime.fromtimestamp(s.created_at).strftime("%Y-%m-%d %H:%M:%S")
            print(f"{s.id:<5} {s.type:<10} {time_str:<20} {s.path}")
        return 0

    @staticmethod
    def _parse_time(time_str: str) -> float:
        """解析时间字符串，支持多种格式"""
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%Y/%m/%d %H:%M:%S",
            "%Y/%m/%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(time_str, fmt).timestamp()
            except ValueError:
                continue
        raise ValueError(f"无法解析时间格式: {time_str}，请使用 YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS")
