"""``python -m st_agent.ui.shell``——桌面壳的手动入口（[`T-UI-002.2`]）。

壳自己**不是**常驻主体：它拉起后端（缺省 ``python -m st_agent``，冻结产物传
``--backend <二进制>``）、读握手、开窗，退出时优雅回收后端。开机自启注册的也是这条命令
（[`autostart.self_command`](autostart.py)）。
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from st_agent.ui.shell.app import run_shell

__all__ = ["main"]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m st_agent.ui.shell",
        description="ST Agent 桌面壳（原生窗口 / 托盘 / 自启；业务全在后端）",
    )
    parser.add_argument(
        "--backend", default=None,
        help="后端可执行文件（冻结产物用）；缺省 python -m st_agent",
    )
    parser.add_argument("--dev", action="store_true", help="给后端开 dev 面（发布构建不含）")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_shell(backend_executable=args.backend, dev=args.dev)


if __name__ == "__main__":      # pragma: no cover - 手动入口
    raise SystemExit(main())
