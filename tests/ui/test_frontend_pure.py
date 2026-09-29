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
