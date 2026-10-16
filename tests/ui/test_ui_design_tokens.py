"""视觉令牌的一致性守卫（[T-UI-007] 立 · [T-UI-023] 扩为四组）。

钉住四件事：

1. **名集**——`tokens.css` 声明的令牌名 == 视觉规范 §2.1 令牌总表的令牌名。
   规范文档是给人读的口径，令牌文件是给渲染器消费的值，两处分开写必然漂移，
   而漂移的表现是「文档说不存在的令牌存在」（或反向），只在有人照着文档写页面时才暴露。
2. **值**——每个令牌的**浅色值**（`:root`）与**暗色值**（`prefers-color-scheme: dark` 覆盖）与规范表
   逐项相等。只比名字查不出「文档写 `#8a9099`、文件里是 `#6b7280`」这类漂移，而那正是 P0 要修的缺陷。
3. **对比度**——正文 / 六态 / 涨跌 / 强调在 `--card` 上 ≥ 4.5:1；**界面边界线**（`--line-strong`）
   在 `--card` 与 `--bg` 上都 ≥ 3:1（边界可能画在任一面底上）；暗色下涨跌色与状态色**不得同值**。
   大字（≥20px）按 WCAG 只需 3:1，被上面的 4.5 界覆盖。
4. **零裸间距**——`app.css` 的盒模型间距（margin / padding / gap）一律消费 `--space-*`，
   且各档都是 4px 的整数倍（受控间距的全部意义就在这条能机器校验）。

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

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_TOKEN_DECL = re.compile(r"(--[a-z0-9-]+)\s*:\s*([^;{}]+);")
_CSS_DECL = re.compile(r"([a-z-]+)\s*:\s*([^;{}]+);")
_LIGHT_BLOCK = re.compile(r":root\s*\{(.*?)\}", re.S)
_DARK_BLOCK = re.compile(
    r"@media\s*\(prefers-color-scheme:\s*dark\)\s*\{\s*:root\s*\{(.*?)\}", re.S
)
_SECTION = re.compile(r"###\s*2\.1\s*令牌总表(.*?)(?=\n###\s|\Z)", re.S)
_BACKTICKED = re.compile(r"`([^`]+)`")
_ROW = re.compile(r"^\|(.+)\|\s*$", re.M)

# 正文级文字：对 `--card` 必须 ≥ 4.5:1（WCAG 1.4.3 AA）
_TEXT_ON_CARD = (
    "--fg", "--fg-muted", "--fg-faint", "--accent", "--ok", "--empty", "--delayed",
    "--error", "--input-error", "--notice", "--up", "--down", "--flat",
)
# 界面边界：对 `--card` 与 `--bg` 都必须 ≥ 3:1（WCAG 1.4.11）
_BOUNDARY_ON = ("--card", "--bg")
# 暗色下必须互不同值的两对（换色不解决色觉接近，但「完全同值」连机器都判得出来）
_DISTINCT_IN_DARK = (("--down", "--ok"), ("--up", "--error"))

_SPACING_PROPS = frozenset({
    "margin", "margin-top", "margin-right", "margin-bottom", "margin-left",
    "padding", "padding-top", "padding-right", "padding-bottom", "padding-left",
    "gap", "row-gap", "column-gap",
})
_SPACE_TOKEN = re.compile(r"var\(--space-[1-9]\)")


def _declared(block: str) -> dict[str, str]:
    return {name: value.strip() for name, value in _TOKEN_DECL.findall(block)}


def _token_blocks() -> tuple[dict[str, str], dict[str, str]]:
    """（浅色值，暗色覆盖值）——前者是 `:root`，后者是 dark 媒体块里重声明的那些。"""
    text = _COMMENT.sub("", _TOKENS.read_text(encoding="utf-8"))
    light = _LIGHT_BLOCK.search(text)
    dark = _DARK_BLOCK.search(text)
    assert light, "tokens.css 里找不到 :root 块（本用例据它取浅色值）"
    return _declared(light.group(1)), _declared(dark.group(1) if dark else "")


def _spec_block() -> str:
    match = _SECTION.search(_SPEC.read_text(encoding="utf-8"))
    assert match, "视觉规范里找不到 §2.1 令牌总表（标题或编号变了？本用例据它取令牌名与值）"
    return match.group(1)


def _spec_rows() -> dict[str, list[str]]:
    """§2.1 的**表格行** → {令牌名: [值, ...]}。

    表形是「令牌 | 角色 | 值（浅，暗）」——名字取第 1 格的 `--x`，值取**第 3 格起**的反引号串；
    角色格只写散文，**不参与取值**（否则「六态 · delayed（含 `unavailable`）」里的 `unavailable`
    会被读成一个值）。
    """
    rows: dict[str, list[str]] = {}
    for row in _ROW.finditer(_spec_block()):
        cells = [cell.strip() for cell in row.group(1).split("|")]
        name = re.fullmatch(r"`(--[a-z0-9-]+)`", cells[0])
        if not name:
            continue
        values = [
            value
            for cell in cells[2:]
            for value in _BACKTICKED.findall(cell)
            if not value.startswith("--")
        ]
        rows[name.group(1)] = values
    return rows


def _luminance(colour: str) -> float:
    red, green, blue = (int(colour[index:index + 2], 16) / 255 for index in (1, 3, 5))

    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def _contrast(first: str, second: str) -> float:
    high, low = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_token_sources_are_present() -> None:
    """两个来源都在盘上——路径写错会让本组用例空跑并通过。"""
    for path in (_TOKENS, _APP_CSS, _INDEX, _SPEC):
        assert path.is_file(), f"缺文件：{path}"


def test_token_names_match_the_spec_table() -> None:
    """`tokens.css` 的令牌名集合 == 规范 §2.1 令牌总表的令牌名集合（两处不得漂移）。"""
    light, dark = _token_blocks()
    declared = set(light) | set(dark)
    documented = set(_spec_rows())
    assert declared, "tokens.css 未解析到任何令牌声明"
    assert documented, "规范 §2.1 未解析到任何令牌行"
    assert declared == documented, (
        f"令牌与规范漂移——仅在 tokens.css：{sorted(declared - documented)}；"
        f"仅在规范：{sorted(documented - declared)}"
    )
    # §2.1 块内**任何**反引号令牌名都必须是声明过的（散文里也不得提已删令牌）。
    stray = {name for name in _BACKTICKED.findall(_spec_block()) if name.startswith("--")} - declared
    assert not stray, f"§2.1 里提到了未声明的令牌：{sorted(stray)}"


def test_token_values_match_the_spec_table() -> None:
    """每个令牌的浅色值与暗色覆盖值与规范 §2.1 表逐项相等。"""
    light, dark = _token_blocks()
    mismatched = []
    for name, values in _spec_rows().items():
        assert values, f"{name} 在 §2.1 里没给值（本用例据值列断言）"
        document_light = values[0]
        document_dark = values[1] if len(values) > 1 else None
        if light.get(name) != document_light:
            mismatched.append(f"{name} 浅色：文件 {light.get(name)!r} ≠ 规范 {document_light!r}")
        if dark.get(name) != document_dark:
            mismatched.append(f"{name} 暗色：文件 {dark.get(name)!r} ≠ 规范 {document_dark!r}")
    assert not mismatched, "令牌值与规范漂移：\n" + "\n".join(mismatched)


def test_text_tokens_meet_the_contrast_floor() -> None:
    """正文 / 六态 / 涨跌 / 强调在 `--card` 上 ≥ 4.5:1，浅暗两套主题都要过。"""
    light, dark = _token_blocks()
    failures = []
    for theme, tokens in (("浅色", light), ("暗色", {**light, **dark})):
        for name in _TEXT_ON_CARD:
            ratio = _contrast(tokens[name], tokens["--card"])
            if ratio < 4.5:
                failures.append(f"{theme} {name} 对 --card 只有 {ratio:.2f}:1（要求 ≥ 4.5）")
    assert not failures, "对比度不达标：\n" + "\n".join(failures)


def test_boundary_token_meets_the_interface_floor() -> None:
    """界面边界线 `--line-strong` 对 `--card` 与 `--bg` 都 ≥ 3:1（控件可能落在任一面底上）。"""
    light, dark = _token_blocks()
    failures = []
    for theme, tokens in (("浅色", light), ("暗色", {**light, **dark})):
        for surface in _BOUNDARY_ON:
            ratio = _contrast(tokens["--line-strong"], tokens[surface])
            if ratio < 3.0:
                failures.append(
                    f"{theme} --line-strong 对 {surface} 只有 {ratio:.2f}:1（要求 ≥ 3）"
                )
    assert not failures, "界面边界不达标：\n" + "\n".join(failures)


def test_direction_and_status_colours_are_distinct_in_dark() -> None:
    """暗色下涨跌色不得与状态色同值（原缺陷：`--down` 与 `--ok` 完全同值，1.00:1）。"""
    light, dark = _token_blocks()
    for theme, tokens in (("浅色", light), ("暗色", {**light, **dark})):
        for first, second in _DISTINCT_IN_DARK:
            assert tokens[first] != tokens[second], (
                f"{theme}下 {first} 与 {second} 完全同值（{tokens[first]}）——"
                f"方向与状态不得同色（§4.4）"
            )


def test_space_tokens_are_on_the_four_px_grid() -> None:
    """间距令牌各档都是 4px 的整数倍、且逐档递增（受控间距的全部意义就在这条能机器校验）。"""
    light, _ = _token_blocks()
    spaces = {name: value for name, value in light.items() if name.startswith("--space-")}
    assert len(spaces) == 9, f"间距档数不是 9：{sorted(spaces)}"
    steps = []
    for index in range(1, 10):
        name = f"--space-{index}"
        match = re.fullmatch(r"(\d+)px", spaces.get(name, ""))
        assert match, f"{name} 未声明或值不是 px：{spaces.get(name)!r}"
        pixels = int(match.group(1))
        assert pixels % 4 == 0, f"{name} = {pixels}px 不在 4px 网格上"
        steps.append(pixels)
    assert steps == sorted(steps) and len(set(steps)) == 9, f"间距档未递增：{steps}"


def test_app_css_has_no_bare_spacing_values() -> None:
    """`app.css` 的盒模型间距一律消费 `--space-*`（0 与 auto 除外，它们不含长度）。

    宽度 / 高度 / `min-width` / `flex-basis` 不在本口径内——14 册 §3.1 约束的是**间距**。
    """
    text = _COMMENT.sub("", _APP_CSS.read_text(encoding="utf-8"))
    declarations = _CSS_DECL.findall(text)
    assert declarations, "app.css 未解析到任何声明（正则写错会让本用例空跑并通过）"
    bare = []
    for prop, value in declarations:
        if prop not in _SPACING_PROPS:
            continue
        for part in value.split():
            if part in {"0", "auto"} or _SPACE_TOKEN.fullmatch(part):
                continue
            bare.append(f"{prop}: {value}")
    assert not bare, "app.css 出现裸间距值（一律消费 --space-*）：\n" + "\n".join(sorted(set(bare)))


def test_app_css_consumes_declared_tokens_only() -> None:
    """`app.css` 引用的每个 `var(--x)` 都必须是 `tokens.css` 声明过的（不写臆造的令牌名）。

    只认 `var(--x)` 形态——裸扫 `--[a-z-]+` 会把 BEM 修饰符（`.state--loading` /
    `.approval-item__state--pending`）误当令牌。
    """
    used = set(re.findall(r"var\((--[a-z0-9-]+)\)", _APP_CSS.read_text(encoding="utf-8")))
    light, dark = _token_blocks()
    declared = set(light) | set(dark)
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
