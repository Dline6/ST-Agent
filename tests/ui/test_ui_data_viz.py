"""图表交互、指标块与分栏的静态守卫（[T-UI-026.2]）。

同样不跑 JS，只钉三件事：

1. `trend_chart` 有十字线 / 浮层 / 范围切换，且**缺值按段断开**（不把缺值当 0，也不把
   该点从横轴挤掉）；范围切换只在横轴可解析为日期时启用。
2. `metric` 是壳面原语（未进注册表），数字走 `--text-metric` + `--font-num`。
3. `split-pane` 等权两栏、可拖宽 / 交换 / 折叠，并在推理页真实消费（铁律 4 的结构载体）。
"""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_WEB = _ROOT / "src" / "st_agent" / "ui" / "web"
_TREND = _WEB / "js" / "plugins" / "l3" / "trend_chart.js"
_METRIC = _WEB / "js" / "components" / "metric.js"
_SPLIT = _WEB / "js" / "components" / "split_pane.js"
_DELIBERATION = _WEB / "js" / "pages" / "deliberation.js"
_REGISTRY = _WEB / "js" / "registry.js"
_CSS = _WEB / "css" / "app.css"
_SAMPLES = _ROOT / "src" / "st_agent" / "ui" / "dev" / "samples.py"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_files_are_present() -> None:
    for path in (_TREND, _METRIC, _SPLIT, _DELIBERATION, _REGISTRY, _CSS, _SAMPLES):
        assert path.is_file(), f"缺文件：{path}"


def test_trend_chart_has_crosshair_tooltip_and_ranges() -> None:
    js = _read(_TREND)
    for anchor in ("trend__crosshair", "trend__tooltip", "pointermove", "pointerleave",
                   "parseTimes", "sliceRange", "'1D'", "'1W'", "'1M'", "'1Y'", "'ALL'"):
        assert anchor in js, f"趋势图缺锚点：{anchor}"
    assert "paintHover" in js and "visible[index]" in js, "浮层应显示横轴点与逐序列数值"


def test_trend_chart_breaks_segments_at_missing_values() -> None:
    """缺值留断口：连续非缺值成段、缺值处断段；不按 0 连线。"""
    js = _read(_TREND)
    assert "function segments(" in js
    assert "if (value === null)" in js and "result.push(current)" in js
    assert "polyline" in js
    assert "trend__dot" in js, "单点段应有可见标记（否则范围切到 1 点时什么也看不到）"
    assert "trend__line" in js


def test_trend_chart_ranges_are_disabled_without_dates() -> None:
    js = _read(_TREND)
    assert "button.disabled = name !== 'ALL' && !times" in js
    assert "横轴不是可解析的日期" in js, "非日期轴必须显式说明，不做假切片"


def test_metric_block_shape_and_typography() -> None:
    js = _read(_METRIC)
    assert "metric__label" in js and "metric__value" in js and "metric__note" in js
    css = _read(_CSS)
    assert ".metric__value" in css
    value_rule = css.split(".metric__value", 1)[1].split("}", 1)[0]
    assert "var(--text-metric)" in value_rule
    assert "var(--font-num)" in value_rule
    assert "tabular-nums" in value_rule


def test_metric_is_consumed_not_orphaned() -> None:
    main = (_WEB / "js" / "main.js").read_text(encoding="utf-8")
    assert "metricBlock" in main and "paintSummary" in main
    assert "summary-anchor__value" not in main, "摘要锚点应改用 metric 指标块"


def test_split_pane_capabilities() -> None:
    js = _read(_SPLIT)
    for anchor in ("flexGrow", "swap", "collapse", "ArrowLeft", "ArrowRight",
                   "role", "separator", "aria-valuenow", "pointerdown"):
        assert anchor in js, f"分栏缺锚点：{anchor}"
    assert "left.body.replaceChildren(...rightNodes)" in js, "交换应换内容，不换两侧位置"


def test_split_pane_is_consumed_on_the_deliberation_page() -> None:
    page = _read(_DELIBERATION)
    assert "createSplitPane" in page
    assert "并排对照" in page
    assert "fillComparison" in page
    assert "本次参与视角少于两个" in page, "不足两方时应显式空态，不补位、不合并"


def test_split_pane_does_not_merge_sides() -> None:
    """两栏是独立容器；本件不得产出「合并后」的数据块。"""
    js = _read(_SPLIT)
    assert "merge" not in js.lower()
    assert "combined" not in js.lower()


def test_no_new_component_types() -> None:
    registry = _read(_REGISTRY)
    assert "metric" not in registry
    assert "split_pane" not in registry
    assert "split-pane" not in registry


def test_dev_sample_axis_is_dated_for_the_range_walkthrough() -> None:
    samples = _read(_SAMPLES)
    assert '"2026-10-15"' in samples and '"T-4"' not in samples
