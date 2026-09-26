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
