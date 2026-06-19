#!/usr/bin/env python3
"""增量备份命令行校验工具 - 入口脚本"""

import sys
from backup_tool.cli import main

if __name__ == "__main__":
    sys.exit(main())
