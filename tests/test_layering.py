"""铁律 7（层间只允许向下依赖）的机器校验。

扫描 ``src/st_agent/<layer>/**.py`` 的 import 语句，按层号断言「只能 import 自身或更下层」；
契约层（``contracts``）位于所有层之下，故不得 import 任何具体层。

依赖方向（00-架构总览 §3）：contracts < l0 < l1 < l2 < l3 < l4 < l5 < l6 < eco。
`tests/` 不在扫描范围——测试天然跨层装配，是集成关卡的合法位置。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "st_agent"

#: 层号（越小越底层）；契约层为跨层共享，置于最底。
LAYER_ORDER = {
    "contracts": 0,
    "l0": 1,
    "l1": 2,
    "l2": 3,
    "l3": 4,
    "l4": 5,
    "l5": 6,
    "l6": 7,
    "eco": 8,
}


def _modules():
    for layer, order in LAYER_ORDER.items():
        d = SRC / layer
        if not d.is_dir():
            continue
        for path in sorted(d.rglob("*.py")):
            yield layer, order, path


CASES = [(layer, order, path, f"{layer}/{path.relative_to(SRC / layer)}".replace("\\", "/"))
         for layer, order, path in _modules()]


def _imports(path: Path):
    """产出 ``(模块名, 行号)``（含 ``import x`` 与 ``from x import y``）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                yield node.module, node.lineno
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno


def _layer_of(module: str) -> str | None:
    """``st_agent.l0.storage.store`` → ``l0``；非本仓库模块 → None。"""
    parts = module.split(".")
    if len(parts) >= 2 and parts[0] == "st_agent" and parts[1] in LAYER_ORDER:
        return parts[1]
    return None


def test_source_layers_present():
    """至少扫到契约层与已开工的层（防止路径写错导致整组用例空跑）。"""
    layers = {layer for layer, _o, _p, _r in CASES}
    assert {"contracts", "l0", "l1"} <= layers


@pytest.mark.parametrize("layer,order,path,rel", CASES, ids=[c[3] for c in CASES])
def test_only_downward_dependencies(layer, order, path, rel):
    violations = []
    for module, lineno in _imports(path):
        target = _layer_of(module)
        if target is None or target == layer:
            continue
        if LAYER_ORDER[target] > order:
            violations.append(f"{rel}:{lineno} 依赖更上层 {module}")
    assert not violations, (
        f"违反铁律 7（层间只允许向下依赖）——{layer} 不得依赖更上层：\n  "
        + "\n  ".join(violations)
        + "\n跨层协作一律经 01-平台共享契约（依赖注入/事件），见 00-架构总览 §3。"
    )


# ── 表现层（跨层工程 `T-UI-*`）的依赖约束 ────────────────────────────────────────
# `ui` 是各层的**客户端组合根**，不是第七层（00 §1.1；D-060 ⑤）。故它**不进
# `LAYER_ORDER`**——把 `ui` 塞进层表等于把「不是第七层」反向编码，而且会让
# `test_only_downward_dependencies` 的层序语义变得没有意义。约束改由下面这组用例承担。

UI_ALLOWED_TOP = set(LAYER_ORDER) | {"ui"}


def test_ui_is_client_only():
    """`src/st_agent/ui/**` 只可消费 contracts / l0..l6 / 自身；任何层不得反向 import 它。"""
    ui_dir = SRC / "ui"
    if ui_dir.is_dir():
        for path in sorted(ui_dir.rglob("*.py")):
            rel = f"ui/{path.relative_to(ui_dir)}".replace("\\", "/")
            for module, lineno in _imports(path):
                if not module.startswith("st_agent."):
                    continue
                parts = module.split(".")
                top = parts[1] if len(parts) > 1 else ""
                assert top in UI_ALLOWED_TOP, (
                    f"{rel}:{lineno} import 了 {module}——表现层是各层的客户端组合根，"
                    "只可消费 contracts / l0..l6 / 自身（00 §1.1；D-060）"
                )

    for layer, _order, path, rel in CASES:
        for module, lineno in _imports(path):
            assert not module.startswith("st_agent.ui"), (
                f"{rel}:{lineno} 反向 import 了表现层——UI 是客户端，层不得依赖它"
                "（铁律 7 的同型约束；D-060 ⑤）"
            )


# ── 顶层组合根 `src/st_agent/app.py`（M1 关卡 T-INT-002 / M2 关卡 T-INT-003）的依赖约束 ─
# `app` 是**装配各层的组合根，不是第七层**（同 `ui`，D-060 ⑤）：它不进 `LAYER_ORDER`
# （否则把「不是层」反向编码），其约束由下面这组用例承担（T-INT-002 A2）。

APP_PATH = SRC / "app.py"

