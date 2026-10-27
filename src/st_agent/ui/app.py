"""表现层组合根门面：静态资产 + API 路由装配 + 请求守卫（[00 §1.1]；[D-060]）。

各面的取数实现按「面」拆在 :mod:`st_agent.ui.surfaces`——本模块只保留
:class:`UiApp` 的端口字段、跨面共用的端点（健康 / 面探针 / 描述 / 静态文件）与
:func:`build_ui`。真实数据的接线仍留给需要它的任务：M1 骨架叶只证明
「管道通、六态对、鉴权硬」（[D-063] 的 M1 最小面口径，见任务假设 `A5`）。

dev 面的开关是**构建期**语义：``st_agent.ui.dev`` 子包在发布构建里被
``[tool.setuptools.packages.find] exclude`` **物理剔除**，剔除后 ``dev=True`` 必须
显式失败（:class:`~st_agent.ui.errors.DevSurfaceUnavailable`），而不是静默降级。
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

from st_agent.ui.surfaces.chat import ChatSurface
from st_agent.ui.surfaces.deliberation import DeliberationSurface
from st_agent.ui.surfaces.settings import SettingsSurface
from st_agent.ui.surfaces.reflection import ReflectionSurface
from st_agent.ui.surfaces.evolution import EvolutionSurface
from st_agent.ui.surfaces.eco import EcoSurface
from st_agent.ui.surfaces.memory import MemorySurface
from st_agent.ui.surfaces.skills import SkillsSurface
from st_agent.ui.surfaces.mcp import McpSurface
from st_agent.ui.surfaces.studio import StudioSurface
from st_agent.ui.surfaces.workspace import WorkspaceSurface
from st_agent.ui.surfaces.delivery import DeliverySurface
from st_agent.ui.surfaces.base import _now

__all__ = ["DEV_SCRIPT_MARKER", "PAGE_PATHS", "UiApp", "WEB_ROOT", "build_ui"]

WEB_ROOT = Path(__file__).resolve().parent / "web"
"""生产静态资产根（无构建 ES modules）。"""

_SHELL_TITLE_MAX = 120
"""壳标题长度上限（`title` 意图）——原生标题栏的合理宽度，超长显式拒绝而不是截断。"""


PAGE_PATHS = frozenset({
    "/chat",
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

本集合＝[11-sitemap §2.2]（[`T-UI-010.2`] 冻结的权威口径）声明的**全部 20 条路径**去掉
主入口 `/`（根路径在 `server.py` 里与 `/index.html` 同处判定，不进本集合）——两端由
`tests/ui/test_ui_shell_routing.py` 的漂移断言逐项钉住。

页面路径属**静态壳档**（只校 `Host` / 存在的 `Origin`），故首次导航不必带令牌
（[D-063] 的令牌分档：令牌只压 `/api/*`）；用户数据的取数一律在 `/api/*` 之后。
**未知路径仍 404**——不把任意路径都当页面（否则静态壳会吞掉资产缺失的错误）。
"""


DEV_SCRIPT_MARKER = "<!--ST_DEV_SCRIPT-->"
"""`index.html` 里的 dev 脚本占位：dev 关闭时被替换为空串，故**发布产物不含 dev 引用**。"""


DEV_TOKEN_MARKER = "<!--ST_DEV_TOKEN-->"
"""dev 面把启动令牌注入静态壳的占位（仅 dev；发布构建替换为空串）。

用途：应用内 Browser 预览只能打开 ``http://localhost:<port>``，**无法携带 URL fragment**，
而令牌正典通道就是 fragment。dev 面（[D-060] ④I，发布构建物理剔除）把令牌以 meta 标签
放进同一份静态壳，前端在 fragment 缺失时才回退读取；生产口径不变。
"""


_VERSION = __version__
"""与 `pyproject.toml` 的 `project.version` 一致——单一真相源＝包根 `st_agent.__version__`。"""


def _load_dev() -> Any | None:
    """尝试加载 dev 子包；发布构建里它不存在，返回 ``None``。"""
    try:
        from st_agent.ui import dev  # noqa: PLC0415
    except ImportError:
        return None
    return dev


