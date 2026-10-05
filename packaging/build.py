"""逐平台构建与产物自检（[`T-UI-002.3`]）。

在仓库根跑：

```bash
python packaging/build.py --target all --smoke
```

- ``--target {server,shell,all}``：构建哪几个产物（后端 / 壳 / 两者）；
- ``--smoke``：构建后**用清空 ``PYTHON*`` 环境 + 非仓库 cwd** 真起一次后端二进制，读它的
  就绪握手再让它优雅退出——这是本机对「产物不依赖本机 Python 环境」能做到的最强逼近
  （真机无 Python 未验，见任务假设 `A2`）；
- ``--check-only``：只对既有 ``dist/`` 做自检，不重新构建。

**跨平台**：本脚本只构建**当前平台**的产物（[D-076](../../项目管理/决策日志.md) ②：Windows 实测 +
另两平台落口径）；macOS / Linux 的前置与命令见 [README](README.md)。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPECS = {"server": "st-agent-server.spec", "shell": "st-agent-shell.spec"}
_EXE_SUFFIX = ".exe" if os.name == "nt" else ""


# ───────────────────────── 构建 ─────────────────────────


def build(target: str, *, clean: bool) -> None:
    """按 spec 构建一个产物（``server`` / ``shell``）。"""
    spec = PACKAGING / SPECS[target]
    if clean:
        shutil.rmtree(DIST / Path(SPECS[target]).stem, ignore_errors=True)
    subprocess.run(
        [
            sys.executable, "-m", "PyInstaller",
            "--noconfirm",
            "--distpath", str(DIST),
            "--workpath", str(BUILD),
            str(spec),
        ],
        cwd=str(ROOT),
        check=True,
    )


# ───────────────────────── 自检 ─────────────────────────


def artifact_dir(target: str) -> Path:
    return DIST / Path(SPECS[target]).stem


def bundled_names(target: str) -> set[str]:
    """产物里**真打进去**的模块名（PYZ 归档内的点分名）。

    断言**模块**在不在产物里必须看这个：纯 Python 模块进的是可执行文件里的 PYZ 归档，
    ``_internal/`` 下没有 ``st_agent/`` 目录——按目录找会「一路通过」而什么都没验到。
    （**数据**文件相反：onedir 布局把它们落在 ``_internal/`` 下，那一半看磁盘，见 `_on_disk`。）
    """
    from PyInstaller.archive.readers import pkg_archive_contents      # noqa: PLC0415

    exe = artifact_dir(target) / f"{Path(SPECS[target]).stem}{_EXE_SUFFIX}"
    return {name.replace("/", ".") for name in pkg_archive_contents(str(exe))}


def _on_disk(root: Path, relative: str) -> bool:
    """随包**数据**文件是否在产物目录里（onedir 布局：数据落盘、模块进 PYZ 归档）。"""
    return any(
        path.is_file() and path.as_posix().endswith(relative)
        for path in root.rglob(Path(relative).name)
    )


def check(target: str) -> list[str]:
    """产物自检：可执行文件在、随包资产在、**dev 面不在**（壳产物另查「不含业务层」）。"""
    problems: list[str] = []
    root = artifact_dir(target)
    exe = root / f"{Path(SPECS[target]).stem}{_EXE_SUFFIX}"
    if not exe.exists():
        problems.append(f"缺可执行文件：{exe}")
        return problems
    try:
        names = bundled_names(target)
    except ImportError:                       # pragma: no cover - 构建机上恒有
        return ["自检需要 PyInstaller：pip install -e \".[packaging]\""]

    if target == "server":
        for relative, label in (
            ("st_agent/ui/web/index.html", "静态前端"),
            ("st_agent/l0/market/schema.sql", "市场库 DDL"),
        ):
            if not _on_disk(root, relative):
                problems.append(f"产物缺{label}（{relative}）")
    if any(name.startswith("st_agent.ui.dev") for name in names):
        problems.append("产物含 dev 面（[D-060] ④I 要求物理剔除）")
    if target == "shell":
        for layer in ("l0", "l1", "l2", "l3", "l4"):
            if any(name.startswith(f"st_agent.{layer}") for name in names):
                problems.append(f"壳产物含业务层 st_agent.{layer}（壳不含业务逻辑）")
        if any(name.startswith("st_agent.app") for name in names):
            problems.append("壳产物含组合根 st_agent.app（后端是另一个产物）")
    return problems


def smoke_server() -> list[str]:
    """清空 ``PYTHON*`` + 非仓库 cwd 起一次后端二进制，走一遍「就绪 → 取数 → 优雅停」。

    取数走 ``/api/health`` 与 ``/api/chat``：前者证回环面活着，后者证**真组合根**被接上
    （不是「未接入对话门面」的那条 fail-closed 分支）——冻结产物最容易漏的正是组合根里
    那几处**延迟导入**（见规格里的 `BUNDLE_MODULES`）。
    """
    exe = artifact_dir("server") / f"st-agent-server{_EXE_SUFFIX}"
    if not exe.exists():
        return [f"缺可执行文件：{exe}"]
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYTHON")}
    env.pop("PYTHONPATH", None)
    env["PYTHONIOENCODING"] = "utf-8"
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as workdir, \
            tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as store:
        proc = subprocess.Popen(
            [str(exe), "--root", store, "--handshake", "json", "--control-stdin"],
            cwd=workdir, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8",
        )
        try:
            line = proc.stdout.readline()          # type: ignore[union-attr]
            payload = json.loads(line) if line else {}
            if payload.get("event") != "ready":
                return [f"冻结产物未就绪：{line.strip()[:200] or '无输出'}"]
            problems = _smoke_endpoints(payload)
            if proc.stdin is not None:
                proc.stdin.write("stop\n")
                proc.stdin.flush()
                proc.stdin.close()
            if proc.wait(timeout=30) != 0:
                problems.append("冻结产物退出码非 0（优雅收尾未生效）")
            return problems
        except (json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
            proc.kill()
            return [f"冻结产物冒烟失败：{exc}"]


def _smoke_endpoints(payload: dict) -> list[str]:
    """对刚起来的冻结产物打两个端点（令牌走自定义头）。"""
    import urllib.error                            # noqa: PLC0415
    import urllib.request                          # noqa: PLC0415

    base = f"http://{payload['host']}:{payload['port']}"
    headers = {"X-ST-Token": str(payload["token"])}
    problems: list[str] = []
    try:
        request = urllib.request.Request(f"{base}/api/health", headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            health = json.loads(response.read().decode("utf-8"))
        if health.get("status") != "ok":
            problems.append(f"/api/health 非 ok：{health.get('status')}")

        body = json.dumps({"action": "post", "text": "你好"}).encode("utf-8")
        request = urllib.request.Request(
            f"{base}/api/chat", data=body, method="POST",
            headers={**headers, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            chat = json.loads(response.read().decode("utf-8"))
        if "未接入对话门面" in (chat.get("reason") or ""):
            problems.append("/api/chat 未接上真组合根（门面缺失）")
    except (urllib.error.URLError, json.JSONDecodeError) as exc:
        problems.append(f"端点冒烟失败：{exc}")
    return problems


# ───────────────────────── CLI ─────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python packaging/build.py", description=__doc__)
    parser.add_argument("--target", choices=("server", "shell", "all"), default="all")
    parser.add_argument("--clean", action="store_true", help="构建前清掉该产物的 dist 目录")
    parser.add_argument("--check-only", action="store_true", help="只自检既有产物")
    parser.add_argument("--smoke", action="store_true", help="构建后冒烟一次后端二进制")
    args = parser.parse_args(argv)

    targets = ("server", "shell") if args.target == "all" else (args.target,)
    failures: list[str] = []
    for target in targets:
        if not args.check_only:
            build(target, clean=args.clean)
        failures += [f"[{target}] {p}" for p in check(target)]
    if args.smoke and not args.check_only:
        failures += [f"[smoke] {p}" for p in smoke_server()]

    if failures:
        print("产物自检未通过：")
        for item in failures:
            print("[FAIL]", item)
        return 1
    print(f"产物自检通过：{', '.join(str(artifact_dir(t)) for t in targets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
