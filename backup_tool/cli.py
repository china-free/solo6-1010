"""命令行入口"""

import argparse
import sys
from pathlib import Path

from .backup import BackupManager
from .history import HistoryManager
from .storage import Storage
from .verify import Verifier


def _find_backup_root(explicit_path: str | None) -> Path:
    """查找备份仓库根目录"""
    if explicit_path:
        return Path(explicit_path).resolve()
    found = Storage.find_backup_root(Path.cwd())
    if found:
        return found
    return Path.cwd().resolve()


def cmd_init(args: argparse.Namespace) -> int:
    root = _find_backup_root(args.path)
    mgr = BackupManager(root)
    return 0 if mgr.init() else 1


def cmd_add(args: argparse.Namespace) -> int:
    root = _find_backup_root(args.path)
    storage = Storage(root)
    if not storage.is_initialized():
        print("错误: 未找到备份仓库，请先运行 init 命令", file=sys.stderr)
        return 1

    for target in args.targets:
        target_path = Path(target).resolve()
        if not target_path.exists():
            print(f"错误: 路径不存在: {target_path}", file=sys.stderr)
            continue
        source_type = "directory" if target_path.is_dir() else "file"
        sid = storage.add_source(str(target_path), source_type)
        print(f"✅ 已添加备份源 [{source_type}] #{sid}: {target_path}")
    return 0


def cmd_backup(args: argparse.Namespace) -> int:
    root = _find_backup_root(args.path)
    mgr = BackupManager(root)
    result = mgr.backup(description=args.description or "")
    return 0 if result else 1


def cmd_list(args: argparse.Namespace) -> int:
    root = _find_backup_root(args.path)
    hist = HistoryManager(root)

    if args.what == "snapshots":
        return hist.list_snapshots(
            start_time=args.from_time,
            end_time=args.to_time,
            limit=args.limit,
            verbose=args.verbose,
        )
    elif args.what == "sources":
        return hist.list_sources()
    return 1


def cmd_show(args: argparse.Namespace) -> int:
    root = _find_backup_root(args.path)
    hist = HistoryManager(root)
    return hist.show_snapshot(args.snapshot_id)


def cmd_verify(args: argparse.Namespace) -> int:
    root = _find_backup_root(args.path)
    verifier = Verifier(root)
    try:
        if args.source:
            verifier.verify_source_consistency(
                snapshot_id=args.snapshot_id,
                verbose=args.verbose,
            )
        elif args.all:
            verifier.verify_all(algorithm=args.algorithm)
        else:
            verifier.verify_snapshot(
                snapshot_id=args.snapshot_id,
                algorithm=args.algorithm,
                verbose=args.verbose,
            )
        return 0
    except (RuntimeError, ValueError) as e:
        print(f"错误: {e}", file=sys.stderr)
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="backuptool",
        description="增量备份命令行校验工具 - 用于开发者备份代码仓库和配置文件",
    )
    parser.add_argument(
        "-p", "--path",
        help="备份仓库路径（默认在当前目录及父目录中查找）",
        default=None,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # init
    p_init = sub.add_parser("init", help="在当前目录初始化备份仓库")
    p_init.set_defaults(func=cmd_init)

    # add
    p_add = sub.add_parser("add", help="添加要备份的目录或文件")
    p_add.add_argument("targets", nargs="+", help="要备份的目录或文件路径")
    p_add.set_defaults(func=cmd_add)

    # backup
    p_backup = sub.add_parser("backup", help="执行一次增量备份")
    p_backup.add_argument("-d", "--description", help="本次备份描述", default="")
    p_backup.set_defaults(func=cmd_backup)

    # list
    p_list = sub.add_parser("list", help="列出备份历史或备份源")
    p_list.add_argument(
        "what", nargs="?", default="snapshots",
        choices=["snapshots", "sources"],
        help="列出快照或备份源（默认 snapshots）",
    )
    p_list.add_argument("-n", "--limit", type=int, help="仅显示最近 N 条")
    p_list.add_argument("--from", dest="from_time", help="起始时间，如 2024-01-01")
    p_list.add_argument("--to", dest="to_time", help="结束时间，如 2024-12-31")
    p_list.add_argument("-v", "--verbose", action="store_true", help="显示详细文件列表")
    p_list.set_defaults(func=cmd_list)

    # show
    p_show = sub.add_parser("show", help="显示指定快照的详细信息")
    p_show.add_argument("snapshot_id", type=int, help="快照 ID")
    p_show.set_defaults(func=cmd_show)

    # verify
    p_verify = sub.add_parser("verify", help="校验备份完整性")
    p_verify.add_argument("snapshot_id", nargs="?", type=int, default=None, help="快照 ID（默认最新）")
    p_verify.add_argument(
        "-a", "--algorithm", choices=["md5", "sha256"],
        default="sha256", help="校验算法（默认 sha256）",
    )
    p_verify.add_argument("--all", action="store_true", help="校验所有快照")
    p_verify.add_argument("-s", "--source", action="store_true",
                          help="校验快照文件列表与源目录当前状态的一致性")
    p_verify.add_argument("-v", "--verbose", action="store_true", help="显示每个文件的校验结果")
    p_verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
