"""交互底座的静态守卫（[T-UI-025.1] / [T-UI-025.2]）。

本组不跑 JS（本包不引运行时），只钉**词表与结构**这几件最容易漂移的事：

1. **快捷键词表**——`shortcuts.js` 的 `SHORTCUTS` 与 [14 册 §8.1](../../docs/PRD-v2-Agent/14-desktop-form.md)
   的表格逐键对位；`Ctrl+W` / `Ctrl+Q` 显式登记为**延后到 `T-UI-027`**，且本叶没有接它们。
2. **浮层语义**——对话框有 `role="dialog"` / `aria-modal` / 焦点陷阱 / Esc / 焦点归还；
   浮层栈提供「关最上层」。
3. **反馈四层**——toast 区域是专用活区、时长落在 4–8s；忙碌分级的四档阈值写在代码里。
4. **忙碌态与不可用态分开**——`.is-busy` 独占 `cursor: progress`，`:disabled` 一律 `cursor: default`。
5. **破坏性确认改版**——清空 / 回滚不再连点计数，改真对话框 + 确认词。
6. **面板四类来源**——页面 / 动作 / 记忆节点 / Skill，后两类只消费既有读面。
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_WEB = _ROOT / "src" / "st_agent" / "ui" / "web"
_JS = _WEB / "js"
_SPEC14 = _ROOT / "docs" / "PRD-v2-Agent" / "14-desktop-form.md"
_INDEX = _WEB / "index.html"
_CSS = _WEB / "css" / "app.css"
_TOKENS = _WEB / "css" / "tokens.css"

_SHORTCUTS = _JS / "shortcuts.js"
_PALETTE = _JS / "palette.js"
_FIND = _JS / "find.js"
_DIALOG = _JS / "components" / "dialog.js"
_OVERLAY = _JS / "components" / "overlay.js"
_TOAST = _JS / "components" / "toast.js"
_CONTEXT = _JS / "components" / "context_menu.js"
_SHEET = _JS / "components" / "sheet.js"
_BUSY = _JS / "busy.js"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _rule_block(css: str, selector: str) -> str:
    match = re.search(rf"(?m)^\s*{re.escape(selector)}\s*\{{(.*?)\}}", css, re.S)
    assert match, f"app.css 里找不到规则 {selector}"
    return match.group(1)


def _shortcut_rows() -> list[tuple[str, str]]:
    """14 册 §8.1 的表格行 → (键列去掉反引号, 动作列)。

    标题与切块字符串是**规范侧的稳定锚点**；改了会让本组空跑，故先断言非空。
    """
    text = _read(_SPEC14)
    assert "### 8.1 快捷键" in text and "### 8.2" in text, "14 册 §8.1 的标题变了"
    block = text.split("### 8.1 快捷键", 1)[1].split("### 8.2", 1)[0]
    rows: list[tuple[str, str]] = []
    for line in block.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set("".join(cells)) <= set("-: "):
            continue
        keys = re.findall(r"`([^`]+)`", cells[0])
        if keys:
            rows.append((cells[0].replace("`", "").strip(), cells[1]))
    return rows


# ── 快捷键表 ───────────────────────────────────────────────────────────────────


def test_shortcut_table_is_parsed_from_the_spec() -> None:
    rows = _shortcut_rows()
    assert len(rows) >= 12, f"未从 14 册 §8.1 解析到完整快捷键表（只得到 {len(rows)} 行）"


def test_every_shortcut_in_the_spec_is_registered() -> None:
    """规范表的每个键都在 `SHORTCUTS` / `DEFERRED_SHORTCUTS` 里逐字出现。"""
    text = _read(_SHORTCUTS)
    missing = [keys for keys, _action in _shortcut_rows() if keys not in text]
    assert not missing, f"14 册 §8.1 有、shortcuts.js 未登记的键：{missing}"


def test_shell_shortcuts_are_deferred_not_wired() -> None:
    """`Ctrl+W` / `Ctrl+Q` 显式归 `T-UI-027`，本叶不得接（也不得拦浏览器默认）。"""
    text = _read(_SHORTCUTS)
    assert "Ctrl+W / Ctrl+Q" in text and "T-UI-027" in text
    assert "'w'" not in text and "'q'" not in text, "壳级快捷键被本叶接管了"


def test_partition_switch_covers_all_eight_partitions() -> None:
    """`Ctrl+1…8` 落到的分区序取自 `PARTITIONS`，且确有 8 个分区。"""
    text = _read(_SHORTCUTS)
    assert "/^[1-8]$/" in text, "Ctrl+1…8 的分区号范围不是 1–8"
    main = _read(_JS / "main.js")
    assert "PARTITIONS[index - 1]" in main, "分区切换未按 PARTITIONS 的登记序取"


# ── 浮层：对话框 / 浮层栈 ──────────────────────────────────────────────────────


def test_dialog_declares_the_modal_contract() -> None:
    text = _read(_DIALOG)
    assert "setAttribute('role', 'dialog')" in text
    assert "aria-modal" in text and "'true'" in text
    assert "trapFocus" in text, "对话框未使用焦点陷阱"
    assert "restoreFocus(previous)" in text, "关闭后未归还焦点"
    assert "Escape" in text, "对话框未处理 Esc"


def test_overlay_stack_closes_the_topmost_layer() -> None:
    text = _read(_OVERLAY)
    assert "closeTopOverlay" in text
    assert "stack[stack.length - 1]" in text
    assert "trapFocus" in text


def test_sheet_has_a_real_consumer() -> None:
    """sheet 是非阻断详情面，消费方＝Skills 页「快速查看」（不改既有 `?skill=` 路由）。"""
    assert "openSheet" in _read(_SHEET)
    skills = _read(_JS / "pages" / "skills.js")
    assert "openSheet" in skills and "快速查看" in skills


def test_context_menu_is_level_one_and_puts_destructive_last() -> None:
    text = _read(_CONTEXT)
    assert "!item.destructive" in text and "item.destructive" in text
    assert text.index("!item.destructive") < text.rindex("item.destructive")
    workspace = _read(_JS / "pages" / "workspace.js")
    assert "openContextMenu" in workspace and "contextmenu" in workspace


# ── 反馈四层与忙碌分级 ─────────────────────────────────────────────────────────


def test_toast_region_is_a_dedicated_live_region() -> None:
    html = _read(_INDEX)
    tag = re.search(r'<div[^>]*id="toast-region"[^>]*>', html)
    assert tag and 'role="status"' in tag.group(0) and 'aria-live="polite"' in tag.group(0)


def test_toast_duration_stays_in_the_four_to_eight_second_band() -> None:
    text = _read(_TOAST)
    assert "4800" in text and "8000" in text and "4000" in text


def test_busy_grading_thresholds_are_explicit() -> None:
    text = _read(_BUSY)
    for marker in (
        "indicateAfter: 100",
        "leavableAfter: 1000",
        "backgroundAfter: 10000",
        "maxBlocking: 60000",
    ):
        assert marker in text, f"忙碌分级缺阈值：{marker}"
    assert "已转入后台" in text and "showToast" in text, ">10s 未转后台告知"


def test_busy_and_disabled_are_distinct_states() -> None:
    css = _read(_CSS)
    busy = _rule_block(css, ".is-busy")
    spinner = _rule_block(css, ".is-busy::before")
    assert "cursor: progress" in busy
    assert "animation" in spinner, "忙碌态没有不确定进度指示"
    without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    assert without_comments.count("cursor: progress") == 1, "`cursor: progress` 又回到不可用态上了"
    disabled = re.search(
        r"\.feedback__button:disabled,\s*\n\.proposal-actions__button:disabled\s*\{(.*?)\}", css, re.S
    )
    assert disabled and "cursor: default" in disabled.group(1)


def test_elevation_tokens_exist_in_both_sources() -> None:
    tokens = _read(_TOKENS)
    spec = _read(_ROOT / "docs" / "PRD-v2-Agent" / "13-visual-design.md")
    for name in ("--z-modal", "--z-toast", "--shadow-3", "--dur-base", "--ease-out"):
        assert name in tokens, f"tokens.css 缺浮层令牌 {name}"
        assert name in spec, f"13 册 §2.1 缺浮层令牌 {name}"


# ── 破坏性确认改版 ─────────────────────────────────────────────────────────────


def test_wipe_uses_a_typed_confirmation_dialog() -> None:
    text = _read(_JS / "pages" / "settings.js")
    assert "openDialog" in text and "requirePhrase: '清空'" in text
    assert "确认进度" not in text and "count <" not in text, "清空仍在用连点计数"
    assert "WIPE_TOKENS.slice()" in text, "服务端三令牌硬闸的请求体被改动"


def test_factory_reset_uses_a_typed_confirmation_dialog() -> None:
    text = _read(_JS / "pages" / "evolution.js")
    assert "openDialog" in text and "requirePhrase: '回滚'" in text
    assert "确认进度" not in text and "count <" not in text, "出厂回滚仍在用连点计数"
    assert "confirmations: NEEDED" in text


def test_other_blocking_actions_are_dialogued() -> None:
    memory = _read(_JS / "pages" / "memory.js")
    timeline = _read(_JS / "plugins" / "reflection" / "change_timeline.js")
    assert "openDialog" in memory and "/api/memory/export" in memory
    assert "openDialog" in timeline and "/api/evolution/changes/rollback" in timeline


# ── 命令面板与面内查找 ─────────────────────────────────────────────────────────


def test_palette_covers_pages_actions_memory_and_skills() -> None:
    text = _read(_PALETTE)
    assert "PAGES" in text, "面板未复用页面登记表"
    assert "group: '动作'" in text
    assert "/api/memory/browse?view=list" in text, "记忆节点未按既有读面取候选"
    assert "/api/skills/catalog" in text, "Skill 未按既有读面取候选"
    assert "不可用" in text, "数据面缺席时未点名原因"


def test_palette_is_keyboard_only() -> None:
    text = _read(_PALETTE)
    for key in ("ArrowDown", "ArrowUp", "Home", "End", "Enter", "Escape", "Tab"):
        assert key in text, f"面板缺键盘处理：{key}"
    assert "aria-activedescendant" in text
    assert "role', 'dialog'" in text and "aria-modal" in text


def test_palette_entry_is_enabled_and_wired() -> None:
    tag = re.search(r'<button[^>]*id="palette-entry"[^>]*>', _read(_INDEX))
    assert tag and "disabled" not in tag.group(0), "命令面板入口仍是禁用占位"
    main = _read(_JS / "main.js")
    assert "openPalette" in main and "installShortcuts" in main


def test_in_page_find_uses_the_native_engine_and_says_when_missing() -> None:
    text = _read(_FIND)
    assert "window.find" in text
    assert "不支持面内查找" in text, "引擎无原生查找时未显式说明"


def test_shortcut_help_is_a_real_dialog() -> None:
    main = _read(_JS / "main.js")
    assert "showShortcutHelp" in main and "shortcutTable" in main
    assert "DEFERRED_SHORTCUTS" in main, "快捷键表未标注延后到壳闭合叶的键"
