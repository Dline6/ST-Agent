"""平台能力的离线分级声明（02 §7 的**出厂声明**）。

02 §7 要求「每项平台能力声明 ``offline_level``，产品在离线模式下按声明渲染
『可用 / 受限 / 不可用』清单」。声明本体由 :meth:`EgressGateway.register_capability`
落在 ``config`` 分区——但**注册动作此前只有测试在调**，故真实装配下
``offline_report()`` 恒空，[11-sitemap §3.9] 的离线横幅三类清单会是一片空白。

本模块补上出厂声明：一张**常量表** + 一个幂等播种函数。播种**只补缺失项**
（`register_capability` 遇已存在即抛，故先比对后登记），因此第二次装配不重置任何
用户改动——与「构造不写盘（官方 Pack 播种除外，幂等）」的既有口径一致。

清单取自 [story-05](../../../docs/PRD-v2-Agent/story-05-local-first.md) 的离线口径与
[02 §7](../../../docs/技术架构-v2/02-L0-本地优先基座.md) 的三级表，**不是**对各实现
的探测结论——故条目用中性名称与能力语义，不绑定具体模块。
"""

from __future__ import annotations

__all__ = ["DEFAULT_CAPABILITIES", "seed_default_capabilities"]

DEFAULT_CAPABILITIES: tuple[tuple[str, str, str, str], ...] = (
    # (capability_id, display_name, offline_level, degrade_note)
    ("memory-rw", "记忆读写（Memory 图谱浏览与编辑）", "full", ""),
    ("cached-data", "已缓存市场数据查询", "full", ""),
    ("local-notify", "本地通知", "full", ""),
    ("chat-history", "对话历史检索", "full", ""),
    ("cached-skill", "已缓存 Skill 的运行", "full", ""),
    (
        "cloud-llm",
        "云端 LLM 推理",
        "degraded",
        "断网时不可用；若已配置本地推理端点则改用本地端点，未配置则该项不可用",
    ),
    (
        "market-snapshot",
        "市场数据更新",
        "degraded",
        "数据停留在最后一次成功快照并标注过期时间，不再拉取新数据",
    ),
    ("live-fetch", "新数据抓取（公告 / 行情 / 舆情等）", "none", ""),
    ("net-search", "联网搜索", "none", ""),
    ("web-fetch", "网页抓取", "none", ""),
    ("remote-mcp", "远程 MCP Server", "none", ""),
    ("cloud-channel", "云端渠道投递（邮件 / IM Webhook）", "none", ""),
)
"""出厂声明表：``(capability_id, display_name, offline_level, degrade_note)``。

``degraded`` 条目**必带**降级标注（[`CapabilityRecord`](models.py) 的模型校验要求）。
"""


def seed_default_capabilities(gateway) -> tuple[str, ...]:
    """把出厂声明补进网关（**只补缺失**）；返回本次新登记的 ``capability_id``。

    幂等：已登记的条目原样保留（用户或后续任务改过的分级不被重置）。
    """
    existing = {r.capability_id for r in gateway.list_capabilities()}
    added: list[str] = []
    for capability_id, display_name, level, note in DEFAULT_CAPABILITIES:
        if capability_id in existing:
            continue
        gateway.register_capability(capability_id, display_name, level, note)
        added.append(capability_id)
    return tuple(added)
