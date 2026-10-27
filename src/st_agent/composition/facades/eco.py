"""生态门面：导出、导入校验、索引与越界记录。"""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Any
from st_agent.eco import (
    ECOSYSTEM_BOUNDARY,
    EcoError,
    EMPTY_STATE_TEXT,
    IMPORT_CONFIRMATION,
    SHARE_KINDS,
    OfficialIndex,
    ShareExporter,
    ShareImporter,
)
from st_agent.l1.skills.ids import parse_skill_id
from st_agent.l3.approval import ApprovalRequest, CapabilityApprovalPanel


class _ViolationRecorder:
    """越界行为（`BehaviorViolation`）的**表现层取数面**：订阅留痕、按需拉取。

    [01 §11] 明写「**跨进程**（常驻后端 → 可分离 UI 客户端）的推送是表现层消费面的事，
    **不在本口径内**」——故本批取**拉取式**（进程内记最近若干条，表现层按需取），
    不引入推式通道（SSE / 长轮询）。跨进程推送仍待。

    未接订阅面时 :attr:`attached` 为假——表现层据此**如实标注未受理 + 点名**（不假装送达）。
    """

    EVENT = "BehaviorViolation"

    def __init__(self, *, limit: int = 50, failures: list | None = None) -> None:
        self._records: deque[dict[str, Any]] = deque(maxlen=limit)
        self._failures = failures if failures is not None else []
        self.subscription: tuple[Any, ...] = ()

    @property
    def attached(self) -> bool:
        return bool(self.subscription)

    def attach(self, events: Any, *, subscriber_id: str = "m4:violation-recorder") -> tuple[Any, ...]:
        """把本面订阅到总线（[01 §11] 的 `BehaviorViolation`）。"""
        self.subscription = (events.subscribe(self.EVENT, self._handle, subscriber_id=subscriber_id),)
        return self.subscription

    def _handle(self, event: Any) -> None:
        payload = dict(getattr(event, "payload", None) or {})
        self._records.append({
            "skill_id": str(payload.get("skill_id") or ""),
            "violation": str(payload.get("violation") or ""),
            "trace_id": str(getattr(event, "trace_id", None) or ""),
            "occurred_at": str(getattr(event, "occurred_at", None) or ""),
        })

    def records(self) -> tuple[dict[str, Any], ...]:
        """已记下的越界（新者在前）——**留痕即事实**，不重排、不合并。"""
        return tuple(reversed(self._records))


