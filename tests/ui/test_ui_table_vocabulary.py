"""`table` 槽词汇：产出方与渲染件**两侧形状不漂移**（[01 §12]；[D-064] 的槽词汇表）。

本组锁的是 2026-10-09 那处实测缺陷的**成因**，不是它的症状——当时六处产出方给位置形状
（``columns: ["名"]`` / ``rows: [[值]]``），渲染件读键控形状（``column.label`` /
``row[column.key]``），于是**生产面上六张表全是空的**（Edge 实测，[D-105] ①）。纯 Python 侧
的断言当时**照样全绿**：它们锁的正是位置形状；前端又没有 JS 测试。故本组从三个方向钉住：

1. **产出方侧**——`_gated_description("table", …)` 一律经 `ui/tables.py` 的漏斗出，
   不得就地手拼 slots（手拼正是那六处的形态）；
2. **两侧的类型集**——`kind` 的三值在 Python 与 `table.js` 各有一份，逐值一致；
3. **涨跌三路冗余**——方向符与颜色在渲染件里**同一次**产出，删任一路即红。

只做静态与纯函数断言（不跑 JS、不依赖浏览器）——浏览器实测是收工时的验证，不是这里的兜底。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from st_agent.ui.app import UiApp, build_ui
from st_agent.ui.tables import COLUMN_KINDS, Column, table_slots

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"
_APP_PY = _UI / "app.py"
_TABLE_JS = _UI / "web" / "js" / "components" / "table.js"
_APP_CSS = _UI / "web" / "css" / "app.css"


# ── 1 · 产出方一律经漏斗 ──────────────────────────────────────────────────────


def test_every_table_description_goes_through_the_funnel() -> None:
    """`_gated_description("table", …)` 的 slots 必须是 `table_slots(...)` 的返回值。

    这条守的是**构造点只有一处**：形状写在 `ui/tables.py` 一次，六处产出方都经它出表。
    就地手拼 slots 会让两侧重新各自演化——那正是本组存在的理由。
    """
    tree = ast.parse(_APP_PY.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _is_table_description(node)
    ]
    assert len(calls) >= 6, f"只扫到 {len(calls)} 处表格描述（根路径或写法变了会让本用例空跑）"
    for node in calls:
        kwargs = {kw.arg: kw.value for kw in node.keywords}
        assert isinstance(kwargs.get("slots"), ast.Call), "表格的 slots 未经漏斗构造"
        assert _callee(kwargs["slots"]) == "table_slots"
        assert _callee(kwargs.get("text_kinds")) == "table_text_kinds"


def _is_table_description(node: ast.Call) -> bool:
    callee = _callee(node)
    if callee != "_gated_description" or not node.args:
        return False
    first = node.args[0]
    return isinstance(first, ast.Constant) and first.value == "table"


def _callee(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Call):
        return _callee(node.func)
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Name):
        return node.id
    return None


# ── 2 · 两侧的 kind 类型集逐值一致 ────────────────────────────────────────────


def test_column_kinds_match_between_python_and_the_renderer() -> None:
    """`kind` 的三值两侧各有一份（同 `ui/registry.py` 与前端注册表的关系）——不得漂移。"""
    text = _TABLE_JS.read_text(encoding="utf-8")
    match = re.search(r"const COLUMN_KINDS = \[(.*?)\];", text, re.S)
    assert match, "未在 table.js 里找到 COLUMN_KINDS（渲染件不再声明自己的 kind 集？）"
    frontend = tuple(re.findall(r"'([^']+)'", match.group(1)))
    assert frontend == COLUMN_KINDS


@pytest.mark.parametrize("kind", [k for k in COLUMN_KINDS if k != "text"])
def test_digits_only_ride_on_numeric_columns(kind: str) -> None:
    """`digits` 只对数字与涨跌列有意义——这一点由**形状本身**表达，不靠约定。"""
    numeric = Column("value", "值", kind=kind).as_slot()
    assert numeric["digits"] == 2 and numeric["kind"] == kind
    plain = Column("note", "备注").as_slot()
    assert plain["kind"] == "text" and "digits" not in plain


def test_column_rejects_a_key_the_renderer_could_not_bind() -> None:
    """列键是渲染件的取值面（`row[column.key]`）——不是标识符则两侧无从绑定。"""
    with pytest.raises(ValueError):
        Column("", "空键")
    with pytest.raises(ValueError):
        Column("not an identifier", "带空格的键")
    with pytest.raises(ValueError):
        Column("value", "值", kind="percentage")  # type: ignore[arg-type]


# ── 3 · 涨跌的三路冗余恒同时出现 ──────────────────────────────────────────────

_DIRECTION_MODIFIERS = ("num--up", "num--down", "num--flat")


def _direction_cell_body() -> str:
    """`directionCell` 的函数体（按大括号配平取，供下面两条断言用）。"""
    text = _TABLE_JS.read_text(encoding="utf-8")
    start = text.index("function directionCell(")
    depth = 0
    for index in range(text.index("{", start), len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError("directionCell 的大括号未配平")


def test_direction_encoding_never_splits_colour_from_the_arrow() -> None:
    """涨跌的**颜色类**与**方向符**必须出现在同一行——三路不可分离（规范 §2.2）。

    灰阶判据（剥掉颜色后涨跌仍可判）靠的就是这条：符号与颜色在同一次返回里给出，
    故不存在「只上颜色不出符号」的写法。
    """
    body = _direction_cell_body()
    for modifier in _DIRECTION_MODIFIERS:
        lines = [line for line in body.splitlines() if modifier in line]
        assert lines, f"{modifier} 未在 directionCell 里出现（涨跌少了一路色）"
        for line in lines:
            assert "ARROW." in line, (
                f"{modifier} 与方向符**不在同一行**：{line.strip()!r}"
                "——三路冗余被拆开了（灰阶下涨跌将不可判）"
            )


def _strip_line_comments(text: str) -> str:
    """去掉**整行注释**（本模块顶部有一张把三路并列写出来的说明表，那不是产出点）。"""
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("//")
    )


def test_direction_colours_are_emitted_only_by_the_direction_branch() -> None:
    """三路色只在 `directionCell` 里产出——别处不得凭空上色（上了就没有方向符）。"""
    text = _strip_line_comments(_TABLE_JS.read_text(encoding="utf-8"))
    outside = text.replace(_direction_cell_body(), "")
    for modifier in _DIRECTION_MODIFIERS:
        assert modifier not in outside, f"{modifier} 出现在了 directionCell 之外"


def test_number_and_direction_modifiers_have_stylesheet_rules() -> None:
    """渲染件产出的类名与样式表**逐名对应**（同令牌表与 `tokens.css` 的关系）。

    无数据一路（`num--none`）是**能力缺席**而非一个方向，故它取占位墨而不取方向色。
    """
    css = _APP_CSS.read_text(encoding="utf-8")
    modifiers = [*_DIRECTION_MODIFIERS, "num--none"]
    for modifier in modifiers:
        assert f".component-table .{modifier}" in css, f"样式表里没有 .{modifier} 的规则"
    text = _TABLE_JS.read_text(encoding="utf-8")
    for modifier in modifiers:
        assert f"'{modifier}'" in text, f"渲染件不再产出 {modifier}（样式表那条成了死规则）"


def test_missing_key_falls_to_no_data_not_to_a_fabricated_value() -> None:
    """行里**没有**列键 ⇒ 单元格是「无数据」而不是原样吞掉（两侧的空值口径一致）。"""
    slots = table_slots(
        (Column("close", "收盘", kind="number"), Column("pct_chg", "涨跌幅", kind="direction")),
        [{}],
    )
    assert slots["rows"] == [{}], "漏斗不得给缺键的行补值（补了就是编造）"
    assert slots["columns"][1] == {
        "key": "pct_chg", "label": "涨跌幅", "kind": "direction", "digits": 2,
    }


# ── 4 · 六处产出方的真产出形状（合并后的端到端一遍） ──────────────────────────


class _FakeReflection:
    def experiments(self) -> list[dict]:
        return [{
            "hypothesis": "新文案更易被采纳", "scope": "delivery-strategy",
            "sample": {"baseline": 3}, "result": {"new": 3},
            "decision": "adopt", "status": "decided",
        }]

    def trainings(self) -> list[dict]:
        return [{"session": {
            "training_id": "trn_1", "correction": "我觉得这条不对",
            "restatement": "关注的是估值", "pattern": {"topic": "估值"},
            "confirmed": True, "created_at": "2026-10-11T20:00:00+08:00",
        }}]


class _FakeEco:
    def inbox(self) -> list[dict]:
        return [{"file_name": "a.stskill", "size": 128}]

    def index(self) -> dict:
        return {"available": True, "entries": [{
            "kind": "official", "name": "官方 Pack", "version": "1.0",
            "description": "官方资源", "download_url": "https://example.invalid/p",
            "checksum": "sha256:0",
        }]}

    def imports_ledger(self) -> dict:
        return {"empty_text": "", "records": [{
            "import_id": "imp_1", "kind": "skill", "installed_id": "sk_ma_v1.0",
            "origin": {"sharer": "alice", "imported_at": "2026-10-07T18:00:00+08:00"},
        }]}

    def violations(self) -> dict:
        return {"available": True, "disabled": [], "records": [{
            "skill_id": "sk_ma_watch", "violation": "local_read",
            "occurred_at": "2026-10-07T18:00:00+08:00", "trace_id": "tr_" + "0" * 20,
        }]}


@pytest.fixture()
def bare_app() -> UiApp:
    return build_ui(host="127.0.0.1", port=1, reflection=_FakeReflection(), eco=_FakeEco())


_SIX_TABLES = (
    "api_reflection_experiments",
    "api_reflection_training",
    "api_eco_inbox",
    "api_eco_index",
    "api_eco_imports",
    "api_eco_violation_history",
)


@pytest.mark.parametrize("method", _SIX_TABLES)
def test_produced_tables_carry_the_keyed_shape(bare_app: UiApp, method: str) -> None:
    """六处产出方的真产出：列带 `key`/`label`/`kind`，行是**按键取值**的对象。

    位置形状在这里就会现形（`rows` 的元素是 list、`columns` 的元素是 str）——而那正是
    实机上一片空白的形态。
    """
    payload = getattr(bare_app, method)()
    description = payload["data"]
    assert description["component_type"] == "table", f"{method} 已不是表格"
    columns = description["slots"]["columns"]
    rows = description["slots"]["rows"]
    assert columns and rows, f"{method} 出了空表（本用例会空跑）"
    keys = []
    for column in columns:
        assert isinstance(column, dict), f"{method} 的列不是键控形状：{column!r}"
        assert column["label"] and column["kind"] in COLUMN_KINDS
        keys.append(column["key"])
    for row in rows:
        assert isinstance(row, dict), f"{method} 的行不是键控形状：{row!r}"
        assert set(row) <= set(keys)
