"""表现层组合根：静态资产 + API 路由 + 请求守卫（[00 §1.1]；[D-060]）。

本模块只做**装配与传输**——不引入任何域逻辑。真实数据的接线留给需要它的任务：M1 骨架叶
只证明「管道通、六态对、鉴权硬」（[D-063] 的 M1 最小面口径，见任务假设 `A5`）。

dev 面的开关是**构建期**语义：``st_agent.ui.dev`` 子包在发布构建里被
``[tool.setuptools.packages.find] exclude`` **物理剔除**，剔除后 ``dev=True`` 必须
显式失败（:class:`~st_agent.ui.errors.DevSurfaceUnavailable`），而不是静默降级。
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from st_agent import __version__
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription, checked_description, new_description_id

from st_agent.ui.envelope import envelope_payload
from st_agent.ui.errors import DevSurfaceUnavailable
from st_agent.ui.neutrality_gate import NeutralityGate
from st_agent.ui.registry import slot_gaps
from st_agent.ui.security import RequestGuard, new_token, resolve_static
from st_agent.ui.tables import Column, table_slots, table_text_kinds

__all__ = ["DEV_SCRIPT_MARKER", "PAGE_PATHS", "UiApp", "WEB_ROOT", "build_ui"]

WEB_ROOT = Path(__file__).resolve().parent / "web"
"""生产静态资产根（无构建 ES modules）。"""

PAGE_PATHS = frozenset({
    "/workspace",
    "/memory",
    "/skills",
    "/mcp",
    "/deliberation",
    "/delivery",
    "/reflection",
    "/reflection/changes",
    "/reflection/experiments",
    "/reflection/training",
    "/settings",
    "/settings/evolution",
    "/eco",
    "/eco/import",
    "/eco/index",
    "/eco/imports",
    "/eco/security",
    "/studio",
})
"""已知**页面路径**——它们回同一静态壳（SPA 回退），与 `ui/web/js/pages.js` 的登记表同源。

本集合＝[11-sitemap §2.2]（[`T-UI-010.2`] 冻结的权威口径）声明的**全部 19 条路径**去掉
主入口 `/`（根路径在 `server.py` 里与 `/index.html` 同处判定，不进本集合）——两端由
`tests/ui/test_ui_shell_routing.py` 的漂移断言逐项钉住。

页面路径属**静态壳档**（只校 `Host` / 存在的 `Origin`），故首次导航不必带令牌
（[D-063] 的令牌分档：令牌只压 `/api/*`）；用户数据的取数一律在 `/api/*` 之后。
**未知路径仍 404**——不把任意路径都当页面（否则静态壳会吞掉资产缺失的错误）。
"""

DEV_SCRIPT_MARKER = "<!--ST_DEV_SCRIPT-->"
"""`index.html` 里的 dev 脚本占位：dev 关闭时被替换为空串，故**发布产物不含 dev 引用**。"""

_VERSION = __version__
"""与 `pyproject.toml` 的 `project.version` 一致——单一真相源＝包根 `st_agent.__version__`。"""

_LOG = logging.getLogger("st_agent.ui.app")


def _now() -> datetime:
    """带时区的当前时刻（01 §8：内部传输时间锚点带时区）。"""
    return datetime.now().astimezone()


def _load_dev() -> Any | None:
    """尝试加载 dev 子包；发布构建里它不存在，返回 ``None``。"""
    try:
        from st_agent.ui import dev  # noqa: PLC0415
    except ImportError:
        return None
    return dev


def _required(body: Mapping[str, Any], key: str) -> Any:
    """取请求体的必填项；缺失 / 空白即 ``ValueError``（表现层据此回 `validation_failed`）。"""
    value = body.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValueError(f"请求体缺少必填项 {key!r}")
    return value


def _studio_envelope(payload: dict[str, Any], label: str) -> Any:
    """Studio 面的返回 → 信封：`available: false` ⇒ `unavailable` + 点名；否则 `ok`。"""
    if not payload.get("available"):
        return ResultEnvelope.unavailable(
            payload.get("reason") or f"未接入{label}面（装配归组合根）",
            last_updated_at=_now(),
        )
    return ResultEnvelope.ok(payload)


def _compact(value: Any) -> Any:
    """表格单元格的取值：容器压成一行 JSON、其余原样（**不臆造摘要**）。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _anchor_text(item: Any) -> str:
    """一个留痕锚点的展示文本（证据引用是 ``{kind, ref}`` 对象，其余为 ID 字符串）。"""
    if isinstance(item, Mapping):
        return f"{item.get('kind')}:{item.get('ref')}"
    return str(item)


_FEEDBACK_ACTIONS = ("adopted", "ignored", "rejected", "queried", "liked")
"""反馈的五类动作（[08 §1]；与 L3 采集面的 `ACTIONS` 同序同集）。"""

_FEEDBACK_REASON_REQUIRED = ("rejected",)
"""其中**必填原因**的一类——「否定 + 说明原因」是 story-09 的原始要求。"""

_FEEDBACK_LABELS = {
    "adopted": "采纳",
    "ignored": "忽略",
    "rejected": "否定",
    "queried": "追问",
    "liked": "点赞",
}
"""动作键 → 中性中文标签（**本层生成文案**，过 [01 §6] 执行点 2）。"""


