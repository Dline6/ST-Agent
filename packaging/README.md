# 打包（Packaging）

把后端与桌面壳冻结成两份**可独立分发**的产物（[`T-UI-002.3`](../项目管理/tasks/T-UI-002.3-打包与产物.md)）。
两进程形态见 [00 §1.1](../docs/技术架构-v2/00-架构总览.md)：**后端是常驻主体**（`st-agent-server`），
**壳是它的客户端**（`st-agent-shell`，只做原生窗口 / 托盘 / 自启 / 通知接线）。

```bash
pip install -e ".[shell,packaging]"      # 壳依赖 + PyInstaller
python packaging/build.py --target all --smoke --clean
```

## 产物

| 产物 | 入口 | 说明 |
|---|---|---|
| `dist/st-agent-server/` | [`python -m st_agent`](../src/st_agent/__main__.py) | 后端：L0–L4 装配 + 本机回环面 + 握手（stdout 单行 JSON） |
| `dist/st-agent-shell/` | [`python -m st_agent.ui.shell`](../src/st_agent/ui/shell/__main__.py) | 壳：用**系统 WebView** 打开回环界面；拉起 / 回收同级后端 |

壳按**摆放惯例**找后端（同目录，或同级目录下的 `st-agent-server/`），找不到才回落
`python -m st_agent`；也可用 `st-agent-shell --backend <路径>` 显式指定。

## 自检

`build.py` 在构建后做三件事（`--check-only` 可只查不建）：

1. **模块面**——读产物内 PYZ 归档：**dev 面必须不在**（[D-060](../项目管理/决策日志.md) ④I 的物理剔除），
   且**壳产物不含任何业务层**（`st_agent.l0`–`l4`、`st_agent.app`）；按目录找会「一路通过」而什么都没验到；
2. **数据面**——静态前端（`ui/web/index.html`）与市场库 DDL（`l0/market/schema.sql`）在盘；
3. **冒烟**（`--smoke`）——清空 `PYTHON*` 环境 + 非仓库 `cwd` 真起后端二进制，走
   「就绪握手 → `/api/health` → `/api/chat`（需接上真组合根）→ 优雅收尾」。

## 逐平台前置与命令

| 平台 | 前置 | 命令 | 产物状态 |
|---|---|---|---|
| **Windows** | Python ≥3.12（构建机）；WebView2 Runtime（运行机，Win11 自带） | `python packaging/build.py --target all --smoke --clean` | ✅ **2026-10-05 实测**：两份产物构建通过、自检通过、后端冒烟通过、冻结壳确能拉起同级冻结后端 |
| **macOS** | Python ≥3.12；Apple Command Line Tools（`pyobjc` 由 pywebview 的依赖带入） | `python packaging/build.py --target all --clean` | ⚠️ **未产出**（须在 macOS 主机上构建；本仓当前无该主机） |
| **Linux** | Python ≥3.12；`python3-gi` / `gir1.2-webkit2-4.1`（WebKitGTK）+ `libnotify-bin` | `python packaging/build.py --target all --clean` | ⚠️ **未产出**（同上；须在 Linux 主机上构建） |

**已知口径**：

- **安全软件可能删掉 `runw.exe`**（PyInstaller 的**窗口版** bootloader，壳用它才不弹控制台）——本机实测遇到过一次：`site-packages/PyInstaller/bootloader/Windows-64bit-intel/` 下只剩 `run.exe`，构建报 `Fatal error: PyInstaller does not include a pre-compiled bootloader`。**处置**：给该目录加白名单，或从 PyInstaller 的 wheel 里取回该文件（wheel 内确实含它）：
  ```bash
  python -c "import zipfile,glob,pathlib,os;p=glob.glob('**/pyinstaller-*.whl',recursive=True)[0];d=pathlib.Path(os.environ['APPDATA'])/'Python/Python314/site-packages/PyInstaller/bootloader/Windows-64bit-intel';(d/'runw.exe').write_bytes(zipfile.ZipFile(p).read('PyInstaller/bootloader/Windows-64bit-intel/runw.exe'))"
  ```
- 产物**不含安装器 / 签名 / 公证**——那属发布流程，本任务只到「可运行的产物目录」（[`T-UI-002.3`](../项目管理/tasks/T-UI-002.3-打包与产物.md) 的「不交付」段）。
- 「无 Python 环境」以**清空 `PYTHON*` + 非仓库 `cwd`** 逼近（构建机装着 Python，无法真造无 Python 机器）；真机未验的残余风险记在任务假设里。
- 壳的通知能力取决于系统（Windows toast / macOS `osascript` / Linux `notify-send`）；**业务通知链**不在本任务，见 [`T-L5-002`](../项目管理/tasks/T-L5-002-渠道适配器投递编排与升级链.md)。
