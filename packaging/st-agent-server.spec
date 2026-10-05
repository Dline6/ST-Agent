# -*- mode: python ; coding: utf-8 -*-
"""后端二进制的 PyInstaller 规格（[`T-UI-002.3`]）。

冻结 [`python -m st_agent`](../../src/st_agent/__main__.py)（生产运行入口，[`T-UI-002.1`]）：
后端是**常驻主体**，壳只是它的客户端（[00 §1.1](../../docs/技术架构-v2/00-架构总览.md)）。

两处随包资产（缺一即运行期报错，故列在此处而非"记得加"）：

- ``st_agent/l0/market/schema.sql``——`MarketDb.init_db` 经 ``importlib.resources`` 读同包 DDL；
- ``st_agent/ui/web/**``——无构建静态前端（[D-063](../../项目管理/决策日志.md) ①）。

**排除 dev 面**：``st_agent.ui.dev``（浏览器直开走查面板）按 [D-060](../../项目管理/决策日志.md) ④I
在发布产物里**物理剔除**——被剔除后 ``build_ui(dev=True)`` 会显式失败，不静默降级。
壳侧依赖（pywebview / pystray）同样排除：壳是**另一个产物**。
"""

from pathlib import Path
import sys

ROOT = Path(SPECPATH).resolve().parent          # noqa: F821 —— SPECPATH 由 PyInstaller 注入
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

# 官方 Pack 的两 Bundle 执行模块是**延迟导入**（`importlib.import_module`，见
# `l1/skills/official/install.BUNDLE_MODULES`）——静态分析看不见它们，不显式收进来
# 冻结产物一装载官方 Pack 就报 ModuleNotFoundError（T-UI-002.3 实测踩到）。
# 从**同一个真相源**取清单，避免规格与代码漂移。
from st_agent.l1.skills.official.install import BUNDLE_MODULES   # noqa: E402

datas = [
    (str(SRC / "st_agent" / "l0" / "market" / "schema.sql"), "st_agent/l0/market"),
    (str(SRC / "st_agent" / "ui" / "web"), "st_agent/ui/web"),
]

a = Analysis(                                   # noqa: F821
    [str(SRC / "st_agent" / "__main__.py")],
    pathex=[str(SRC)],
    binaries=[],
    datas=datas,
    hiddenimports=list(BUNDLE_MODULES),
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "st_agent.ui.dev",          # D-060 ④I：发布构建物理剔除
        "webview", "pystray", "PIL",  # 壳侧依赖——归壳产物
        "tkinter", "test", "unittest", "pydoc_data",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)                               # noqa: F821

exe = EXE(                                      # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="st-agent-server",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,                               # 握手走 stdout —— 壳要读它
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
    name="st-agent-server",
)