_STUDIO_LABELS = {
    "accept": "接受",
    "reject": "否决",
    "edit": "编辑",
    "add_node": "添加节点",
    "remove_node": "删除节点",
    "connect": "连线",
    "disconnect": "断线",
    "group": "建分组",
    "ungroup": "解散分组",
}
"""Studio 画布的控件文案（**本层生成文案**，过 [01 §6] 执行点 2；[`T-UI-005.1`]）。

动作键与组合根 `_CANVAS_EDIT_OPS` / `_STUDIO_ACTIONS` 同集——**动作不进描述**（01 §12）：
描述只放数据与标签，发起写请求的路由（`/api/studio/edit` 等）是渲染件已知的固定回环路由。
"""

_STUDIO_CANVAS_TITLE = "Studio 画布"
"""画布描述标题（生成文案，过 [01 §6] 执行点 2）。"""

# ── 各表的列（`table` 槽的**唯一漏斗**在 `ui/tables.py`，形状与呈现角色见其模块文档） ──
#
# 列名是**生成文案**（过 [01 §6] 执行点 2）；表要用 `table_slots` / `table_text_kinds` 出，
# 不在这里手拼 slots——手拼正是 2026-10-09 那处「产出方与渲染件形状不一致、六张表全空」的成因
# （[D-105] ①）。

_EXPERIMENT_COLUMNS = (
    Column("hypothesis", "假设"),
    Column("scope", "范围"),
    Column("sample", "样本"),
    Column("result", "结果"),
    Column("decision", "决策"),
    Column("status", "状态"),
)
"""A/B 实验日志的表（[08 §4]）。"""

_TRAINING_COLUMNS = (
    Column("training_id", "训练 id"),
    Column("correction", "用户的修正（原话）"),
    Column("restatement", "复述"),
    Column("pattern", "模式"),
    Column("confirmed", "已确认"),
    Column("created_at", "时刻"),
)
"""训练对话留痕的表（[08 §1]）；`correction` 是**用户原话**，整行按 `data` 原样呈现（[D-053]）。"""

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

_POSITION_COLUMNS = (
    Column("code", "代码"),
    Column("code_name", "名称"),
    Column("close", "收盘", kind="number"),
    Column("pct_chg", "涨跌幅", kind="direction"),
    Column("turn", "换手率", kind="number"),
)
"""记忆区的**持仓 / 关注行情读面**（[`T-UI-009.2`]）。

`pct_chg` 是 `direction` 列——**三路冗余编码（颜色 + ▲▼ + 带符号数值）由渲染件一次产出**
（[13-visual-design §2.2]），本层只给**数值**；某标的在本地缓存里没有日线时给 `None`，
由渲染面出「无数据」，**不**冒充 0、**不**读作「平」。"""

_POSITION_SCOPES: dict[str, tuple[str, str]] = {
    "holdings": ("持仓", "记忆中没有持仓信息"),
    "watchlist": ("关注池", "记忆中没有关注池信息"),
}
"""两段的**面键** →（段名 · 空态原因）。原因措辞与 [05 §2] 上下文卡片的同段一致：
空是邀请（说清空的原因），不是留白。"""


def _training_row(item: Mapping[str, Any]) -> dict[str, Any]:
    """训练留痕的一行（`session` 段可能缺席，逐字段取值、不抛）。"""
    session = item.get("session") or {}
    return {
        "training_id": session.get("training_id"),
        "correction": session.get("correction"),
        "restatement": session.get("restatement"),
        "pattern": _compact(session.get("pattern")),
        "confirmed": "是" if session.get("confirmed") else "否",
        "created_at": session.get("created_at"),
    }


def _as_datetime(value: Any) -> datetime | None:
    """把 ISO 串还原成**带时区**的 ``datetime``（[01 §8]：`as_of` 须带时区语义）。"""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


