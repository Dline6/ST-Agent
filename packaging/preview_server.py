"""应用内 Browser 预览用的开发启动器。

在**临时工作目录**里跑真实后端：这样仓库根的 `.env` 不会被引导装载（否则后端会索取
凭据口令，预览启动器无法应答）。仅用于本地预览 / 走查，不属于发布产物。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
WORK = REPO / "tmp" / "preview-cwd"
WORK.mkdir(parents=True, exist_ok=True)
os.chdir(WORK)
sys.path.insert(0, str(REPO / "src"))

from st_agent.__main__ import main  # noqa: E402

raise SystemExit(main(["--root", "root", "--port", "8765", "--dev"]))
