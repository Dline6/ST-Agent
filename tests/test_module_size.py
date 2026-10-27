"""组合根与表现层的体量守卫（2026-10-27 按面拆分后新增）。

拆分的价值在于**物理边界**：一个面一个文件，加界面能力时改的是一个几百行的模块，
而不是往两个六千 / 三千行的文件里再堆一段。没有守卫，边界会随下一次功能回长——
本组把「拆分后的尺寸」钉成上限，越界即红。

只覆盖本次拆分的两块（`app.py` 与 `ui/app.py` 及其子模块）；其余层的存量体量不在此列，
以免把一次重构扩成全仓清理。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "st_agent"

#: 两个组合根门面只留装配与再导出——超过这个尺寸说明「面」又被堆回门面了。
FACADE_CAPS = {
    ROOT / "app.py": 300,
    ROOT / "ui" / "app.py": 500,
}

#: 拆出的每个面 / 部件模块的上限；命中即应继续按面拆。
SUBPACKAGES = (ROOT / "composition", ROOT / "ui" / "surfaces")
SUBPACKAGE_CAP = 800


def _lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def test_facades_stay_thin() -> None:
    for path, cap in FACADE_CAPS.items():
        assert path.is_file(), f"{path} 不存在（拆分后的门面路径变了？）"
        size = _lines(path)
        assert size <= cap, (
            f"{path.relative_to(ROOT.parent)} 已 {size} 行（上限 {cap}）——"
            "新的界面能力应落到 composition/ 或 ui/surfaces/ 的对应面模块，不要再堆回门面"
        )


def test_surface_modules_stay_reviewable() -> None:
    files = [p for package in SUBPACKAGES for p in sorted(package.rglob("*.py"))]
    assert len(files) >= 10, f"只扫到 {len(files)} 个面 / 部件模块（根路径写错会让本用例空跑）"
    oversized = [
        f"{path.relative_to(ROOT.parent).as_posix()}（{_lines(path)} 行）"
        for path in files
        if _lines(path) > SUBPACKAGE_CAP
    ]
    assert not oversized, (
        f"以下模块超过 {SUBPACKAGE_CAP} 行，应按面再拆：\n  " + "\n  ".join(oversized)
    )