@dataclass(frozen=True)
class UiApp:
    """一次服务进程的装配结果（不可变；由 :func:`build_ui` 构造）。"""

    guard: RequestGuard
    dev: bool = False
    web_root: Path = WEB_ROOT
    dev_package: Any = None
    neutrality_gate: NeutralityGate = field(default_factory=NeutralityGate)
    chat: Any = None
    """对话门面（鸭子类型 ``turn(body) -> dict``）；缺省 ``None`` → 对话端点 fail-closed。

    由 M1 关卡的组合根注入（`ui` **不 import** `st_agent.app`，只经此鸭子端口消费，
    否则破 `test_ui_is_client_only`；见 [T-INT-002] 假设 A2）。
    """

    reflection: Any = None
    """L6 反思演进栈（鸭子端口；缺省 ``None`` → 反思 / 演进而端点 fail-closed）。

    同一端口服务 `/api/reflection/*` 与 `/api/evolution/*`——两者同属 L6（[08-L6] 的
    反思面与演进面）。由生产入口注入（`ui` 不 import `l6` 的类型，也不 import `app`）。
    """

    eco: Any = None
    """生态面（鸭子端口：`exporter` / `importer` / `index`）；缺省 ``None`` → 生态面 fail-closed。"""

    memory: Any = None
    """记忆区读面（鸭子端口：``positions(scope) -> dict``）；缺省 ``None`` → 记忆区 fail-closed。

    **与其余三端口同一形**：由组合根注入适配面（`MemoryPositionsFacade`），`ui` 不 import
    `l2` / `l0` 的类型。注意它与「面在、但行情源没注入」是**两回事**——后者是面自己回的
    ``available=False``（表现为 `unavailable` + 原因并有最后更新时间），本条只管「面本身没接」。
    """

    # ── M6 各面（七个新鸭子端口；[`T-UI-011.1`]） ──────────────────────────
    # 每个端口由组合根注入同名薄门面（[`st_agent.app`]），`ui` 不 import 任何层类型；
    # 缺省 `None` ⇒ 该面探针 fail-closed（`unavailable` + 点名，不 500、不装空表）。
    # **端口名 = 面名**，故 `api_face_status("skills")` 的 `getattr` 直接可用。

    workspace: Any = None
    """工作区（钉住的持久组件）面；缺省 ``None`` → fail-closed。

    **本批刻意保持未接线**：钉住物的落盘与读面归 [`T-UI-013`]，本批不造空门面冒充它
    （[01 §5]：不拿空壳当「面在」）。故 `/api/workspace/status` 在真组合根上仍回
    `unavailable` + 点名——这正是「未接线」该有的样子。
    """

    skills: Any = None
    """L1 能力（Skill）面；缺省 ``None`` → fail-closed。"""

    mcp: Any = None
    """L1 能力（MCP）面；缺省 ``None`` → fail-closed。"""

    graph: Any = None
    """L2 记忆图谱面（服务既有 `/memory` 的三视图 / 画像）；缺省 ``None`` → fail-closed。"""

    deliberation: Any = None
    """L4 多视角推理面；缺省 ``None`` → fail-closed。"""

    delivery: Any = None
    """L5 主动触达面；缺省 ``None`` → fail-closed。"""

    settings: Any = None
    """L0 存储 / 配置面；缺省 ``None`` → fail-closed。"""

    commands: Any = None
    """快捷指令注册表（鸭子端口：``list() -> tuple[QuickCommand, ...]``）；缺省 ``None`` → fail-closed。

    [05 §8](../../docs/技术架构-v2/05-L3-对话主入口.md) 的命令注册表由 L3 维持（含官方 Pack
    的 `command` kind 整组替换），故首页的 `/` 补全清单**经此端口取真注册表**、不在前端另造
    一份枚举。由组合根注入 `M1Runtime.commands`；`ui` 不 import `l3` 的类型。
    """

    @property
    def dev_enabled(self) -> bool:
        """dev 面是否**在本进程内真实可用**（开关打开 **且** 子包在盘上）。"""
        return self.dev and self.dev_package is not None

    def api_chat(self, body: dict[str, Any]) -> dict[str, Any]:
        """对话端点：经注入的门面走一段反向流，出站点仍过**同一条**校验。

        门面的返回含 `reply`（`ResultEnvelope`，附六态渲染语义）、可选
        `description`（`UiDescription`，走 :meth:`api_description` 的中性化门与必填槽）与可选
        `clarification`（澄清轮：问项 / 方向候选 / 超预算默认项，[05 §3.2]，原样透传）。
        未注入门面 → `unavailable` + 点名（不伪造，同 `api_health` 之外的各 fail-closed 面）。

        **本方法不向上抛**（[00 §6] 失败显式化）：非法请求体（门面在构造契约对象时
        拒绝，属 `ValueError` / `KeyError` 族）转 `validation_failed`；其余未预期异常
        记 traceback 后转 `failed`（附可查 `log_ref`）——两端都回信封，连接不被关闭。
        """
        if self.chat is None:
            return envelope_payload(
                ResultEnvelope.unavailable(
                    "未接入对话门面，/api/chat 不可用（装配归组合根）",
                    last_updated_at=_now(),
                )
            )
        try:
            result = self.chat.turn(body)
        except (ValueError, KeyError) as exc:
            # 请求体非法 → 门面构造契约对象时拒绝：pydantic 包装的 ValidationError 与 L3 各
            # *ValidationError 均属 ValueError；未知 session_id 属 SessionNotFoundError(KeyError)。
            _LOG.info("对话请求体被门面拒绝（%s）：%s", type(exc).__name__, exc)
            return envelope_payload(
                ResultEnvelope.validation_failed(
                    "对话请求体不合法，无法受理"
                    "（需非空 text；action=dispatch 需带待确认的 session_id）"
                )
            )
        except Exception as exc:  # noqa: BLE001 —— 真正的编程/内部错误：不吞，落日志 + 显式 failed
            log_ref = f"ui/chat-{uuid.uuid4().hex[:12]}"
            _LOG.exception("对话门面未预期异常（log_ref=%s）：%s", log_ref, exc)
            return envelope_payload(
                ResultEnvelope.failed("对话处理未预期失败，详见服务端日志", log_ref=log_ref)
            )
        reply = result.get("reply")
        payload = envelope_payload(reply) if isinstance(reply, ResultEnvelope) else {
            "status": "failed", "reason": "对话门面返回非法结构", "render": {},
        }
        payload["session_id"] = result.get("session_id")
        payload["needs_confirmation"] = bool(result.get("needs_confirmation"))
        payload["clarification"] = result.get("clarification")
        description = result.get("description")
        if description is not None:
            described = envelope_payload(ResultEnvelope.ok(description))
            gaps = slot_gaps(description)
            verdict = self.neutrality_gate.check(description)
            if gaps or not verdict.passed:
                payload["description"] = envelope_payload(
                    ResultEnvelope.validation_failed(
                        "、".join(gaps) if gaps else verdict.reason
                    )
                )
            else:
                payload["description"] = described
        else:
            payload["description"] = None
        return payload

    def api_chat_context(self) -> dict[str, Any]:
        """首页上下文卡片（[05 §2]）：经注入的对话门面取卡片描述（`context_card`）。

        门面缺席、或其未持 `context_card` 取数口 ⇒ `unavailable` + 点名（不伪造一张卡片）。
        卡片六段与非 ok 段的「原因 + 生产方」由 L3 的 `describe_context_card` 给出，表现层
        只转发（[01 §5] 六态不混用）。
        """
        face = self.chat
        if face is None or not hasattr(face, "context_card"):
            return envelope_payload(
                ResultEnvelope.unavailable(
                    "未接入上下文卡片面：对话门面或 context_card 取数口缺席"
                    "（装配归组合根）",
                    last_updated_at=_now(),
                )
            )
        return self._call_face(
            "chat-context", face, "上下文卡片面", lambda f: f.context_card(),
        )

    def api_chat_commands(self) -> dict[str, Any]:
        """快捷指令清单（[05 §8]）：经注入的指令注册表取条目（名称 / 说明 / 意图 / 来源）。

        注册表未接 ⇒ `unavailable` + 点名（不摆假清单、不前端硬编码）。指令名与说明在
        注册时已过中性门（[01 §6] 执行点 1），故此处只转发、不复检。
        """
        return self._call_face(
            "chat-commands", self.commands, "快捷指令注册表",
            lambda face: self._commands_envelope(face),
        )

    def _commands_envelope(self, registry: Any) -> ResultEnvelope:
        commands = tuple(registry.list())
        if not commands:
            return ResultEnvelope.empty("快捷指令注册表为空（无可用指令）")
        return ResultEnvelope.ok(
            [
                {
                    "name": command.name,
                    "description": command.description,
                    "intent": command.intent,
                    "source": command.source,
                }
                for command in commands
            ]
        )

    def api_health(self) -> dict[str, Any]:
        """健康面：恒为 `ok`——本叶不接真实数据（任务假设 `A5`）。"""
        return envelope_payload(
            ResultEnvelope.ok(
                {"service": "st-agent-ui", "version": _VERSION, "dev": self.dev_enabled}
            )
        )

    def api_face_status(self, face: str) -> dict[str, Any]:
        """面可用性探针：端口在 ⇒ `ok`；不在 ⇒ `unavailable` + 点名。

        表现层壳用它决定导航项是否可用。**「未接入」与「接入但无数据」是两回事**——
        前者是本进程没拿到该面的装配口（点名归属），后者由各面自己的读面给空态
        （[01 §5] 六态不可混用，同 [08 §4]「不由计数 0 反推」的口径）。
        """
        if getattr(self, face, None) is None:
            return envelope_payload(
                ResultEnvelope.unavailable(
                    f"未接入 {face} 面：该面端点在本次装配中不可用（装配归生产入口）",
                    last_updated_at=_now(),
                )
            )
        return envelope_payload(ResultEnvelope.ok({"face": face, "available": True}))

    # ───────────────────── 反思中心（[T-UI-004.2]） ─────────────────────

    def api_reflection_report(self, *, week: str = "") -> dict[str, Any]:
        """每周反思报告：四段式 `report_card`；两种空态**分开判定、皆不硬凑**（[08 §2]）。"""
        return self._call_face(
            "reflection-report", self.reflection, "L6 反思演进面",
            lambda face: self._report_envelope(face, week),
        )

    def api_reflection_trace(self, *, week: str = "") -> dict[str, Any]:
        """报告的**留痕锚点**（`trace_timeline`）——兑现「报告生成走完整 Trace」（[08 §2]）。"""
        return self._call_face(
            "reflection-trace", self.reflection, "L6 反思演进面",
            lambda face: self._trace_envelope(face, week),
        )

    def api_reflection_feedback_capture(self) -> dict[str, Any]:
        """反馈采集组件：对**本周这次触达**采集反馈（目标取自最近一期报告的投递留痕）。

        写面在 :meth:`api_reflection_feedback`；本端点只把「可反馈的对象」喂给组件——
        没有可反馈对象时给 `empty`，**不硬凑**一个假目标。
        """
        return self._call_face(
            "reflection-feedback-capture", self.reflection, "L6 反思演进面",
            lambda face: self._feedback_envelope(face),
        )

    def _feedback_envelope(self, face: Any) -> ResultEnvelope:
        report = self._latest_report(face, "")
        if isinstance(report, ResultEnvelope):
            return report
        deliveries = (report.get("trace") or {}).get("delivery_ids") or []
        if not deliveries:
            return ResultEnvelope.empty("本周没有可反馈的触达（报告无投递留痕）")
        return ResultEnvelope.ok(
            self._gated_description(
                "feedback_capture",
                slots={
                    "target": {"kind": "delivery", "ref": str(deliveries[-1])},
                    "actions": list(_FEEDBACK_ACTIONS),
                    "reason_required": list(_FEEDBACK_REASON_REQUIRED),
                    "labels": dict(_FEEDBACK_LABELS),
                    "prompt": "否定类反馈必须说明原因",
                },
                text_kinds={
                    "target": "data",
                    "actions": "data",
                    "reason_required": "data",
                    "labels": "generated",
                    "prompt": "generated",
                },
                title=f"对本周触达的反馈 · {report.get('week')}",
            )
        )

    def api_reflection_proposals(self) -> dict[str, Any]:
        """待处置的建议：待批准队列 + 最近一期周报的候选 + 主动提案（`proposal_card`）。"""
        return self._call_face(
            "reflection-proposals", self.reflection, "L6 反思演进面",
            lambda face: self._proposals_envelope(face),
        )

    def api_reflection_feedback(self, body: dict[str, Any]) -> dict[str, Any]:
        """反馈采集（写面）——**产生方是交互层**，本层只把请求转给注入的采集面（[01 §1]）。

        `feedback_id` 由采集面铸造、`rejected` 的 `reason` 由它校验必填；本层**不预判、
        也不落盘**（[05 §9]：L3 只产事件、反馈池归 L6）。
        """
        return self._call_face(
            "reflection-feedback", self.reflection, "L6 反思演进面",
            lambda face: face.record_feedback(
                target=_required(body, "target"),
                action=_required(body, "action"),
                reason=body.get("reason"),
                context=body.get("context"),
            ),
        )

    def api_reflection_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        """逐条处置提案（接受 / 否决 / 延后）——**唯一通道是变更流**（[08 §5] / §7 红线）。"""
        return self._call_face(
            "reflection-decide", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(
                face.decide(
                    action=_required(body, "action"),
                    proposal=body.get("proposal"),
                    pending_id=str(body.get("pending_id") or ""),
                )
            ),
        )

    def api_reflection_studio_handoff(self, body: dict[str, Any]) -> dict[str, Any]:
        """主动提案的 Skill 草稿**交 Studio**（落画布）——[08 §4]「衔接 story-06」。

        经组合根把 L6 的 `SkillDraft` 翻成 L1 的 `WorkflowDraft` 并转交 Studio 接收面；
        表现层**不 import** 各层类型。子面未接线 → `unavailable` + 点名；输入类失败 →
        `validation_failed`。
        """
        return self._call_face(
            "reflection-studio-handoff", self.reflection, "L6 反思演进面",
            lambda face: _studio_envelope(
                face.studio_handoff(str(_required(body, "proposal_id"))), "Studio",
            ),
        )

    def api_reflection_studio_decide(self, body: dict[str, Any]) -> dict[str, Any]:
        """处置一条**已交 Studio** 的提案：接受（落 v1.0）/ 否决——两个动作都委托 L1。"""
        return self._call_face(
            "reflection-studio-decide", self.reflection, "L6 反思演进面",
            lambda face: _studio_envelope(
                face.studio_decide(
                    proposal_id=str(_required(body, "proposal_id")),
                    action=str(_required(body, "action")),
                ),
                "Studio",
            ),
        )

    # ───────────────────── Studio 画布（[`T-UI-005.1`]） ─────────────────────

    def api_studio_sessions(self) -> dict[str, Any]:
        """已交 Studio 未处置的会话一览——画布页左栏（`{available, sessions}`）。"""
        return self._call_face(
            "studio-sessions", self.reflection, "L6 反思演进面",
            lambda face: _studio_envelope(face.studio_sessions(), "Studio"),
        )

    def api_studio_skills(self) -> dict[str, Any]:
        """画布页「加节点」的可选 Skill 清单（01 §2 描述体的投影；不新造第二份登记）。"""
        return self._call_face(
            "studio-skills", self.reflection, "L6 反思演进面",
            lambda face: _studio_envelope(face.studio_skills(), "Studio"),
        )

    def api_studio_canvas(self, *, proposal_id: str) -> dict[str, Any]:
        """某会话的画布——出站为 `studio_canvas` 描述（过必填槽 + 中性化两道闸）。"""
        return self._call_face(
            "studio-canvas", self.reflection, "L6 反思演进面",
            lambda face: self._studio_canvas_envelope(face.studio_canvas(proposal_id)),
        )

    def api_studio_edit(self, body: dict[str, Any]) -> dict[str, Any]:
        """一次画布结构编辑（增删节点 / 连线断线 / 建分组解散）——回 `studio_canvas` 描述，
        附本次 `edit` 小结（`applied` / `message` / `blocked_by`）与**编辑后的画布**。"""
        return self._call_face(
            "studio-edit", self.reflection, "L6 反思演进面",
            lambda face: self._studio_canvas_envelope(
                face.studio_edit(
                    proposal_id=str(_required(body, "proposal_id")),
                    op=str(_required(body, "op")),
                    args=body.get("args"),
                )
            ),
        )

    def _studio_canvas_envelope(self, payload: dict[str, Any]) -> Any:
        """画布载荷 → 信封：子面未接线 ⇒ `unavailable` + 点名；否则出 `studio_canvas` 描述。

        `canvas` 槽按 **data** 承载（节点 / 连线 / 分组是**数据展示**，含用户填的标识与
        绑定值，不整串复检）；`labels` 槽是本层生成文案，过 [01 §6] 执行点 2。
        """
        if not payload.get("available"):
            return ResultEnvelope.unavailable(
                payload.get("reason") or "未接入 Studio 草稿接收面（08 §4）",
                last_updated_at=_now(),
            )
        return ResultEnvelope.ok(
            self._gated_description(
                "studio_canvas",
                slots={"canvas": payload["canvas"], "labels": dict(_STUDIO_LABELS)},
                text_kinds={"canvas": "data", "labels": "generated"},
                title=_STUDIO_CANVAS_TITLE,
            )
        )

    def api_reflection_experiments(self) -> dict[str, Any]:
        """A/B 实验日志（`table`）——五字段 + 状态与决策；判定**保守**，不产 p 值（[08 §4]）。"""
        return self._call_face(
            "reflection-experiments", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(
                self._gated_description(
                    "table",
                    slots=table_slots(
                        _EXPERIMENT_COLUMNS,
                        [
                            {
                                "hypothesis": item.get("hypothesis"),
                                "scope": item.get("scope"),
                                "sample": _compact(item.get("sample")),
                                "result": _compact(item.get("result")),
                                "decision": item.get("decision"),
                                "status": item.get("status"),
                            }
                            for item in face.experiments()
                        ],
                    ),
                    text_kinds=table_text_kinds(),
                    title="A/B 实验日志",
                )
            ),
        )

    def api_reflection_training(self) -> dict[str, Any]:
        """训练会话与待回访项（`table`）——用户原话按 `data` **原样呈现**（[D-053]）。"""
        return self._call_face(
            "reflection-training", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(
                self._gated_description(
                    "table",
                    slots=table_slots(_TRAINING_COLUMNS, map(_training_row, face.trainings())),
                    text_kinds=table_text_kinds(),
                    title="训练对话留痕",
                )
            ),
        )

    # ───────────────────── 反思中心的取数装配 ─────────────────────

    def _report_envelope(self, face: Any, week: str) -> ResultEnvelope:
        """周报信封：无报告 ⇒ `empty`；**无触达 / 数据不足两态由 L6 的正文原话承载**
        （本层不自行推断——按反馈数为 0 反推「无触达」正是 [08 §4] 明禁的那种误判）。"""
        report = self._latest_report(face, week)
        if isinstance(report, ResultEnvelope):
            return report
        if report.get("empty") or report.get("insufficient"):
            return ResultEnvelope.empty(str(report.get("body") or ""))
        sections = [
            {
                "title": section.get("label"),
                "lines": [line.get("text") for line in section.get("lines") or []],
            }
            for section in report.get("sections") or []
        ]
        return ResultEnvelope.ok(
            self._gated_description(
                "report_card",
                slots={"sections": sections},
                text_kinds={"sections": "data"},
                title=f"反思报告 · {report.get('week')}",
                as_of=_as_datetime(report.get("generated_at")),
            )
        )

    def _trace_envelope(self, face: Any, week: str) -> ResultEnvelope:
        """留痕锚点信封——每一环 = 一类留痕（投递 / 信号 / 反馈 / 记忆 / 证据 / 推理链）。"""
        report = self._latest_report(face, week)
        if isinstance(report, ResultEnvelope):
            return report
        trace = report.get("trace") or {}
        window = f"{report.get('period_start')}–{report.get('period_end')}"
        plan = (
            ("投递留痕", trace.get("delivery_ids") or []),
            ("信号留痕", trace.get("signal_ids") or []),
            ("反馈留痕", trace.get("feedback_ids") or []),
            ("记忆读取", trace.get("memory_node_ids") or []),
            ("证据引用", trace.get("evidence_refs") or []),
            ("推理链引用", trace.get("trace_ids") or []),
        )
        steps = [
            {
                "step_type": name,
                "ref": "、".join(_anchor_text(item) for item in anchors) or "—",
                "input_digest": f"取材窗口 {window}",
                "output_digest": f"{len(anchors)} 条",
                "duration_ms": 0,
                "timestamp": report.get("generated_at"),
                "degraded": not anchors,
            }
            for name, anchors in plan
        ]
        return ResultEnvelope.ok(
            self._gated_description(
                "trace_timeline",
                slots={"steps": steps},
                text_kinds={"steps": "generated"},
                title=(
                    f"留痕锚点 · {report.get('week')}"
                    "（报告生成不逐环计时，耗时恒记 0；degraded 标记该类留痕为空）"
                ),
            )
        )

    def _proposals_envelope(self, face: Any) -> ResultEnvelope:
        """建议信封：待批准队列（三动作）+ 周报候选（可接受）+ 主动提案（**信息面**）。"""
        items: list[dict[str, Any]] = []
        for pending in face.pending():
            if pending.get("status") not in ("pending", "deferred"):
                continue
            proposal = pending.get("proposal") or {}
            items.append({
                "kind": "change",
                "pending_id": pending.get("pending_id"),
                "status": pending.get("status"),
                "deferrals": pending.get("deferrals", 0),
                "config_id": proposal.get("config_id"),
                "current": proposal.get("current"),
                "suggested": proposal.get("suggested"),
                "reason": proposal.get("reason"),
                "trace_ref": proposal.get("trace_ref"),
                "source": proposal.get("source"),
                "actions": ["accept", "reject", "defer"],
            })
        report = self._latest_report(face, "")
        if not isinstance(report, ResultEnvelope):
            for candidate in report.get("proposals") or []:
                items.append({
                    "kind": "change",
                    "pending_id": "",
                    "config_id": candidate.get("config_id"),
                    "current": candidate.get("current"),
                    "suggested": candidate.get("suggested"),
                    "reason": candidate.get("reason"),
                    "trace_ref": candidate.get("trace_ref"),
                    "source": f"周报 {report.get('week')}",
                    "actions": ["accept"],
                })
        for proposal in face.proposals():
            draft = proposal.get("draft") or {}
            items.append({
                "kind": "skill",
                "proposal_id": proposal.get("proposal_id"),
                "key": proposal.get("key"),
                "count": proposal.get("count"),
                "reason": proposal.get("reason"),
                "sample": proposal.get("sample"),
                "draft": {
                    "name": draft.get("name"),
                    "description": draft.get("description"),
                    "nodes": len(draft.get("nodes") or []),
                    "queued": not proposal.get("released_week"),
                },
                "actions": ["studio"],
                "note": "交 Studio 落画布后可接受 / 否决；画布微调待 Studio 页面（08 §4）",
            })
        return ResultEnvelope.ok(
            self._gated_description(
                "proposal_card",
                slots={
                    "proposals": items,
                    "labels": {"accept": "接受", "reject": "否决", "defer": "延后",
                               "studio": "交 Studio"},
                },
                text_kinds={"proposals": "data", "labels": "generated"},
                title="建议与提案",
            )
        )

    def _latest_report(self, face: Any, week: str) -> Any:
        """取目标周的报告；无周可读 / 尚未生成 ⇒ 一条 `empty` 信封（**不是**空报告）。"""
        weeks = face.report_weeks()
        target = week or (weeks[-1] if weeks else "")
        if not target:
            return ResultEnvelope.empty("尚无任何一期反思报告（到点后由常驻循环生成）")
        report = face.report(target)
        if report is None:
            return ResultEnvelope.empty(f"{target} 的反思报告尚未生成")
        return report

    # ───────────────────── 演进面（[T-UI-004.3]） ─────────────────────

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

    # ───────────────────── 演进面的取数装配 ─────────────────────

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

    # ───────────────────── 生态面（[T-UI-004.4]） ─────────────────────

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

    # ───────────────────── 生态面的取数装配 ─────────────────────

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

    # ───────────────────── 记忆区（[T-UI-009.2]） ─────────────────────

    def api_memory_positions(self, scope: str) -> dict[str, Any]:
        """记忆区的**持仓 / 关注行情读面**（`table`）——两条端点，一条一段。

        这是 [13-visual-design §2.2] 涨跌三路冗余编码与 `--font-num` 的**首个真实消费方**：
        描述只承载**数值**（涨跌幅给带符号百分数），编码由渲染件一次产出——故「颜色与符号
        恒同时出现」是结构上成立的，不靠本层自觉（[D-105] ②）。

        三种缺口**互不冒充**（[01 §5] 六态）：面未接线 ⇒ `unavailable` + 点名；记忆里这段为空
        ⇒ `empty` + 原因；行情源没注入 / 本地行情库非 `ok` ⇒ `unavailable` + 原因。某标的
        在缓存里没有日线**不是**缺口——那一行的行情列给 `None`，由渲染面出「无数据」。
        """
        if scope not in _POSITION_SCOPES:
            return envelope_payload(
                ResultEnvelope.validation_failed(f"不认识的面：{scope!r}")
            )
        return self._call_face(
            f"memory-{scope}", self.memory, "记忆面",
            lambda face: self._positions_envelope(face, scope),
        )

    def _positions_envelope(self, face: Any, scope: str) -> ResultEnvelope:
        label, empty_reason = _POSITION_SCOPES[scope]
        view = face.positions(scope)
        if not view.get("available"):
            return ResultEnvelope.unavailable(
                str(view.get("reason") or "记忆面未接线"), last_updated_at=_now()
            )
        rows = view.get("rows") or []
        if not rows:
            return ResultEnvelope.empty(empty_reason)
        return ResultEnvelope.ok(
            self._gated_description(
                "table",
                slots=table_slots(_POSITION_COLUMNS, rows),
                text_kinds=table_text_kinds(),
                title=f"{label}行情",
            )
        )

    def _gated_description(
        self,
        component_type: str,
        *,
        slots: dict[str, Any],
        text_kinds: dict[str, str],
        title: str | None = None,
        as_of: datetime | None = None,
    ) -> UiDescription:
        """造一份描述并过**本层两道闸**：必填槽（注册表镜像）+ 渲染前中性化门（01 §6 执行点 2）。

        不过闸即抛 :class:`ValueError`（调用方回 `validation_failed`，[01 §12]：不回可渲染的描述）。
        """
        description = checked_description(
            description_id=new_description_id(),
            component_type=component_type,
            slots=slots,
            text_kinds=text_kinds,
            title=title,
            as_of=as_of,
        )
        gaps = slot_gaps(description)
        if gaps:
            raise ValueError(
                f"{component_type} 缺少必填槽：{'、'.join(gaps)}（01 §12 的必填槽表）"
            )
        verdict = self.neutrality_gate.check(description)
        if not verdict.passed:
            raise ValueError(verdict.reason)
        return description

    def _call_face(
        self,
        where: str,
        face: Any,
        label: str,
        fn: Callable[[Any], Any],
    ) -> dict[str, Any]:
        """调一次面操作并包成信封载荷（**本方法不向上抛**，同 :meth:`api_chat` 的口径）。

        - 面未注入 ⇒ `unavailable` + 点名（不 500、不伪造）；
        - `ValueError` / `KeyError`（输入类，含契约构造失败）⇒ `validation_failed`；
        - 返回 `ResultEnvelope` ⇒ 原样出（六态由该面给，不吞）；其余值 ⇒ `ok` 包起来；
        - 其他异常 ⇒ `failed` + `log_ref`（[00 §6]，不静默）。
        """
        if face is None:
            return envelope_payload(
                ResultEnvelope.unavailable(
                    f"未接入{label}，该端点不可用（装配归生产入口）", last_updated_at=_now()
                )
            )
        try:
            result = fn(face)
        except (ValueError, KeyError) as exc:
            _LOG.info("表现层端点 %s 拒绝请求（%s）：%s", where, type(exc).__name__, exc)
            return envelope_payload(
                ResultEnvelope.validation_failed(str(exc) or "请求不合契约")
            )
        except Exception as exc:  # noqa: BLE001 —— 内部失败：不吞，落日志 + 显式 failed
            log_ref = f"ui/{where}-{uuid.uuid4().hex[:12]}"
            _LOG.exception("表现层端点 %s 未预期失败（log_ref=%s）：%s", where, log_ref, exc)
            return envelope_payload(
                ResultEnvelope.failed("该端点未预期失败，详见服务端日志", log_ref=log_ref)
            )
        if isinstance(result, ResultEnvelope):
            return envelope_payload(result)
        return envelope_payload(ResultEnvelope.ok(result))

    def api_description(self, description: UiDescription) -> dict[str, Any]:
        """出站一份 UI 描述：先查该型必填槽，再过**中性化门**（[01 §6] 执行点 2）。

        两道任一不过都**阻断渲染**并回 `validation_failed`——不回可渲染的描述（[01 §12]）。
        门与注册表都在 Python 侧，规则库与类型表因此只有一份真相源。
        """
        gaps = slot_gaps(description)
        if gaps:
            return envelope_payload(
                ResultEnvelope.validation_failed(
                    f"UI 描述缺少 {description.component_type} 的必填槽：{'、'.join(gaps)}"
                )
            )
        verdict = self.neutrality_gate.check(description)
        if not verdict.passed:
            return envelope_payload(ResultEnvelope.validation_failed(verdict.reason))
        return envelope_payload(ResultEnvelope.ok(description))

    def api_dev_description(self, kind: str) -> dict[str, Any] | None:
        """dev 示例描述：走与真实描述**同一条**出站校验路径。"""
        if not self.dev_enabled:
            return None
        description = self.dev_package.sample_description(kind)
        if description is None:
            return None
        return self.api_description(description)

    def api_dev_sample(self, status: str) -> dict[str, Any] | None:
        """dev 示例端点：按状态名造一条合法信封；未知状态名返回 ``None``（调用方回 400）。"""
        if not self.dev_enabled:
            return None
        envelope = self.dev_package.sample_envelope(status)
        if envelope is None:
            return None
        return envelope_payload(envelope)

    def index_html(self) -> str:
        """`index.html` —— dev 关闭时把占位注释替换为空串。"""
        html = (self.web_root / "index.html").read_text(encoding="utf-8")
        tag = self.dev_package.dev_script_tag() if self.dev_enabled else ""
        return html.replace(DEV_SCRIPT_MARKER, tag)

    def static_file(self, url_path: str) -> Path | None:
        """解析一个静态资产路径（生产根，或 dev 开启时的 dev 根）。"""
        if self.dev_enabled and url_path.startswith("/dev/"):
            return resolve_static(self.dev_package.web_root(), url_path[len("/dev/") :])
        return resolve_static(self.web_root, url_path)