class EcoFacade:
    """ECO 生态面的**表现层适配面**（组合根所有；`ui` 只经鸭子端口消费，[`T-UI-004.4`]）。

    与 :class:`ReflectionFacade` 同口径：取数转 JSON 就绪结构 · **输入类失败就地转 `ValueError`** ·
    子面未接线回 ``{"available": False, "reason": …}``。

    **文件面**（[09 §6] 的表现层口径）：导出 / 导入**只在两处目录内**读写——导出物落
    `exports_dir`、待导入文件放进 `inbox_dir`。本面**不提供**「按任意路径读 / 写」的原语：
    文件名一律按**单段名**校验，路径分隔符 / 绝对路径 / 上跳一律拒。
    """

    def __init__(
        self,
        *,
        eco: Any,
        approvals: Any,
        violations: Any,
        sandbox: Any,
        exports_dir: Path,
        inbox_dir: Path,
    ) -> None:
        self._eco = eco
        self._approvals = approvals
        self._violations = violations
        self._sandbox = sandbox
        self._exports_dir = Path(exports_dir)
        self._inbox_dir = Path(inbox_dir)

    # ── 导出（[09 §2] 的三步：清单 → 用户确认 → 生成） ────────────────────────
    def export_plan(self) -> dict[str, Any]:
        """「本次导出包含以下公开信息」清单——**直接取 L2 的计划**，本面不自行筛。"""
        plan = self._eco.exporter.plan_memory()
        return {
            "included": list(plan.included_summary),
            "excluded_nodes": int(plan.excluded_nodes),
            "confirmed_by_required": IMPORT_CONFIRMATION,
            "exports_dir": str(self._exports_dir),
        }

    def export(
        self, *, kind: str, ref: str = "", author: str = "", confirmed_by: str = ""
    ) -> dict[str, Any]:
        """按 `kind` 导出到**导出目录**，回路径 + 分享卡片。"""
        if kind not in SHARE_KINDS:
            raise ValueError(f"分享物种类须为 {'/'.join(SHARE_KINDS)}，收到 {kind!r}")
        if not str(author).strip():
            raise ValueError("导出须给出 author（manifest 的作者声明，09 §1）")
        exporter = self._eco.exporter
        if kind == "mem":
            if confirmed_by != IMPORT_CONFIRMATION:
                raise ValueError(
                    "导出记忆片段须由用户确认（三步的第 3 步，09 §2）——未确认即不生成文件"
                )
            container = exporter.export_memory(author=author, confirmed_by=confirmed_by)
        else:
            if not str(ref).strip():
                raise ValueError(f"导出 {kind} 须给出 ref（其 skill_id / flow_id / lens_id）")
            container = {
                "skill": exporter.export_skill,
                "flow": exporter.export_flow,
                "lens": exporter.export_lens,
            }[kind](str(ref), author=author)
        card = exporter.card_for(container)
        target = container.save_to(self._exports_dir / card.file_name)
        return {
            "kind": kind,
            "path": str(target),
            "checksum": card.checksum,
            "card": card.model_dump(mode="json"),
        }

    # ── 导入（[09 §3] 的五段） ────────────────────────────────────────────────
    def inbox(self) -> list[dict[str, Any]]:
        """收件目录里的待导入文件（只列文件名与大小，**不读内容**）。"""
        if not self._inbox_dir.is_dir():
            return []
        return [
            {"file_name": path.name, "size": path.stat().st_size}
            for path in sorted(self._inbox_dir.iterdir())
            if path.is_file()
        ]

    def review(self, *, file_name: str, received_from: str = "") -> dict[str, Any]:
        """格式校验 + 依赖解析 + 权限登记后的审核面（[09 §3] 的第 1–3 段，**尚未安装**）。"""
        plan = self._plan(file_name, received_from=received_from)
        return {
            "file_name": file_name,
            "kind": plan.kind,
            "identity": plan.identity(),
            "wants": list(plan.what_it_wants()),
            "permissions": self._approval_items(plan),
            "missing": [gap.model_dump(mode="json") for gap in plan.missing],
            "provenance": plan.provenance.model_dump(mode="json"),
        }

    def decide_import(self, *, file_name: str, permission: str, decision: str = "approve") -> dict[str, Any]:
        """逐项批准 / 拒绝一条已声明的权限（[01 §10] 的逐项批准，经 L3 审批面落账）。"""
        if decision not in ("approve", "reject"):
            raise ValueError(f"决定须为 approve / reject，收到 {decision!r}")
        plan = self._plan(file_name)
        if not plan.permissions:
            raise ValueError("该导入物未声明任何权限，无可批准项")
        if permission not in plan.permissions:
            raise ValueError(f"该导入物未声明这条权限：{permission!r}")
        request = self._approval_request(plan)
        envelope = (
            self._approvals.approve(request, permission)
            if decision == "approve"
            else self._approvals.reject(request, permission)
        )
        if envelope.status != "ok":
            raise ValueError(envelope.reason or "权限审批面未受理本次决定")
        return {
            "permission": permission,
            "decision": decision,
            "permissions": self._approval_items(plan),
        }

    def install(
        self, *, file_name: str, confirmed_by: str = "", received_from: str = ""
    ) -> dict[str, Any]:
        """用户确认后安装（[09 §3] 第 4–5 段）——`.stskill` 另需权限声明**全部已批准**。"""
        if confirmed_by != IMPORT_CONFIRMATION:
            raise ValueError("安装须由用户确认（09 §3 第 4 段）")
        plan = self._plan(file_name, received_from=received_from)
        try:
            outcome = self._eco.importer.install(plan, confirmed_by=confirmed_by)
        except EcoError as exc:
            raise ValueError(f"安装未通过：{exc}") from exc
        return outcome.model_dump(mode="json")

    def imports_ledger(self) -> dict[str, Any]:
        """来源追溯：全部导入留痕（**空即空态**，用 L1 的中性文本，不硬凑列表）。"""
        records = [item.model_dump(mode="json") for item in self._eco.importer.ledger_records()]
        return {"records": records, "empty_text": "" if records else EMPTY_STATE_TEXT}

    # ── 官方索引（[09 §4]） ───────────────────────────────────────────────────
    def index(self) -> dict[str, Any]:
        """只读浏览；**不可用即如实不可用**（离线 / 未配端点），不留半截数据。"""
        try:
            entries = self._eco.index.browse()
        except Exception as exc:  # noqa: BLE001 —— 未配端点 / 离线：如实记因，不吞成空列表
            return {"available": False, "reason": f"官方 Skill 索引不可用：{exc}"}
        return {
            "available": True,
            "entries": [entry.model_dump(mode="json") for entry in entries],
            "boundary": ECOSYSTEM_BOUNDARY,
        }

    # ── 越界行为警示（[09 §3] 的运行时防护） ──────────────────────────────────
    def violations(self) -> dict[str, Any]:
        if self._violations is None:
            return {
                "available": False,
                "reason": "未接入越界行为的订阅面（装配归组合根）",
            }
        return {
            "available": True,
            "attached": bool(self._violations.attached),
            "records": list(self._violations.records()),
            "disabled": list(self._sandbox.disabled_skills()),
        }

    def disable(self, *, skill_id: str) -> dict[str, Any]:
        """禁用越界能力（[03 §1.5] 的禁用面）——写面在沙箱，本面只转交。"""
        if not str(skill_id).strip():
            raise ValueError("须给出要禁用的 skill_id")
        self._sandbox.disable(str(skill_id))
        return {"skill_id": skill_id, "disabled": True}

    # ── 内部工具 ─────────────────────────────────────────────────────────────
    def _plan(self, file_name: str, *, received_from: str = "") -> Any:
        path = self._inbox_path(file_name)
        try:
            blob = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"收件目录里读不到该文件：{file_name!r}") from exc
        try:
            return self._eco.importer.inspect(blob, received_from=received_from or None)
        except EcoError as exc:
            # 生态面的自有失败都源于**输入**（文件形态 / 缺来源 / 版本不兼容）⇒ 转成输入类失败
            raise ValueError(f"导入校验未通过：{exc}") from exc

    def _inbox_path(self, file_name: str) -> Path:
        """**只在收件目录内**解析文件名（单段名；分隔符 / 绝对路径 / 上跳一律拒）。"""
        if not isinstance(file_name, str) or not file_name.strip():
            raise ValueError("须给出收件目录里的文件名")
        if Path(file_name).name != file_name:
            raise ValueError(f"只接受收件目录内的文件名（不接受路径）：{file_name!r}")
        return self._inbox_dir / file_name

    def _approval_request(self, plan: Any) -> Any:
        payload = plan.container.payload
        skill_id = str(getattr(payload, "skill_id", "") or "")
        base, _version = parse_skill_id(skill_id)
        return ApprovalRequest(key=base, source="imported", permissions=plan.permissions)

    def _approval_items(self, plan: Any) -> list[dict[str, Any]]:
        """逐条权限的批准态（经 **L3 审批面**取，不自行读账本）。"""
        if not plan.permissions:
            return []
        envelope = self._approvals.open(self._approval_request(plan))
        if envelope.status != "ok":
            raise ValueError(envelope.reason or "权限审批面不可用")
        return [item.model_dump(mode="json") for item in envelope.data.items]
