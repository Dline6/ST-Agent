"""视觉令牌的一致性守卫（[T-UI-007] / [D-104]）。

钉住一件事：**`tokens.css` 的令牌名与视觉规范 §2.1 的令牌总表逐名一致**。
规范文档是给人读的口径，令牌文件是给渲染器消费的值——两处分开写必然漂移，
而漂移的表现是「文档说不存在的令牌存在」（或反向），只在有人照着文档写页面时才暴露。

另钉一条链路：`index.html` 真的引了 `tokens.css`——加文件不加 `<link>` 会让整套
令牌静默不生效（页面照常渲染，只是回到浏览器默认值），这种失败不会报错。
"""

from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_TOKENS = _ROOT / "src" / "st_agent" / "ui" / "web" / "css" / "tokens.css"
_APP_CSS = _ROOT / "src" / "st_agent" / "ui" / "web" / "css" / "app.css"
_INDEX = _ROOT / "src" / "st_agent" / "ui" / "web" / "index.html"
_SPEC = _ROOT / "docs" / "PRD-v2-Agent" / "13-visual-design.md"

_DECLARED = re.compile(r"^\s*(--[a-z0-9-]+)\s*:", re.M)
_IN_TABLE = re.compile(r"`(--[a-z0-9-]+)`")
_SECTION = re.compile(r"###\s*2\.1\s*令牌总表(.*?)(?=\n###\s|\Z)", re.S)


def _token_file_names() -> set[str]:
    return set(_DECLARED.findall(_TOKENS.read_text(encoding="utf-8")))


def _spec_table_names() -> set[str]:
    match = _SECTION.search(_SPEC.read_text(encoding="utf-8"))
    assert match, "视觉规范里找不到 §2.1 令牌总表（标题或编号变了？本用例据它取令牌名）"
    return set(_IN_TABLE.findall(match.group(1)))


def test_token_sources_are_present() -> None:
    """两个来源都在盘上——路径写错会让本组用例空跑并通过。"""
    for path in (_TOKENS, _APP_CSS, _INDEX, _SPEC):
        assert path.is_file(), f"缺文件：{path}"


def test_token_names_match_the_spec_table() -> None:
    """`tokens.css` 的令牌名集合 == 规范 §2.1 令牌总表的令牌名集合（两处不得漂移）。"""
    declared = _token_file_names()
    documented = _spec_table_names()
    assert declared, "tokens.css 未解析到任何令牌声明"
    assert declared == documented, (
        f"令牌与规范漂移——仅在 tokens.css：{sorted(declared - documented)}；"
        f"仅在规范：{sorted(documented - declared)}"
    )


def test_app_css_consumes_declared_tokens_only() -> None:
    """`app.css` 引用的每个 `var(--x)` 都必须是 `tokens.css` 声明过的（不写臆造的令牌名）。

    只认 `var(--x)` 形态——裸扫 `--[a-z-]+` 会把 BEM 修饰符（`.state--loading` /
    `.approval-item__state--pending`）误当令牌。
    """
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", _APP_CSS.read_text(encoding="utf-8")))
    declared = _token_file_names()
    assert used, "app.css 未引用任何令牌（令牌层形同虚设）"
    assert used <= declared, f"app.css 引用了未声明的令牌：{sorted(used - declared)}"


def test_index_html_links_the_token_layer() -> None:
    """`index.html` 必须引 `tokens.css`（且排在 `app.css` 之前——后者消费前者）。"""
    html = _INDEX.read_text(encoding="utf-8")
    tokens_at = html.find("/css/tokens.css")
    app_at = html.find("/css/app.css")
    assert tokens_at != -1, "index.html 未引 tokens.css：整套令牌会静默不生效"
    assert app_at != -1, "index.html 未引 app.css"
    assert tokens_at < app_at, "tokens.css 必须排在 app.css 之前"
