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
