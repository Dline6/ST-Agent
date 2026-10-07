"""前端静态资产的硬约束：零 Python 依赖、零可执行片段。

前端跑在三引擎 WebView / 浏览器里（**没有 Node 运行时**），只经回环 HTTP 取 JSON
（`T-UI-001.2` 假设 `A1` / `A4`）；且「组件注册表只解析描述、不解析任意代码」这条
（[05 §6]）从骨架期就由本组用例钉住——它不依赖任何 JS 测试框架，纯静态扫描即可进 CI。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"
WEB_ROOTS = (_UI / "web", _UI / "dev" / "web")

FORBIDDEN: dict[str, re.Pattern[str]] = {
    "Python 侧引用": re.compile(r"st_agent"),
    "CommonJS require": re.compile(r"\brequire\s*\("),
    "eval": re.compile(r"\beval\s*\("),
    "Function 构造": re.compile(r"\bnew\s+Function\b"),
    "动态 import": re.compile(r"\bimport\s*\("),
    "innerHTML": re.compile(r"innerHTML"),
    "document.write": re.compile(r"document\s*\.\s*write\s*\("),
}

JS_FILES = sorted(path for root in WEB_ROOTS for path in root.rglob("*.js"))


def test_web_scripts_are_present() -> None:
    assert JS_FILES, "未扫到任何前端脚本（根路径写错会让本组用例空跑）"


@pytest.mark.parametrize("path", JS_FILES, ids=[path.name for path in JS_FILES])
def test_frontend_has_no_forbidden_constructs(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    hits = [name for name, pattern in FORBIDDEN.items() if pattern.search(text)]
    assert not hits, f"{path.name} 命中禁用构造：{hits}"


def test_dev_panel_lives_under_the_dev_package() -> None:
    """dev 面板必须落在会被发布剔除的子包内，否则「物理剔除」名不副实。"""
    assert (_UI / "dev" / "web" / "devpanel.js").is_file()
    assert not (_UI / "web" / "dev").exists()


def test_index_html_carries_the_dev_marker() -> None:
    html = (_UI / "web" / "index.html").read_text(encoding="utf-8")
    assert "<!--ST_DEV_SCRIPT-->" in html


_RELATIVE_IMPORT = re.compile(r"""from\s+'(\.{1,2}/[^']+)'""")


def test_every_relative_import_resolves_to_a_file() -> None:
    """前端模块图必须**可解析**：相对 import 指到盘上不存在的文件即失败。

    本组不跑 JS；这条是**静态**兜底，专拦「路径深度写错」那类错（如 `plugins/reflection/`
    里的 `../components/` 实际解析到 `plugins/components/`）。它在浏览器里表现为**整页停在
    加载态**，而服务端用例与「注册表两侧一致」的断言都看不见——2026-10-07 由
    [`T-UI-004.2`](../../项目管理/tasks/T-UI-004.2-反思中心表现层入口.md) 的浏览器实测撞出。
    """
    missing = [
        (path.name, target)
        for path in JS_FILES
        for target in _RELATIVE_IMPORT.findall(path.read_text(encoding="utf-8"))
        if not (path.parent / target).resolve().is_file()
    ]
    assert not missing, f"相对 import 指向不存在的文件：{missing}"
