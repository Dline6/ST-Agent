"""L0 存储 / 配置门面。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SettingsFacade:
    """L0 存储 / 配置面：持 01 §7 配置注册表门面与本地存储句柄（[`T-UI-018`] 扩为全量）。

    :param config_registry: 统一配置注册表门面（[01 §7]；各设置项经它落值）
    :param store: 本地存储句柄（位置 / 格式 / 分区用量 / 凭据分区锁定态）
    :param gateway: 出网网关（网络活动留痕、离线清单与在线态）
    :param backup: ``st_agent.l0.backup`` 模块（备份 / 恢复 / 清空与留存策略的**本体**）
    :param endpoints: LLM 端点注册表（设置-LLM 提供方页的端点读面）
    :param vault: 凭据库（掩码视图与使用记录；明文不出站）
    :param exports_dir: 表现层导出目录（网络活动报告落点）
    :param backups_dir: 缺省归档落点——**存储根之外**（清空时不会被一并删掉）
    """

    config_registry: Any
    store: Any = None
    gateway: Any = None
    backup: Any = None
    endpoints: Any = None
    vault: Any = None
    exports_dir: Any = None
    backups_dir: Any = None
    set_online: Any = None
    convert_store: Any = None
    """明密双向转换口（``Store.convert`` 类方法）——**源不动、写新根**（02 §2.2）。"""
    """在线态翻转口（``L1Runtime.set_online``）——**带离线补跑**：由离翻回在线时按策略
    处置待补到期点（03 §6）。表现层经它把浏览器的 `online` / `offline` 事件同步过来。"""
    llm_runtime: Any = None
    """L1 运行时（设置页改 LLM 引导配置时调 :meth:`L1Runtime.apply_llm_bootstrap`）。"""
    dotenv_path: Any = None
    """启动引导 ``.env`` 的实际路径（读面显示、写面原地改写；[D-129]）。"""
    root_boot: Any = None
    """:class:`~st_agent.l0.bootstrap.root.RootBoot`——存储根指针的读写面。"""
    data_sync: Any = None
    """:class:`~st_agent.l0.sync_runner.DataSyncRunner`——数据总览与手动同步。"""
