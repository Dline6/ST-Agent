"""演进面：变更历史、回滚、授权与出厂重置（[T-UI-004.3]）。"""

from __future__ import annotations

from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _now,
    _required,
)


class EvolutionSurface(SurfaceBase):
    """演进面：变更历史、回滚、授权与出厂重置（[T-UI-004.3]）。"""

    def api_evolution_changes(self) -> dict[str, Any]:
        """变更历史时间线（`change_timeline`）——含回滚状态与「一键回滚」动作。"""
        return self._call_face(
            "evolution-changes", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(self._changes_description(face)),
        )

    def api_evolution_rollback(self, body: dict[str, Any]) -> dict[str, Any]:
        """一键回滚——按该变更的**生效前取值**回放；回滚**本身也是一次变更**（[08 §6]）。"""
        return self._call_face(
            "evolution-rollback", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(face.rollback(str(_required(body, "change_id")))),
        )

    def api_evolution_authorization(self) -> dict[str, Any]:
        """演进授权设置面（`setting_panel`）——档位可改，风险分级清单只呈现。"""
        return self._call_face(
            "evolution-authorization", self.reflection, "L6 反思演进面",
            lambda face: self._authorization_envelope(face),
        )

    def api_evolution_set(self, body: dict[str, Any]) -> dict[str, Any]:
        """应用一条设置——经 [01 §7] 的落值面生效（**切换留痕**，[08 §5]）。"""
        return self._call_face(
            "evolution-set", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(
                self._apply_setting(face, str(_required(body, "config_id")), body.get("value"))
            ),
        )

    def api_evolution_factory_reset(self, body: dict[str, Any]) -> dict[str, Any]:
        """「回滚到出厂设置」——**三次确认**是硬门（不足即拒、不留痕、不执行任何动作，[08 §6]）。"""
        return self._call_face(
            "evolution-factory-reset", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(face.factory_reset(body.get("confirmations"))),
        )

    def _changes_description(self, face: Any) -> UiDescription:
        items = []
        for change in face.changes():
            rolled_back = bool(change.get("rolled_back_at"))
            items.append({
                **change,
                "rolled_back": rolled_back,
                # 已回滚过的条目不再给回滚动作——重复回滚会被 L6 显式拒（不静默）
                "actions": [] if rolled_back else ["rollback"],
            })
        return self._gated_description(
            "change_timeline",
            slots={"changes": items, "labels": {"rollback": "一键回滚"}},
            text_kinds={"changes": "data", "labels": "generated"},
            title="变更历史",
        )

    def _authorization_envelope(self, face: Any) -> ResultEnvelope:
        info = face.authorization()
        if not info.get("available"):
            return ResultEnvelope.unavailable(
                str(info.get("reason") or "演进授权面未接线"), last_updated_at=_now()
            )
        entries = [
            {
                "config_id": info.get("tier_config_id"),
                "params": {"config_id": info.get("tier_config_id")},
                "current": info.get("tier"),
                "options": [{"value": value} for value in info.get("tiers") or ()],
                "actions": ["set"],
            },
            {
                "config_id": info.get("grading_config_id"),
                "current": f"{len(info.get('grading') or ())} 条规则",
                "detail": info.get("grading") or [],
                "options": [],
                "actions": [],
                "note": "本页只呈现当前清单；清单整体替换不在本页范围",
            },
        ]
        return ResultEnvelope.ok(
            self._gated_description(
                "setting_panel",
                slots={
                    "entries": entries,
                    "surface": "evolution",
                    "labels": {
                        "set": "应用",
                        "manual": "手动",
                        "collaborative": "协作",
                        "autonomous": "自主",
                    },
                },
                text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
                title="演进授权",
            )
        )

    def _apply_setting(self, face: Any, config_id: str, value: Any) -> dict[str, Any]:
        info = face.authorization()
        if not info.get("available"):
            raise ValueError(str(info.get("reason") or "演进授权面未接线"))
        if config_id == info.get("tier_config_id"):
            if value is None:
                raise ValueError("切换档位须给出 value")
            return face.set_authorization(tier=value)
        if config_id == info.get("grading_config_id"):
            if value is None:
                raise ValueError("替换风险分级清单须给出 value")
            return face.set_authorization(grading=value)
        raise ValueError(f"该设置面不认这个条目：{config_id!r}")
