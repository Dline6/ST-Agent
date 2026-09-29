"""渲染前的中性化校验门（[01 §6] 执行点 2；[01 §12] 的 `text_kinds` 分栏）。

落点在**回环服务边界**——即描述送达浏览器之前的最后一段 Python（选型见 [D-063]）：

- 词表唯一真相源是 [`contracts/neutrality.py`][n]，且规则库可随官方 Pack 更新；
  前端再抄一份必然分叉；
- 既有 L3 生成文案也都在 Python 构造期过同一个 `check_output`；
- `data` 槽（用户原话 / 记忆本体）**不整串复检**——否则会把用户数据误判为违规（[D-053]）。

命中即**阻断渲染**：调用方回 `validation_failed` 信封，不回可渲染的描述。本门**没有
关闭开关**（§6：校验点不可配置关闭）。

**留痕不回显违规文本**：命中记录只含槽路径与命中数——把违规原文抄进信封等于让它换个
地方继续传播。

[n]: ../../contracts/neutrality.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator

from st_agent.contracts.neutrality import NeutralityFinding, NeutralityGuard
from st_agent.contracts.ui_description import UiDescription

__all__ = ["GateVerdict", "NeutralityGate"]


def _iter_texts(value: Any) -> Iterator[str]:
    """递归取出一个槽值里的全部字符串（键是字段名，不算展示文本）。"""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_texts(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_texts(item)


@dataclass(frozen=True)
class GateVerdict:
    """一次出站校验的结论。"""

    passed: bool
    hits: tuple[str, ...] = ()
    """命中的位置（槽路径或 ``title``）——**只记位置，不记原文**。"""

    findings: tuple[NeutralityFinding, ...] = field(default=())

    @property
    def reason(self) -> str:
        """回给调用方的人可读原因（中性措辞，不含违规原文）。"""
        if self.passed:
            return ""
        return (
            f"生成文案未过中性校验（01 §6 执行点 2）：命中 {len(self.findings)} 处，"
            f"位置 {'、'.join(self.hits)}"
        )


class NeutralityGate:
    """对一份 UI 描述执行 §6 执行点 2（**无关闭开关**）。"""

    def __init__(self, guard: NeutralityGuard | None = None) -> None:
        self._guard = guard or NeutralityGuard()

    def check(self, description: UiDescription) -> GateVerdict:
        """检查该描述里所有**标为 ``generated``** 的文本。

        ``title`` 按 §12 恒为生成文案，故一律受检；``data`` 槽一律跳过。
        """
        hits: list[str] = []
        findings: list[NeutralityFinding] = []

        def _scan(text: str, where: str) -> None:
            verdict = self._guard.check_output(text)
            if not verdict.passed:
                hits.append(where)
                findings.extend(verdict.findings)

        if description.title:
            _scan(description.title, "title")

        for slot, value in description.slots.items():
            if description.text_kinds.get(slot) != "generated":
                continue
            for text in _iter_texts(value):
                _scan(text, f"slots.{slot}")

        return GateVerdict(passed=not findings, hits=tuple(hits), findings=tuple(findings))