#: 组合根只可向下装配这些层（M1 反向流触及 L0–L3，M2 关卡加 L4，M3 关卡加 L5，
#: M4 关卡 [`T-INT-005`] 加 L6 与 `eco`——「关卡接线才进允许集」，M4 关卡即 L6 / ECO 的接线。
APP_ALLOWED_IMPORTS = {"contracts", "l0", "l1", "l2", "l3", "l4", "l5", "l6", "eco"}

ENTRY_PATH = SRC / "__main__.py"
"""生产运行入口（``python -m st_agent``；[`T-UI-002.1`]）——装配 `app` + `ui`，**不是层**。

它是**唯一**允许 import 组合根 `app` 的模块：壳要拉起的正是「装配好的后端」。
`ui` 仍只经鸭子端口收 `chat`（下条反向断言），故 `app`+`ui` 的合流只在入口发生。
"""

ENTRY_ALLOWED_IMPORTS = set(LAYER_ORDER) | {"app", "ui"}
ENTRY_REL = "__main__.py"


def test_app_is_composition_root_only():
    """`app` 只向下 import 已开工的层 + contracts；除入口外任何模块都不得 import 它。"""
    if not APP_PATH.is_file():
        pytest.skip("组合根尚未交付")
    for module, lineno in _imports(APP_PATH):
        if not module.startswith("st_agent."):
            continue  # __future__ / 标准库 / 第三方（pydantic）不属层间约束
        target = _layer_of(module)
        assert target is not None and target in APP_ALLOWED_IMPORTS, (
            f"app.py:{lineno} import 了 {module}——组合根只可向下装配 "
            f"{sorted(APP_ALLOWED_IMPORTS)}（M1/M2/M3/M4 反向流面；"
            "T-INT-002 A2 / T-INT-003 A1 / T-INT-004 A1 / T-INT-005 A1）"
        )

    consumers = []
    for path in sorted(SRC.rglob("*.py")):
        if path == APP_PATH:
            continue
        rel = str(path.relative_to(SRC)).replace("\\", "/")
        for module, lineno in _imports(path):
            if module == "st_agent.app" or module.startswith("st_agent.app."):
                consumers.append(f"{rel}:{lineno}")
                assert rel == ENTRY_REL, (
                    f"{rel}:{lineno} 反向 import 了组合根 app——装配根只被生产入口 "
                    f"（{ENTRY_REL}，`python -m st_agent`）与测试消费；层与表现层都不得依赖"
                    "它（对话面经鸭子端口注入，ui 不 import app，T-INT-002 A2）"
                )


def test_entry_is_assembly_only():
    """生产入口只可装配 `app` / `ui` / contracts / 各层；且不得被任何模块 import。"""
    if not ENTRY_PATH.is_file():
        pytest.skip("生产入口尚未交付")
    for module, lineno in _imports(ENTRY_PATH):
        if not module.startswith("st_agent."):
            continue
        parts = module.split(".")
        top = parts[1] if len(parts) > 1 else ""
        assert top in ENTRY_ALLOWED_IMPORTS, (
            f"{ENTRY_REL}:{lineno} import 了 {module}——入口是**装配面**，只可消费 "
            f"{sorted(ENTRY_ALLOWED_IMPORTS)}（T-UI-002.1；00 §1.1）"
        )

    for path in sorted(SRC.rglob("*.py")):
        if path == ENTRY_PATH:
            continue
        rel = str(path.relative_to(SRC)).replace("\\", "/")
        for module, lineno in _imports(path):
            assert module != "st_agent.__main__" and not module.startswith("st_agent.__main__."), (
                f"{rel}:{lineno} import 了生产入口——入口是进程边界，只由 `python -m st_agent` 唤醒"
            )


# ── `analyze` 去向的跨层边界（M2 关卡 T-INT-003 A5）─────────────────────────────
# L4 的编排件由组合根经**鸭子端口**注入总线：L3 不得 import L4（铁律 7；`LAYER_ORDER`
# 为 `l3 < l4`）。`test_only_downward_dependencies` 已按层号兜住，这里再**点名**钉住
# `analyze` 去向所在模块——它的载荷面（`DispatchOutcome.analysis`）最容易被顺手写成
# 具体 L4 类型，从而在不知不觉间把这条边反向。

BUS_PATH = SRC / "l3" / "dispatch" / "bus.py"


def test_analyze_route_stays_duck_typed():
    """总线模块不 import `st_agent.l4`——`analyze` 载荷按鸭子面承载（T-INT-003 A5）。"""
    for module, lineno in _imports(BUS_PATH):
        assert not module.startswith("st_agent.l4"), (
            f"l3/dispatch/bus.py:{lineno} import 了 {module}——`analyze` 去向的编排件"
            "由组合根按鸭子端口注入（铁律 7；T-INT-003 A5）"
        )
