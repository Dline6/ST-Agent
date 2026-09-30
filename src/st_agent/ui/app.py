"""表现层组合根：静态资产 + API 路由 + 请求守卫（[00 §1.1]；[D-060]）。

本模块只做**装配与传输**——不引入任何域逻辑。真实数据的接线留给需要它的任务：M1 骨架叶
只证明「管道通、六态对、鉴权硬」（[D-063] 的 M1 最小面口径，见任务假设 `A5`）。

dev 面的开关是**构建期**语义：``st_agent.ui.dev`` 子包在发布构建里被
``[tool.setuptools.packages.find] exclude`` **物理剔除**，剔除后 ``dev=True`` 必须
显式失败（:class:`~st_agent.ui.errors.DevSurfaceUnavailable`），而不是静默降级。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.contracts.ui_description import UiDescription

from st_agent.ui.envelope import envelope_payload
from st_agent.ui.errors import DevSurfaceUnavailable
from st_agent.ui.neutrality_gate import NeutralityGate
from st_agent.ui.registry import slot_gaps
from st_agent.ui.security import RequestGuard, new_token, resolve_static

__all__ = ["DEV_SCRIPT_MARKER", "UiApp", "WEB_ROOT", "build_ui"]

WEB_ROOT = Path(__file__).resolve().parent / "web"
"""生产静态资产根（无构建 ES modules）。"""

DEV_SCRIPT_MARKER = "<!--ST_DEV_SCRIPT-->"
"""`index.html` 里的 dev 脚本占位：dev 关闭时被替换为空串，故**发布产物不含 dev 引用**。"""

_VERSION = "0.1.0"
"""与 `pyproject.toml` 的 `project.version` 保持一致。"""


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

    @property
    def dev_enabled(self) -> bool:
        """dev 面是否**在本进程内真实可用**（开关打开 **且** 子包在盘上）。"""
        return self.dev and self.dev_package is not None

    def api_chat(self, body: dict[str, Any]) -> dict[str, Any]:
        """对话端点：经注入的门面走一段反向流，出站点仍过**同一条**校验。

        门面的返回含 `reply`（`ResultEnvelope`，附六态渲染语义）与可选
        `description`（`UiDescription`，走 :meth:`api_description` 的中性化门与必填槽）。
        未注入门面 → `unavailable` + 点名（不伪造，同 `api_health` 之外的各 fail-closed 面）。
        """
        if self.chat is None:
            return envelope_payload(
                ResultEnvelope.unavailable(
                    "未接入对话门面，/api/chat 不可用（装配归组合根）",
                    last_updated_at=_now(),
                )
            )
        result = self.chat.turn(body)
        reply = result.get("reply")
        payload = envelope_payload(reply) if isinstance(reply, ResultEnvelope) else {
            "status": "failed", "reason": "对话门面返回非法结构", "render": {},
        }
        payload["session_id"] = result.get("session_id")
        payload["needs_confirmation"] = bool(result.get("needs_confirmation"))
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


    def api_health(self) -> dict[str, Any]:
        """健康面：恒为 `ok`——本叶不接真实数据（任务假设 `A5`）。"""
        return envelope_payload(
            ResultEnvelope.ok(
                {"service": "st-agent-ui", "version": _VERSION, "dev": self.dev_enabled}
            )
        )

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
) -> UiApp:
    """按已绑定的 ``host`` / ``port`` 装配表现层。

    ``dev=True`` 而 dev 子包不可用时抛 :class:`DevSurfaceUnavailable`——发布构建里
    「带 dev 跑」是配置错误，必须响，不能装作正常。

    ``chat``：对话门面（鸭子类型 ``turn(body) -> dict``），由组合根注入；缺省 ``None``
    时 `/api/chat` fail-closed（[T-INT-002]：`ui` 不 import `app`，只经此端口消费）。
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
    )