def build_ui(
    *,
    host: str,
    port: int,
    dev: bool = False,
    token: str | None = None,
    web_root: Path | None = None,
    chat: Any = None,
    reflection: Any = None,
    eco: Any = None,
    memory: Any = None,
    workspace: Any = None,
    skills: Any = None,
    mcp: Any = None,
    graph: Any = None,
    deliberation: Any = None,
    delivery: Any = None,
    settings: Any = None,
    commands: Any = None,
) -> UiApp:
    """按已绑定的 ``host`` / ``port`` 装配表现层。

    ``dev=True`` 而 dev 子包不可用时抛 :class:`DevSurfaceUnavailable`——发布构建里
    「带 dev 跑」是配置错误，必须响，不能装作正常。

    ``chat`` / ``reflection`` / ``eco`` / ``memory``：M1–M4 的四个**鸭子端口**；``workspace`` /
    ``skills`` / ``mcp`` / ``graph`` / ``deliberation`` / ``delivery`` / ``settings``：M6 的
    七个（[`T-UI-011.1`]）；``commands``：快捷指令注册表（[`T-UI-012.1`]）。全部缺省
    ``None`` ⇒ 对应面 fail-closed（`ui` 不 import `st_agent.app`，只经端口消费）。
    """
    dev_package = _load_dev() if dev else None
    if dev and dev_package is None:
        raise DevSurfaceUnavailable(
            "dev 面不可用：st_agent.ui.dev 未安装（发布构建已按 [D-060] ④I 剔除）"
        )
    return UiApp(
        guard=RequestGuard(token=token or new_token(), host=host, port=port),
        dev=dev,
        web_root=web_root or WEB_ROOT,
        dev_package=dev_package,
        chat=chat,
        reflection=reflection,
        eco=eco,
        memory=memory,
        workspace=workspace,
        skills=skills,
        mcp=mcp,
        graph=graph,
        deliberation=deliberation,
        delivery=delivery,
        settings=settings,
        commands=commands,
    )