@dataclass
class RestartGate:
    """破坏性操作（完全清空 / 恢复）之后的**进程级闸门**。

    ``wipe_all`` 直接删掉存储根整目录、``restore_backup`` 覆盖写当前根——两者都让本进程
    手里的 `Store` **句柄失效**（清单缓存过期，甚至根目录已不存在）。此后的读写要么报错、
    要么**静默作用于陈旧清单**，后者正是必须防的那一类（[00 §6] 失败显式化）。

    故操作成功后置 :attr:`reason`：:meth:`UiApp._call_face` 据此把**全部**数据面端点回
    `unavailable` + 该原因，直到用户重启进程。**不假装进程仍健康**。
    """

    reason: str | None = None


@dataclass(frozen=True)
class UiApp(
    ChatSurface,
    DeliberationSurface,
    SettingsSurface,
    ReflectionSurface,
    EvolutionSurface,
    EcoSurface,
    MemorySurface,
    SkillsSurface,
    McpSurface,
    StudioSurface,
    WorkspaceSurface,
    DeliverySurface,
):
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
    """工作区（钉住的持久组件）面（鸭子端口：``pins()`` / ``pin(body)`` / ``unpin(body)`` /
    ``refresh(body)``）；缺省 ``None`` → fail-closed。

    由组合根注入 [`WorkspaceFacade`](../../src/st_agent/l3/workspace.py)——**重解析来源表
    也在那一侧**（真取数口住组合根，表现层不认识来源面键背后的任何东西，[01 §12] 的
    「动作不进描述」）。`ui` 不 import `l3` / `l0` 的类型，只经此鸭子端口消费。
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

    studio: Any = None
    """L1 Studio 面（工作流 / 模板库 / 试跑；[`T-UI-015.3`]）；缺省 ``None`` → fail-closed。

    与 `reflection` 那个 Studio 面**不是同一件事**：后者是 L6 主动提案的**画布会话**交接面
    （`proposal_id` 寻址），本端口是 L1 的**自建工作流**面（版本 / 模板 / 试跑）。同一页面
    `/studio` 上两块各走各的口。
    """

    restart_gate: RestartGate = field(default_factory=RestartGate)
    """破坏性操作后的进程级闸门（见 :class:`RestartGate`）。

    ``frozen`` 的 :class:`UiApp` 装的是一枚**可变**持有者——闸门要在端点里被置位，
    而整个 app 的其余部分不该跟着变。`build_ui` 可注入同一实例供集成套件断言。
    """

    commands: Any = None
    """快捷指令注册表（鸭子端口：``list() -> tuple[QuickCommand, ...]``）；缺省 ``None`` → fail-closed。

    [05 §8](../../docs/技术架构-v2/05-L3-对话主入口.md) 的命令注册表由 L3 维持（含官方 Pack
    的 `command` kind 整组替换），故首页的 `/` 补全清单**经此端口取真注册表**、不在前端另造
    一份枚举。由组合根注入 `M1Runtime.commands`；`ui` 不 import `l3` 的类型。
    """

    shell: bool = False
    """本进程是否由**桌面壳**拉起（[`T-UI-027.1`]）。

    为真时前端才接管 `Ctrl+W` / `Ctrl+Q`——浏览器直开（dev / CI 面）必须让这两个键维持
    浏览器自己的语义，否则会把用户的标签页按住。
    """

    shell_control: Any = None
    """壳意图端口（鸭子类型 ``(action) -> None``；`hide` / `quit`）；缺省 ``None`` → 显式不可用。"""

    @property
    def dev_enabled(self) -> bool:
        """dev 面是否**在本进程内真实可用**（开关打开 **且** 子包在盘上）。"""
        return self.dev and self.dev_package is not None

    def api_health(self) -> dict[str, Any]:
        """健康面：恒为 `ok`——本叶不接真实数据（任务假设 `A5`）。

        `shell` 标记（[`T-UI-027.1`]）告诉前端「本进程由桌面壳拉起」——只有此时才接管
        `Ctrl+W` / `Ctrl+Q`；浏览器面必须让这两个键维持浏览器语义。
        """
        return envelope_payload(
            ResultEnvelope.ok(
                {
                    "service": "st-agent-ui",
                    "version": _VERSION,
                    "dev": self.dev_enabled,
                    "shell": self.shell,
                }
            )
        )

    def api_shell_intent(self, body: dict[str, Any]) -> dict[str, Any]:
        """壳意图（[`T-UI-027.1`]）：`Ctrl+W` → `hide`（收托盘）、`Ctrl+Q` → `quit`（退出）、
        路由变化 → `title`（原生标题栏同步，[14 册 §1]）。

        壳没有 JS 桥（[00 §1.1]「壳不提供任何专有通道」），故动作经回环面到后端、再由后端
        沿既有 stdout 控制管道回传壳。非壳会话（浏览器直开）显式 `unavailable`，不静默。
        """
        action = body.get("action")
        if action not in ("hide", "quit", "title"):
            return envelope_payload(
                ResultEnvelope.validation_failed(
                    f"壳意图动作非法：{action!r}（须为 hide / quit / title）"
                )
            )
        extra: dict[str, Any] | None = None
        if action == "title":
            title = str(body.get("title") or "").strip()
            if not title:
                return envelope_payload(
                    ResultEnvelope.validation_failed("title 动作必须带非空 title")
                )
            if len(title) > _SHELL_TITLE_MAX:
                return envelope_payload(
                    ResultEnvelope.validation_failed(
                        f"标题过长：{len(title)} 字符（上限 {_SHELL_TITLE_MAX}）"
                    )
                )
            extra = {"title": title}
        if not self.shell or self.shell_control is None:
            return envelope_payload(
                ResultEnvelope.unavailable(
                    "当前不是桌面壳会话：收起 / 退出由壳执行，浏览器面不接管该按键",
                    last_updated_at=_now(),
                )
            )
        try:
            self.shell_control(action, extra)
        except Exception as exc:                     # noqa: BLE001 —— 端点须回信封
            return envelope_payload(
                ResultEnvelope.failed(f"壳意图投递失败：{exc}", log_ref="st-agent/ui/shell")
            )
        return envelope_payload(ResultEnvelope.ok({"action": action, **(extra or {})}))

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
        """`index.html` —— dev 关闭时把两个 dev 占位都替换为空串。"""
        html = (self.web_root / "index.html").read_text(encoding="utf-8")
        tag = self.dev_package.dev_script_tag() if self.dev_enabled else ""
        token_tag = (
            f'<meta name="st-dev-token" content="{self.guard.token}">'
            if self.dev_enabled else ""
        )
        return html.replace(DEV_TOKEN_MARKER, token_tag).replace(DEV_SCRIPT_MARKER, tag)

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
    studio: Any = None,
    commands: Any = None,
    restart_gate: RestartGate | None = None,
    shell: bool = False,
    shell_control: Any = None,
) -> UiApp:
    """按已绑定的 ``host`` / ``port`` 装配表现层。

    ``dev=True`` 而 dev 子包不可用时抛 :class:`DevSurfaceUnavailable`——发布构建里
    「带 dev 跑」是配置错误，必须响，不能装作正常。

    ``chat`` / ``reflection`` / ``eco`` / ``memory``：M1–M4 的四个**鸭子端口**；``workspace`` /
    ``skills`` / ``mcp`` / ``graph`` / ``deliberation`` / ``delivery`` / ``settings``：M6 的
    七个（[`T-UI-011.1`]）；``studio``：L1 Studio 面（[`T-UI-015.3`]）；``commands``：快捷指令注册表（[`T-UI-012.1`]）。全部缺省
    ``None`` ⇒ 对应面 fail-closed（`ui` 不 import `st_agent.app`，只经端口消费）。

    ``restart_gate``：见 :class:`RestartGate`；缺省新建一枚。集成套件可注入同一实例，
    以便在端点之外断言「清空 / 恢复之后数据面确实停了」。
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
        studio=studio,
        commands=commands,
        restart_gate=restart_gate if restart_gate is not None else RestartGate(),
        shell=shell,
        shell_control=shell_control,
    )
