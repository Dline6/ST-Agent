"""数据表全套的静态守卫（[T-UI-026.1]）。

本组不跑 JS（本包不引浏览器运行时），只钉**能力锚点与词表**——这些是最容易在后续页面
改动里被无声删掉的部分：

1. `table` 渲染件仍按既有槽形状取值（列键 / `kind` / 三路冗余编码），且排序、列宽、
   选中、键盘、TSV、分页这六件能力各有可检索的实现锚点。
2. 密度双档只换 `[data-density]` 下的变量；数据表单元格消费 `--cell-x` / `--cell-y`，
   表头 sticky，选中是「底色 + 描边」而不是只靠颜色。
3. 默认档案：本机偏好缺省为 `compact`（[D-120] 已决项 2），列宽键带表身份。
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_WEB = _ROOT / "src" / "st_agent" / "ui" / "web"
_TABLE = _WEB / "js" / "components" / "table.js"
_PREFS = _WEB / "js" / "prefs.js"
_SETTINGS = _WEB / "js" / "pages" / "settings.js"
_CSS = _WEB / "css" / "app.css"
_TOKENS = _WEB / "css" / "tokens.css"
_TABLES_PY = _ROOT / "src" / "st_agent" / "ui" / "tables.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_files_are_present() -> None:
    for path in (_TABLE, _PREFS, _SETTINGS, _CSS, _TOKENS, _TABLES_PY):
        assert path.is_file(), f"缺文件：{path}"


def test_table_still_honours_the_slot_vocabulary() -> None:
    """列级三值与三路冗余编码保留（本叶只加交互，不改描述契约）。"""
    js = _read(_TABLE)
    py = _read(_TABLES_PY)
    for kind in ("text", "number", "direction"):
        assert f"'{kind}'" in js, f"table.js 丢了列级角色 {kind}"
        assert f'"{kind}"' in py, f"tables.py 丢了列级角色 {kind}"
    assert "COLUMN_KINDS" in js
    for arrow in ("▲", "▼", "—"):
        assert arrow in js, f"三路冗余的方向符 {arrow} 不在渲染件里"
    for modifier in ("num--up", "num--down", "num--flat", "num--none"):
        assert modifier in js, f"涨跌四态样式 {modifier} 不在渲染件里"


def test_table_carries_the_six_interaction_abilities() -> None:
    """排序 / 列宽 / 选中 / 键盘 / TSV / 分页各有实现锚点（删掉任一即红）。"""
    js = _read(_TABLE)
    anchors = {
        "排序三态": ["aria-sort", "'asc'", "'desc'", "cycleSort"],
        "列宽拖拽": ["data-table__resize", "pointerdown", "dblclick", "autofit"],
        "行选中": ["is-selected", "aria-selected", "ctrlKey", "metaKey"],
        "键盘导航": ["ArrowDown", "ArrowUp", "Home", "End", "PageDown", "PageUp", "Enter", "' '"],
        "TSV 复制": ["toTsv", "navigator.clipboard", "\\t"],
        "分页": ["PAGE_THRESHOLD", "PAGE_SIZE", "pageSlice", "data-table__pager"],
    }
    missing = {name: [token for token in tokens if token not in js]
               for name, tokens in anchors.items()}
    missing = {name: tokens for name, tokens in missing.items() if tokens}
    assert not missing, f"表格交互能力缺锚点：{missing}"


def test_table_identity_and_width_persistence() -> None:
    js = _read(_TABLE)
    prefs = _read(_PREFS)
    assert "tableIdentity" in js and "WIDTH_PREFIX" in js
    assert "title || ''" in js, "表身份应包含标题"
    assert "column.key" in js
    assert "st.table.widths." in prefs, "列宽未挂到本机偏好通道"


def test_density_defaults_to_compact_and_is_switchable() -> None:
    prefs = _read(_PREFS)
    assert "DENSITY_LEVELS = ['compact', 'comfortable']" in prefs
    assert "return DENSITY_LEVELS.includes(value) ? value : 'compact'" in prefs, (
        "默认密度缺省必须是 compact（D-120 已决项 2）"
    )
    settings = _read(_SETTINGS)
    assert "writeDensityDefault" in settings and "数据表默认密度" in settings
    assert "densityChrome" in settings
    js = _read(_TABLE)
    assert "readDensityDefault" in js
    assert "dataset.density" in js, "表应把密度挂到 [data-density] 容器上"
    assert "data-table__density" in js, "每张表应能临时切密度"


def test_density_css_uses_one_variable_set() -> None:
    css = _read(_CSS)
    assert re.search(r"\.data-table__grid th,", css), "缺数据表单元格规则"
    assert "padding: var(--cell-y) var(--cell-x)" in css, "单元格内距应消费密度变量"
    assert "height: var(--row-h)" in css, "行高应消费密度变量"
    assert ".data-table__grid thead th" in css and "position: sticky" in css, "表头必须 sticky"
    assert ".data-table__row.is-selected" in css
    assert "--accent-weak" in css and "outline: 1px solid var(--accent)" in css, (
        "选中态必须是底色 + 1px 强调描边（不只靠颜色）"
    )


def test_density_tokens_are_declared_both_levels() -> None:
    tokens = _read(_TOKENS)
    assert '[data-density="comfortable"]' in tokens
    assert '[data-density="compact"]' in tokens
    assert "--row-h: 24px" in tokens and "--cell-x: 8px" in tokens


def test_no_contract_or_registry_change() -> None:
    """本叶不新增组件型：`table` 仍是唯一的表格型。"""
    registry = _read(_WEB / "js" / "registry.js")
    assert "table: renderTable" in registry
    assert "data_table" not in registry and "metric:" not in registry
