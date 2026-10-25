"""启动引导配置（[02 §2.1 / §4](../../../docs/技术架构-v2/02-L0-本地优先基座.md)；[D-129]）。

两类配置先于「打开存储」与「运行期配置注册表」存在，故单列在此：

- **存储根指针**——决定打开哪个存储；落用户级 ``bootstrap.json``，**在存储根之外**。
- **LLM 引导三键**——``base_url`` / ``model`` 持久化于 ``config`` 分区，``.env`` 为可编辑的
  引导输入；``api_key`` 的凭据本体只落加密凭据库。

本包**不读不写任何用户业务数据**，只处理这两个启动期文件。
"""

from __future__ import annotations

__all__ = ["BootstrapConfigError"]


class BootstrapConfigError(Exception):
    """启动引导配置损坏或不可读写（显式失败，不静默回退）。"""
