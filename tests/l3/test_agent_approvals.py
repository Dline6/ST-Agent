"""按 Skill 供给「已批准权限」的口径（T-AGT-014.3）。

循环的 ``approved_permissions`` 支持三种形态：全局序列（既有）、映射、可调用。
后两种让组合根按**实际执行的 Skill** 取批准态，避免把 A 的批准借给 B。
"""

from __future__ import annotations

from st_agent.l3.runtime.loop import resolve_approved


def test_sequence_is_global():
    assert resolve_approved(("local_read:<a/**>",), "sk_x_v1.0") == ("local_read:<a/**>",)


def test_mapping_is_per_skill():
    source = {"sk_x_v1.0": ("local_write:<workspace/**>",)}
    assert resolve_approved(source, "sk_x_v1.0") == ("local_write:<workspace/**>",)
    assert resolve_approved(source, "sk_y_v1.0") == ()


def test_callable_is_per_skill():
    seen: list[str] = []

    def source(skill_id: str) -> tuple[str, ...]:
        seen.append(skill_id)
        return ("local_read:<inbox/**>",) if skill_id == "sk_x_v1.0" else ()

    assert resolve_approved(source, "sk_x_v1.0") == ("local_read:<inbox/**>",)
    assert resolve_approved(source, "sk_y_v1.0") == ()
    assert seen == ["sk_x_v1.0", "sk_y_v1.0"]


def test_none_is_empty():
    assert resolve_approved(None, "sk_x_v1.0") == ()
