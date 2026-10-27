"""生态面：导出、导入校验、索引与越界告警（[T-UI-004.4]）。"""

from __future__ import annotations

from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id
from st_agent.ui.tables import Column, table_slots, table_text_kinds

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _now,
    _required,
)


_INBOX_COLUMNS = (
    Column("file_name", "文件名"),
    Column("size", "大小（字节）", kind="number", digits=0),
)
"""导入收件目录的表。"""


_INDEX_COLUMNS = (
    Column("kind", "类别"),
    Column("name", "名称"),
    Column("version", "版本"),
    Column("description", "说明"),
    Column("download_url", "下载地址"),
    Column("checksum", "校验和"),
)
"""官方 Skill 索引的表（[09 §6]）。"""


_IMPORT_COLUMNS = (
    Column("import_id", "留痕"),
    Column("kind", "种类"),
    Column("installed_id", "装入标识"),
    Column("sharer", "分享者"),
    Column("imported_at", "导入时间"),
)
"""导入留痕的表（[09 §7] 的来源追溯）。"""


_VIOLATION_COLUMNS = (
    Column("skill_id", "能力"),
    Column("violation", "越界类别"),
    Column("occurred_at", "时刻"),
    Column("trace_id", "关联推理链"),
    Column("disabled", "已禁用"),
)
"""越界行为留痕的表（[09 §3]）。"""


