"""``python -m st_agent.ui`` —— 本机回环服务的手动入口。

不注册 ``console_scripts``：组合根归属与启动方式随 `T-UI-002`（桌面壳与打包）再定，
本叶只提供开发 / 验收期可用的一条命令（[D-060] ④I 的「浏览器直开」）。
"""

from __future__ import annotations

import argparse
import threading
from collections.abc import Sequence

from st_agent.ui.server import serve

__all__ = ["main"]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m st_agent.ui",
        description="ST Agent 本机回环界面（后端常驻，UI 为可分离客户端）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="绑定地址（只应绑回环）")
    parser.add_argument("--port", type=int, default=0, help="0 = 由 OS 分配")
    parser.add_argument("--dev", action="store_true", help="启用 dev 面（发布构建不含）")
    args = parser.parse_args(argv)

    running = serve(host=args.host, port=args.port, dev=args.dev)
    url = f"{running.base_url}/"
    if args.dev:
        from st_agent.ui import dev  # noqa: PLC0415

        url = dev.browser_url(running)
    print(f"本机界面：{url}", flush=True)
    print("按 Ctrl-C 退出。", flush=True)

    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        pass
    finally:
        running.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover - 手动入口
    raise SystemExit(main())
