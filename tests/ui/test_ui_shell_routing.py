"""回环端的**路由骨架与面端口**（[`T-UI-004.1`]）。

本组锁三件事：

1. **面端口的 fail-closed**——端口未注入即 `unavailable` + 点名；注入即 `ok`。
   这条把「未接入」与「接入但无数据」分开（前者点名装配归属，后者由各面自己的读面给空态）。
2. **页面路径回静态壳、未知路径仍 404**——SPA 回退不得吞掉资产缺失（[D-063] 的令牌分档下，
   页面路径属静态壳档，故**不带令牌**也应可开）。
3. **写面走白名单**——`POST` 只认表内路径，表外一律 404；既有 `/api/chat` 行为不变。

另有一条**漂移断言**：前端页面登记表（`web/js/pages.js`）与 Python 侧 `PAGE_PATHS` 相等
（同 `tests/ui/test_ui_registry.py` 对组件类型两侧一致的取向）。

页面集的**覆盖**与**导航收敛**（[`T-UI-011.2`]）另有一组忠实于 [11-sitemap §2.2] 的守卫：
① 声明集须被两端**全覆盖**（不只是「不超出」）；② `nav` 顶层项 ＝ 各**分区根**（从 §2.2
表格逐分区取首行路径，非硬编码期望集）；③ 六条新页面本叶**不挂 `render`**（首屏走六态
可用性探针）；④ 每条登记的 `statusPath` 都在路由表里**真实存在**（探针不落空）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from st_agent.ui.app import PAGE_PATHS
from st_agent.ui.server import _GET_ROUTES, _dispatch_guarded, serve

_UI = Path(__file__).resolve().parents[2] / "src" / "st_agent" / "ui"
_PAGES_JS = _UI / "web" / "js" / "pages.js"

_FACE_STATUS_PATHS = ("/api/reflection/status", "/api/evolution/status", "/api/eco/status")


def _frontend_page_paths() -> set[str]:
    """从 `pages.js` 的登记项里取出页面路径（`{ path: '…', … }` 字面量）。"""
    text = _PAGES_JS.read_text(encoding="utf-8")
    return set(re.findall(r"\{\s*path:\s*'([^']+)'", text))


# ── 面端口：未注入 ⇒ unavailable + 点名 ────────────────────────────────────────
@pytest.mark.parametrize("path", _FACE_STATUS_PATHS)
def test_face_status_is_unavailable_and_names_the_face_when_not_wired(
    ui_server, http_get, auth, path: str
) -> None:
    """端口未注入 ⇒ `unavailable` + 点名（不 500、不伪造「面在」）。"""
    response = http_get(ui_server, path, headers=auth(ui_server))
    assert response.status == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["reason"], "不可用必须说明原因"
    assert "未接入" in payload["reason"] and "面" in payload["reason"]
    assert payload["render"]["presentation"] == "delayed"


@pytest.mark.parametrize("path", _FACE_STATUS_PATHS)
def test_face_status_needs_the_token(ui_server, http_get, path: str) -> None:
    """数据面恒要令牌——新增端点**不得**放宽守卫。"""
    assert http_get(ui_server, path).status == 401


def test_face_status_is_ok_once_the_port_is_injected(http_get, auth) -> None:
    """注入端口 ⇒ `ok`；L6 面同时服务 `/api/reflection/*` 与 `/api/evolution/*`。"""
    with serve(dev=False, reflection=object(), eco=object()) as running:
        for path, face in (
            ("/api/reflection/status", "reflection"),
            ("/api/evolution/status", "reflection"),
            ("/api/eco/status", "eco"),
        ):
            payload = http_get(running, path, headers=auth(running)).json()
            assert payload["status"] == "ok"
            assert payload["data"] == {"face": face, "available": True}


# ── 页面路径：静态壳回退 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("path", sorted(PAGE_PATHS))
def test_page_paths_serve_the_static_shell_without_a_token(ui_server, http_get, path: str) -> None:
    """页面路径回**同一静态壳**，且**不带令牌**也可开（首次导航发不出自定义头）。"""
    response = http_get(ui_server, path)
    assert response.status == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "<title>ST Agent" in response.body.decode("utf-8")


@pytest.mark.parametrize("path", ["/no-such-page", "/reflection/no-such-page", "/api/no-such"])
def test_unknown_paths_are_not_pages(ui_server, http_get, auth, path: str) -> None:
    """未知路径仍 404——SPA 回退**不吞**「资产 / 端点不存在」这个事实。"""
    response = http_get(ui_server, path, headers=auth(ui_server))
    assert response.status == 404


def test_page_paths_still_check_host(ui_server, http_get) -> None:
    """页面路径属静态壳档（免令牌），但**不放宽** `Host` 校验（DNS rebinding）。"""
    assert http_get(ui_server, "/reflection", headers={"Host": "evil.example"}).status == 403


# ── 写面：白名单 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("path", [*_FACE_STATUS_PATHS, "/api/health", "/api/unknown"])
def test_post_to_a_non_whitelisted_path_is_404(ui_server, http_post, auth, path: str) -> None:
    """POST 只认路由表内的路径——表外（含把 GET 路径当 POST 用）一律 404。"""
    assert http_post(ui_server, path, {}, headers=auth(ui_server)).status == 404


def test_chat_stays_the_only_write_endpoint_of_this_leaf(ui_server, http_post, auth) -> None:
    """`/api/chat` 仍按既有口径受理（未接门面 ⇒ `unavailable`），**未**被本次改造改道。"""
    payload = http_post(ui_server, "/api/chat", {"action": "post", "text": "你好"},
                        headers=auth(ui_server)).json()
    assert payload["status"] == "unavailable"


# ── 漂移：前端页面登记表 ↔ Python 侧 PAGE_PATHS ────────────────────────────────
def test_frontend_page_registry_matches_the_python_side() -> None:
    """两侧不漂移：前端登记的页面路径（除根路径外）须与 `PAGE_PATHS` 逐项相等。"""
    frontend = _frontend_page_paths() - {"/"}
    assert frontend == set(PAGE_PATHS), "前端页面登记表与 ui/app.py 的 PAGE_PATHS 不一致"


def test_every_registered_page_has_a_availability_probe() -> None:
    """每条页面登记都点名一个可用性探针路径（壳据此决定导航项是否可点）。"""
    text = _PAGES_JS.read_text(encoding="utf-8")
    probes = re.findall(r"statusPath:\s*'([^']+)'", text)
    assert len(probes) == len(_frontend_page_paths())
    assert all(probe.startswith("/api/") for probe in probes)


# ── 传输层兜底：逸出的异常也转成信封 ──────────────────────────────────────────
def _raise_value_error() -> dict:
    raise ValueError("请求不合契约")


def _raise_runtime_error() -> dict:
    raise RuntimeError("落盘损坏")


def test_transport_guard_turns_escaped_input_errors_into_envelopes() -> None:
    """**兜底**拦的是「参数在进入端点之前就抛」那一类——否则客户端只看到断连（[00 §6]）。"""
    payload = _dispatch_guarded("probe", _raise_value_error)
    assert payload["status"] == "validation_failed"
    assert "请求不合契约" in payload["reason"]


def test_transport_guard_turns_escaped_internal_errors_into_failed() -> None:
    payload = _dispatch_guarded("probe", _raise_runtime_error)
    assert payload["status"] == "failed"
    assert payload["log_ref"].startswith("ui/route-")


def test_transport_guard_passes_normal_payloads_through() -> None:
    sentinel = {"status": "ok"}
    assert _dispatch_guarded("probe", lambda: sentinel) is sentinel


# ── 漂移：页面路径 ↔ 11-sitemap §2.2 的声明集（T-UI-010.2） ─────────────────────
_SITEMAP = Path(__file__).resolve().parents[2] / "docs" / "PRD-v2-Agent" / "11-sitemap.md"


def _declared_page_paths() -> set[str]:
    """从 11-sitemap §2.2 的声明里取出页面路径字面量（反引号包裹的 `/…`）。

    §2.2 是「哪些屏是页面、落在哪个路径」的**权威口径**（[`T-UI-010.2`]）；带标识的面走
    查询串（如 ``/memory?node=<id>``），故先截断 `?` 再取路径部分。本组不跑 JS，纯静态解析。
    """
    text = _SITEMAP.read_text(encoding="utf-8")
    block = text.split("### 2.2 屏 → 页面路径与导航模型", 1)[1].split("## 3 页面 Flow", 1)[0]
    declared = set()
    for candidate in re.findall(r"`([^`]+)`", block):
        base = candidate.split("?", 1)[0]
        if re.fullmatch(r"/[A-Za-z0-9/_-]*", base):
            declared.add(base)
    return declared


def test_declared_page_paths_are_present() -> None:
    """声明的路径集非空且含主入口——否则下面的包含断言会空跑。"""
    declared = _declared_page_paths()
    assert declared, "未从 11-sitemap §2.2 解析到任何页面路径（标题或写法变了会让本组空跑）"
    assert "/" in declared


def test_registered_paths_stay_within_the_declared_set() -> None:
    """**已注册**的页面路径必须都在 §2.2 声明集内——新增路径不可能悄悄绕过 IA 口径。"""
    declared = _declared_page_paths()
    assert set(PAGE_PATHS) <= declared, (
        "已注册的页面路径超出 11-sitemap §2.2 的声明集："
        f"{sorted(set(PAGE_PATHS) - declared)}（先补 IA 口径，再登记页面）"
    )


# ── 页面集覆盖与导航收敛（T-UI-011.2） ───────────────────────────────────────


def _frontend_entries() -> list[tuple[str, str]]:
    """从 `pages.js` 取每条登记的 `(路径, 该登记项的源码块)`——块内可查 `root` / `render` / `statusPath`。

    登记项是**扁平对象字面量**（无嵌套花括号），故按 `{…}` 切块即可；与 `_frontend_page_paths()`
    同法——静态解析，不跑 JS（本包不引运行时）。
    """
    text = _PAGES_JS.read_text(encoding="utf-8")
    entries: list[tuple[str, str]] = []
    for block in re.findall(r"\{([^{}]*)\}", text):
        match = re.search(r"path:\s*'([^']+)'", block)
        if match:
            entries.append((match.group(1), block))
    return entries


def _declared_partition_roots() -> set[str]:
    """从 [11-sitemap §2.2] 的表格**逐分区取首行路径**——即该分区在 `nav` 里的顶层项。

    取表格而非硬编码期望集：口径改了、而 `pages.js` 的 `root` 标记没跟着改，本守卫就红。
    **跨分区的行**（分区列写成 `反思 → 设置` 一类，屏归前者、路径取后者前缀）按定义**不是**
    任何分区的根，故跳过——它是「屏的归属」与「路径的归属」不一致的显式标注。
    """
    text = _SITEMAP.read_text(encoding="utf-8")
    block = text.split("### 2.2 屏 → 页面路径与导航模型", 1)[1].split("## 3 页面 Flow", 1)[0]
    roots: dict[str, str] = {}
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 3 or "→" in cells[0]:
            continue
        match = re.search(r"`([^`]+)`", cells[1])          # 第二列 = 页面路径
        if match:
            roots.setdefault(cells[0], match.group(1).split("?", 1)[0])
    return set(roots.values())


def test_declared_partition_roots_are_parsed() -> None:
    """解析出的分区根非空且含主入口——否则下面的相等断言会空跑。"""
    roots = _declared_partition_roots()
    assert roots, "未从 11-sitemap §2.2 解析到任何分区根（表格写法变了会让本组空跑）"
    assert "/" in roots


def test_declared_partition_roots_are_top_level() -> None:
    """分区根须真的是**顶层**：没有任何声明路径是它的严格祖先（否则它其实是子面）。"""
    declared = _declared_page_paths()
    for root in sorted(_declared_partition_roots()):
        ancestors = {
            path for path in declared
            if path != root and root.startswith(path + "/")     # `/` 不是谁的目录祖先，故不特判
        }
        assert not ancestors, f"{root} 有已声明的祖先路径 {sorted(ancestors)}——它不该是分区根"


def test_registered_paths_cover_every_declared_path() -> None:
    """GWT-1：声明集被两端**全覆盖**——每条声明的屏都有宿主路径，**无屏无家可归**。

    既有断言只保证「不超出」（`PAGE_PATHS ⊆ 声明集`）；本条补**另一个方向**，
    两者合起来才是「两端同源且覆盖全集」。
    """
    declared = _declared_page_paths()
    registered = _frontend_page_paths()
    assert declared <= registered, (
        "11-sitemap §2.2 声明了、但页面登记表里没有宿主路径的屏："
        f"{sorted(declared - registered)}（每条声明的屏都须有家）"
    )
    assert set(PAGE_PATHS) == registered - {"/"}, "Python 侧与前端登记表不一致"


def test_nav_lists_exactly_the_partition_roots() -> None:
    """GWT-3：`nav` 顶层项 ＝ 各分区根（子面不占顶层项）——与 §2.2 表格逐项对位。"""
    expected = _declared_partition_roots()
    actual = {
        path for path, block in _frontend_entries()
        if re.search(r"\broot:\s*true\b", block)
    }
    assert actual == expected, (
        "`nav` 顶层项与 11-sitemap §2.2 的分区根不一致："
        f"多出 {sorted(actual - expected)} / 缺少 {sorted(expected - actual)}"
    )


def test_new_pages_have_no_render_yet() -> None:
    """GWT-2：六条新页面本叶**不挂 `render`**——首屏因此走六态可用性探针（不冒充业务内容）。"""
    blocks = dict(_frontend_entries())
    assert len(blocks) == len(_frontend_entries()), "登记表里有重复路径"
    new_paths = {"/workspace", "/skills", "/mcp", "/deliberation", "/delivery", "/settings"}
    assert new_paths <= set(blocks), f"未登记的声明路径：{sorted(new_paths - set(blocks))}"
    for path in sorted(new_paths):
        assert "render:" not in blocks[path], f"{path} 本叶不应挂 render（内容归七面功能叶）"


def test_every_registered_status_path_resolves_to_a_route() -> None:
    """GWT-4：每条登记的 `statusPath` 都在路由表里**真实存在**——探针不落空。"""
    for path, block in _frontend_entries():
        match = re.search(r"statusPath:\s*'([^']+)'", block)
        assert match, f"{path} 未点名可用性探针路径"
        probe = match.group(1)
        assert probe in _GET_ROUTES, f"{path} 的探针 {probe} 不在路由表里（首屏会拿不到信封）"