class EcoSurface(SurfaceBase):
    """生态面：导出、导入校验、索引与越界告警（[T-UI-004.4]）。"""

    def api_eco_export_plan(self) -> dict[str, Any]:
        """导出计划的「本次导出包含以下公开信息」清单（`report_card`）。"""
        return self._call_face(
            "eco-export-plan", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(self._export_plan_description(face)),
        )

    def api_eco_export(self, body: dict[str, Any]) -> dict[str, Any]:
        """按种类导出到**导出目录**并回路径 + 分享卡片（[09 §2]）。"""
        return self._call_face(
            "eco-export", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(face.export(
                kind=str(_required(body, "kind")),
                ref=str(body.get("ref") or ""),
                author=str(body.get("author") or ""),
                confirmed_by=str(body.get("confirmed_by") or ""),
            )),
        )

    def api_eco_inbox(self) -> dict[str, Any]:
        """收件目录里的待导入文件（`table`；空即空态，不硬凑）。"""
        return self._call_face(
            "eco-inbox", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(self._inbox_description(face)),
        )

    def api_eco_import_review(self, body: dict[str, Any]) -> dict[str, Any]:
        """导入校验：身份 / 权限申请 / 依赖 / 来源追溯**四段并列**（`report_card`）。"""
        file_name = str(_required(body, "file_name"))
        return self._call_face(
            "eco-import-review", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(
                self._review_description(face, file_name, str(body.get("received_from") or ""))
            ),
        )

    def api_eco_import_permissions(self, body: dict[str, Any]) -> dict[str, Any]:
        """逐项批准 / 拒绝的处置面（`setting_panel`，面键 `import`）——[01 §10] 逐项、不合并。"""
        file_name = str(_required(body, "file_name"))
        received_from = str(body.get("received_from") or "")
        return self._call_face(
            "eco-import-permissions", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(
                self._permissions_description(face, file_name, received_from)
            ),
        )

    def api_eco_import_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        """批准 / 拒绝一条已声明的权限（经 **L3 审批面**落账，[01 §10]）。"""
        return self._call_face(
            "eco-import-decide", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(face.decide_import(
                file_name=str(_required(body, "file_name")),
                permission=str(_required(body, "permission")),
                decision=str(_required(body, "action")),
            )),
        )

    def api_eco_import_install(self, body: dict[str, Any]) -> dict[str, Any]:
        """用户确认后安装（[09 §3] 第 4–5 段）——权限声明未全部批准即**显式拒**。"""
        file_name = str(_required(body, "file_name"))
        return self._call_face(
            "eco-import-install", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(face.install(
                file_name=file_name,
                confirmed_by=str(body.get("confirmed_by") or ""),
                received_from=str(body.get("received_from") or ""),
            )),
        )

    def api_eco_index(self) -> dict[str, Any]:
        """官方 Skill 索引浏览（`table`）；不可用即 `unavailable`（不留半截数据）。"""
        return self._call_face(
            "eco-index", self.eco, "ECO 生态面",
            lambda face: self._index_envelope(face),
        )

    def api_eco_imports(self) -> dict[str, Any]:
        """来源追溯：导入留痕（`table`）；**没有留痕即 `empty`**（用既有中性文本，不硬凑空表）。"""
        return self._call_face(
            "eco-imports", self.eco, "ECO 生态面",
            lambda face: self._imports_envelope(face),
        )

    def api_eco_violations(self) -> dict[str, Any]:
        """越界行为警示（`violation_alert`）——取**最新一条**，附「禁用该能力」动作。"""
        return self._call_face(
            "eco-violations", self.eco, "ECO 生态面",
            lambda face: self._violation_envelope(face),
        )

    def api_eco_violation_history(self) -> dict[str, Any]:
        """越界留痕全表（`table`）——警示之外仍可复核全部记录。"""
        return self._call_face(
            "eco-violation-history", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(self._violation_history_description(face)),
        )

    def api_eco_disable(self, body: dict[str, Any]) -> dict[str, Any]:
        """禁用越界能力（[03 §1.5] 的禁用面）。"""
        return self._call_face(
            "eco-disable", self.eco, "ECO 生态面",
            lambda face: ResultEnvelope.ok(
                face.disable(skill_id=str(_required(body, "skill_id")))
            ),
        )

    def _export_plan_description(self, face: Any) -> UiDescription:
        plan = face.export_plan()
        lines = [f"包含：{item}" for item in plan.get("included") or []]
        if not lines:
            lines = ["本次导出没有可公开的信息"]
        return self._gated_description(
            "report_card",
            slots={"sections": [
                {"title": "本次导出包含以下公开信息", "lines": lines},
                {
                    "title": "隐私过滤",
                    "lines": [f"已过滤（未包含）的节点数：{plan.get('excluded_nodes', 0)}"],
                },
                {
                    "title": "导出位置",
                    "lines": [f"生成的文件落在：{plan.get('exports_dir', '')}（09 §6 的表现层口径）"],
                },
            ]},
            text_kinds={"sections": "data"},
            title="导出记忆片段",
        )

    def _inbox_description(self, face: Any) -> UiDescription:
        files = face.inbox()
        return self._gated_description(
            "table",
            slots=table_slots(
                _INBOX_COLUMNS,
                [
                    {"file_name": item.get("file_name"), "size": item.get("size")}
                    for item in files
                ],
            ),
            text_kinds=table_text_kinds(),
            title="导入收件目录",
        )

    def _review_description(self, face: Any, file_name: str, received_from: str) -> UiDescription:
        review = face.review(file_name=file_name, received_from=received_from)
        sections = [{"title": "能力", "lines": [str(review.get("identity") or file_name)]}]
        permissions = review.get("permissions") or []
        if permissions:
            sections.append({
                "title": "权限申请（逐项批准，01 §10）",
                "lines": [
                    f"{item.get('permission')}｜{item.get('state')}｜{item.get('description')}"
                    for item in permissions
                ],
            })
        else:
            sections.append({"title": "权限申请", "lines": ["无声明（不经本机的文件 / 网络 / 命令出口）"]})
        missing = review.get("missing") or []
        sections.append({
            "title": "依赖",
            "lines": (
                [f"缺失 {gap.get('skill_id')} —— {gap.get('how_to_get')}" for gap in missing]
                or ["本地齐备，无缺失"]
            ),
        })
        provenance = review.get("provenance") or {}
        chain = " → ".join(provenance.get("origin_chain") or []) or "（无，未经转手）"
        sections.append({
            "title": "来源追溯",
            "lines": [
                f"分享者 {provenance.get('sharer')}｜导入时间 {provenance.get('imported_at')}"
                f"｜校验和 {provenance.get('checksum')}｜出处链 {chain}"
            ],
        })
        return self._gated_description(
            "report_card",
            slots={"sections": sections},
            text_kinds={"sections": "data"},
            title=f"导入校验 · {file_name}",
        )

    def _permissions_description(
        self, face: Any, file_name: str, received_from: str = ""
    ) -> UiDescription:
        review = face.review(file_name=file_name, received_from=received_from)
        items = review.get("permissions") or []
        entries = [
            {
                "identifier": item.get("permission"),
                "params": {"file_name": file_name, "permission": item.get("permission")},
                "description": item.get("description"),
                "current": item.get("state"),
                "options": [],
                "actions": ["approve", "reject"],
            }
            for item in items
        ]
        return self._gated_description(
            "setting_panel",
            slots={
                "entries": entries,
                "surface": "import",
                "labels": {"approve": "批准", "reject": "拒绝"},
            },
            text_kinds={"entries": "data", "surface": "data", "labels": "generated"},
            title=f"权限申请（逐项批准）· {file_name}",
        )

    def _index_envelope(self, face: Any) -> ResultEnvelope:
        index = face.index()
        if not index.get("available"):
            return ResultEnvelope.unavailable(
                str(index.get("reason") or "官方索引不可用"), last_updated_at=_now()
            )
        entries = index.get("entries") or []
        return ResultEnvelope.ok(
            self._gated_description(
                "table",
                slots=table_slots(
                    _INDEX_COLUMNS,
                    [
                        {
                            "kind": item.get("kind"),
                            "name": item.get("name"),
                            "version": item.get("version"),
                            "description": item.get("description"),
                            "download_url": item.get("download_url"),
                            "checksum": item.get("checksum"),
                        }
                        for item in entries
                    ],
                ),
                text_kinds=table_text_kinds(),
                title="官方 Skill 索引",
            )
        )

    def _imports_envelope(self, face: Any) -> ResultEnvelope:
        """导入留痕：**空即空态**（`table` 渲染件不显示描述标题，故空态不能用标题承载）。

        空态文案沿用 L1 的中性文本（「你的 Skill 库目前只有官方 Pack…」，story-10 的空状态）。
        """
        ledger = face.imports_ledger()
        records = ledger.get("records") or []
        if not records:
            return ResultEnvelope.empty(str(ledger.get("empty_text") or "尚无导入留痕"))
        return ResultEnvelope.ok(self._imports_description(records))

    def _imports_description(self, records: list[dict[str, Any]]) -> UiDescription:
        return self._gated_description(
            "table",
            slots=table_slots(
                _IMPORT_COLUMNS,
                [
                    {
                        "import_id": item.get("import_id"),
                        "kind": item.get("kind"),
                        "installed_id": item.get("installed_id"),
                        "sharer": (item.get("origin") or {}).get("sharer"),
                        "imported_at": (item.get("origin") or {}).get("imported_at"),
                    }
                    for item in records
                ],
            ),
            text_kinds=table_text_kinds(),
            title="导入留痕",
        )

    def _violation_envelope(self, face: Any) -> ResultEnvelope:
        info = face.violations()
        if not info.get("available"):
            return ResultEnvelope.unavailable(
                str(info.get("reason") or "越界行为面未接线"), last_updated_at=_now()
            )
        records = info.get("records") or []
        if not records:
            return ResultEnvelope.empty("尚无越界行为记录")
        newest = dict(records[0])
        newest["disabled"] = newest.get("skill_id") in set(info.get("disabled") or ())
        return ResultEnvelope.ok(
            self._gated_description(
                "violation_alert",
                slots={
                    "record": newest,
                    "labels": {
                        "warning": "该能力的行为超出其声明的范围，已被拦截。",
                        "disable": "禁用该能力",
                    },
                },
                text_kinds={"record": "data", "labels": "generated"},
                title="越界行为警示",
            )
        )

    def _violation_history_description(self, face: Any) -> UiDescription:
        info = face.violations()
        records = info.get("records") or []
        disabled = set(info.get("disabled") or ())
        return self._gated_description(
            "table",
            slots=table_slots(
                _VIOLATION_COLUMNS,
                [
                    {
                        "skill_id": item.get("skill_id"),
                        "violation": item.get("violation"),
                        "occurred_at": item.get("occurred_at"),
                        "trace_id": item.get("trace_id"),
                        "disabled": "是" if item.get("skill_id") in disabled else "否",
                    }
                    for item in records
                ],
            ),
            text_kinds=table_text_kinds(),
            title="越界行为留痕",
        )
