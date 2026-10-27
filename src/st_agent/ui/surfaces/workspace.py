"""工作区：钉住物与重解析（[T-UI-013]）。"""

from __future__ import annotations

from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id

from st_agent.ui.surfaces.base import (
    SurfaceBase,
)


class WorkspaceSurface(SurfaceBase):
    """工作区：钉住物与重解析（[T-UI-013]）。"""

    def api_workspace_pins(self) -> dict[str, Any]:
        """工作区读面：钉住物按**新钉在前**排的描述序列。

        空工作区是 ``empty`` ＋ 原因（[01 §5]：不得返回裸空），不是 ``ok`` 空数组——两者
        在渲染面上是「还没钉过东西」与「钉过但都取不出来」的区别。
        """
        return self._call_face(
            "workspace-pins", self.workspace, "工作区面", self._workspace_pins_envelope
        )

    def api_workspace_pin(self, body: dict[str, Any]) -> dict[str, Any]:
        """钉住一份描述（同 `description_id` 重复钉住 ＝ 覆盖，即刷新落盘的那一半）。"""
        return self._call_face(
            "workspace-pin", self.workspace, "工作区面",
            lambda face: self._gate_single_description(face.pin(body)),
        )

    def api_workspace_unpin(self, body: dict[str, Any]) -> dict[str, Any]:
        """取消钉住（按 `description_id`，[01 §1] 的该 id 即寻址单位）。"""
        return self._call_face(
            "workspace-unpin", self.workspace, "工作区面", lambda face: face.unpin(body)
        )

    def api_workspace_refresh(self, body: dict[str, Any]) -> dict[str, Any]:
        """重解析一条钉住物（来源面键 → 组合根注入的取数口）；见 [`WorkspaceFacade`]。

        三种如实降级（无来源面键 / 该键未接取数口 / 来源自己非 `ok`）都由面给出
        ``unavailable`` ＋ 原因，本层只把它**原样**送到渲染面（[01 §5] 六态不混用）。
        """
        return self._call_face(
            "workspace-refresh", self.workspace, "工作区面",
            lambda face: self._gate_single_description(face.refresh(body)),
        )

    def _workspace_pins_envelope(self, face: Any) -> ResultEnvelope:
        """逐条过闸（必填槽 ＋ 渲染前中性门）——序列里任何一条不过即整面 `validation_failed`。

        不逐条降级为「坏的那条不出、好的照出」：一条不过闸说明**产出方**有问题，
        静默漏掉它会让工作区看起来「本来就少一块」。
        """
        envelope = face.pins()
        if envelope.status != "ok":
            return envelope
        gated = [self._gate_description(item) for item in envelope.data]
        return envelope.model_copy(update={"data": gated})

    def _gate_single_description(self, envelope: ResultEnvelope) -> ResultEnvelope:
        """`ok` 且载荷是**单份**描述时过闸；非 `ok` 原样出（六态由面给，不吞）。"""
        if envelope.status != "ok" or not isinstance(envelope.data, UiDescription):
            return envelope
        return envelope.model_copy(update={"data": self._gate_description(envelope.data)})
