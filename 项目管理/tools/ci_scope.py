#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ci_scope.py — CI 变更范围判定（PR gate 与夜间全量共用）

从 stdin 读 `git diff --name-only` 的输出，向 stdout 写 GitHub Actions 的两行输出：

    run=true|false
    targets=<pytest 目标...>

口径与 [工作流 §测试分层](../工作流.md) 一致。放成模块（而不是内联 shell）是为了让
`tests/test_tools.py` 直接钉住规则——2026-10 的两次范围误判都源于内联脚本与文档各写
一份、且没有测试兜底：

- `packaging/README.md` 命中 `^packaging/` ⇒ 文档-only PR 跑了 4580 条全量；
- `项目管理/tools/**` 落进「其余改动」分支 ⇒ 跑 817 条基表，而文档只要求 311 条。

事件口径：

- `pull_request`：无代码变更不跑；契约 / pyproject / 打包脚本 ⇒ 全量；仅工具面 ⇒
  层方向 + 工具行为；其余 ⇒ 跨层基表 + 变更层。
- `push`（合入 main）：PR 门禁已按范围跑过，这里只留 `verify_docs`（run=false）。
- `schedule` / `workflow_dispatch`：全量。
"""

from __future__ import annotations

import argparse
import re
import sys

#: 触发 pytest 的路径。``packaging/`` 只认构建脚本——README 等文档不该拉全量。
CODE_RE = re.compile(
    r"^(?:src/|tests/|pyproject\.toml|packaging/.*\.(?:py|spec)$|项目管理/tools/)"
)

#: 跨层共享面：契约、打包脚本与依赖清单 ⇒ 全量。
SHARED_RE = re.compile(
    r"^(?:src/st_agent/contracts/|pyproject\.toml|packaging/.*\.(?:py|spec)$)"
)

#: 工具面：改工具只锁层方向与工具行为。
TOOLS_ONLY_RE = re.compile(r"^(?:项目管理/tools/|tests/test_tools\.py$)")

#: 任何代码改动都随跑的跨层基表。
BASE = (
    "tests/contracts",
    "tests/test_layering.py",
    "tests/test_tools.py",
    "tests/test_module_size.py",
    "tests/integration",
)

#: 源码 / 测试目录前缀 → 该层套件（顺序即追加顺序）。
LAYER_MAP = tuple(
    (f"{side}{code}/", f"tests/{code}")
    for code in ("l0", "l1", "l2", "l3", "l4", "l5", "l6", "eco", "ui")
    for side in ("src/st_agent/", "tests/")
)


def decide(event: str, paths: list[str]) -> tuple[bool, list[str]]:
    """按事件与变更路径给出 (是否跑 pytest, targets)。"""
    paths = [p.strip() for p in paths if p and p.strip()]

    if event in ("schedule", "workflow_dispatch"):
        return True, ["tests"]
    if event == "push":
        return False, []

    code = [p for p in paths if CODE_RE.match(p)]
    if not code:
        return False, []
    if any(SHARED_RE.match(p) for p in code):
        return True, ["tests"]
    if all(TOOLS_ONLY_RE.match(p) for p in code):
        return True, ["tests/test_layering.py", "tests/test_tools.py"]

    targets = list(BASE)
    for prefix, target in LAYER_MAP:
        if target not in targets and any(p.startswith(prefix) for p in code):
            targets.append(target)
    return True, targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="CI 变更范围判定")
    parser.add_argument("--event", required=True,
                        choices=("pull_request", "push", "schedule", "workflow_dispatch"))
    parser.add_argument("paths", nargs="*", help="变更路径；缺省从 stdin 逐行读")
    args = parser.parse_args(argv)

    if args.paths:
        paths = args.paths
    elif hasattr(sys.stdin, "buffer"):
        # git 的路径是 UTF-8；不能用平台默认编码读（Windows 本地会是 cp936）
        paths = sys.stdin.buffer.read().decode("utf-8").splitlines()
    else:
        paths = sys.stdin.read().splitlines()
    run, targets = decide(args.event, paths)
    print(f"run={'true' if run else 'false'}")
    if targets:
        print("targets=" + " ".join(targets))
    return 0


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    raise SystemExit(main())
