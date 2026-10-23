"""生成壳与托盘的中性几何图标（[`T-UI-027.1`] GWT-4）。

产物随包入库（`src/st_agent/ui/shell/assets/`）：

- ``st-agent.ico``——安装器 / 快捷方式 / 窗口图标，含 **16 / 32 / 256** 三档；
- ``tray-light-{16,32,256}.png``——浅色系统主题：**深色**线条；
- ``tray-dark-{16,32,256}.png``——深色系统主题：**浅色**线条。

图形是**中性几何形**（圆角框 + 内方块），不写任何文字、不带品牌符号——产品口径要求
产出不做拟人化。资源随包由 ``packaging/st-agent-shell.spec`` 打入壳产物；
[`T-UI-019`](../../项目管理/tasks/T-UI-019-Windows安装器与安装体验.md) 消费同一个 ``.ico``。

用法（仓库根，需 Pillow）：

```bash
python packaging/make_icons.py
```
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "src" / "st_agent" / "ui" / "shell" / "assets"

SIZES = (16, 32, 256)
DARK = (28, 32, 38, 255)
LIGHT = (226, 232, 240, 255)


def render(size: int, *, foreground: tuple[int, int, int, int],
           background: tuple[int, int, int, int] | None) -> Image.Image:
    """画一枚 ``size × size`` 的图标（透明底；给了 ``background`` 则铺圆角底）。"""
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    unit = size / 256

    if background is not None:
        draw.rounded_rectangle(
            (0, 0, size - 1, size - 1), radius=max(2, round(48 * unit)), fill=background
        )
        inset = max(1, round(40 * unit))
        draw.rounded_rectangle(
            (inset, inset, size - 1 - inset, size - 1 - inset),
            radius=max(1, round(24 * unit)),
            outline=foreground,
            width=max(1, round(10 * unit)),
        )
    else:
        inset = max(1, round(28 * unit))
        draw.rounded_rectangle(
            (inset, inset, size - 1 - inset, size - 1 - inset),
            radius=max(1, round(48 * unit)),
            outline=foreground,
            width=max(1, round(20 * unit)),
        )

    inner = round(size * 0.32)
    draw.rectangle((inner, inner, size - 1 - inner, size - 1 - inner), fill=foreground)
    return image


def write_assets(directory: Path = ASSETS) -> list[Path]:
    """写全部图标资源，返回落盘路径（可重复执行）。"""
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    ico = directory / "st-agent.ico"
    app_icon = render(256, foreground=LIGHT, background=DARK)
    app_icon.save(ico, format="ICO", sizes=[(size, size) for size in SIZES])
    written.append(ico)

    for theme, colour in (("light", DARK), ("dark", LIGHT)):
        for size in SIZES:
            path = directory / f"tray-{theme}-{size}.png"
            render(size, foreground=colour, background=None).save(path, format="PNG")
            written.append(path)
    return written


def main() -> int:
    for path in write_assets():
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
