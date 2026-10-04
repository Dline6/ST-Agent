#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""convert_store.py — 存储明密双向转换（T-L0-018.1）

把一棵存储根**转换到另一种落盘格式**：`--to plain`（免口令、明文落盘）或
`--to encrypted`（用户主密码派生密钥加密）。语义与 `Store.convert` 同源：

- **源不动、写新根**——目标必须是全新（不存在或空）目录；转换全过程写在一个
  临时目录，收尾整目录改名，故中断只留「完整旧根」或「完整新根」，不留半格式根；
- `secrets` 分区的密钥材料与密文**原样搬运**，故**转换不改凭据口令**；
- 分区集合 / 文件相对路径 / `Store` 读回的明文逐字节等价。

零泄漏纪律（02 §6 / 宪法第 1 条）：口令只经环境变量传入，**不进 argv**（进程
列表可见）、不进摘要、不落盘。

用法：
    明文 → 加密：
    ST_AGENT_TARGET_PASSPHRASE='...' python 项目管理/tools/convert_store.py \
        --source tmp/store-plain --target tmp/store-enc --to encrypted

    加密 → 明文：
    ST_AGENT_SOURCE_PASSPHRASE='...' python 项目管理/tools/convert_store.py \
        --source tmp/store-enc --target tmp/store-plain --to plain

退出码：0 = 转换完成；2 = 用法 / 环境 / 转换错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE_PASSPHRASE_ENV = "ST_AGENT_SOURCE_PASSPHRASE"
TARGET_PASSPHRASE_ENV = "ST_AGENT_TARGET_PASSPHRASE"


def _count_files(root: Path) -> dict[str, int]:
    """各分区的**数据文件**数（不含清单；仅供摘要，不读内容）。"""
    out: dict[str, int] = {}
    for part in sorted(p for p in root.iterdir() if p.is_dir()):
        out[part.name] = sum(
            1 for f in part.rglob("*") if f.is_file() and f.name != "manifest.bin"
        )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="存储明密双向转换（源不动、写新根）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--source", required=True, help="源存储根")
    parser.add_argument("--target", required=True, help="目标根（必须不存在或为空目录）")
    parser.add_argument("--to", required=True, choices=("plain", "encrypted"),
                        dest="to_format", help="目标格式")
    parser.add_argument("--out", default="", help="可选：摘要 JSON 落盘位置")
    args = parser.parse_args(argv)

    source = Path(args.source)
    if not source.is_dir():
        print(f"源根不存在：{source}", file=sys.stderr)
        return 2
    source_passphrase = os.environ.get(SOURCE_PASSPHRASE_ENV) or None
    target_passphrase = os.environ.get(TARGET_PASSPHRASE_ENV) or None
    if args.to_format == "encrypted" and not target_passphrase:
        print(f"目标为加密格式，须设置环境变量 {TARGET_PASSPHRASE_ENV}", file=sys.stderr)
        return 2

    try:
        from st_agent.l0.storage import Store, read_mode
    except ImportError as exc:  # 包未安装
        print(f"无法导入 st_agent（先 pip install -e .）：{exc}", file=sys.stderr)
        return 2

    try:
        source_mode = read_mode(source) or "encrypted"
        Store.convert(
            source, Path(args.target), to_format=args.to_format,
            source_passphrase=source_passphrase,
            target_passphrase=target_passphrase,
        )
    except Exception as exc:  # noqa: BLE001 — 装配面统一显式化（失败不留半格式根）
        print(f"转换失败：{exc}", file=sys.stderr)
        return 2

    summary = {
        "source": str(source.resolve()),
        "target": str(Path(args.target).resolve()),
        "from": source_mode,
        "to": args.to_format,
        "source_passphrase_source": f"env:{SOURCE_PASSPHRASE_ENV}"
        if source_passphrase else "(未给出)",
        "target_passphrase_source": f"env:{TARGET_PASSPHRASE_ENV}"
        if target_passphrase else "(未给出)",
        "files": _count_files(Path(args.target)),
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
