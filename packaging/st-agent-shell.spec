# -*- mode: python ; coding: utf-8 -*-
"""桌面壳产物的 PyInstaller 规格（[`T-UI-002.3`]）。

冻结 [`python -m st_agent.ui.shell`](../../src/st_agent/ui/shell/__main__.py)：壳只做
**原生窗口 / 托盘 / 自启 / 通知接线**，**不含业务逻辑**，也**不含后端**——它拉起的是
**另一个产物**（``st-agent-server``，见 [同目录另一份规格](st-agent-server.spec)）。
故这里显式排除 `st_agent` 的各业务层与组合根：壳的导入图本就不含它们
（`st_agent.ui.__init__` 的再导出是**惰性**的，见 [T-UI-002.3] 的说明），排除只是把意图写死。

壳侧依赖（pywebview / pystray / pythonnet）经 ``collect_all`` 收全：pywebview 的 Windows
后端要把 ``webview/lib`` 下的 WebView2 装配件与 .NET 桥（pythonnet）一并带上。
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 —— SPECPATH 由 PyInstaller 注入
SRC = ROOT / "src"

webview_datas, webview_binaries, webview_hidden = collect_all("webview")
pystray_datas, pystray_binaries, pystray_hidden = collect_all("pystray")

datas = [*webview_datas, *pystray_datas]
binaries = [*webview_binaries, *pystray_binaries]
hiddenimports = [*webview_hidden, *pystray_hidden, "clr", "clr_loader", "pythonnet"]

a = Analysis(                                   # noqa: F821
    [str(SRC / "st_agent" / "ui" / "shell" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # 壳不含业务逻辑，也不含后端（后端是另一个产物）
        "st_agent.app",
        "st_agent.contracts",
        "st_agent.l0", "st_agent.l1", "st_agent.l2", "st_agent.l3", "st_agent.l4",
        "st_agent.ui.app", "st_agent.ui.server", "st_agent.ui.envelope",
        "st_agent.ui.dev", "st_agent.ui.registry", "st_agent.ui.neutrality_gate",
        "tkinter",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)                               # noqa: F821

exe = EXE(                                      # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="st-agent-shell",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                              # GUI 应用：无控制台（故收尾走 stdin 控制通道）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(                                 # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="st-agent-shell",
)
