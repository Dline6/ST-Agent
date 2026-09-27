"""L2 · 个人投资记忆图谱（04-L2；Story 3）。

个性化核心：一张**本地、可查、可编辑、可导出**的结构化图谱。所有 Skill 调用
读它（个性化推理）、写它（新事件/观点/反馈）。物理存储在 L0 ``memory`` 分区
（[02 §2.1](../../docs/技术架构-v2/02-L0-本地优先基座.md)）。

- :mod:`st_agent.l2.memory.models` —— 六类节点 / 四类边的本体（04 §1–§2）
- :mod:`st_agent.l2.memory.graph` —— ``MemoryGraph`` 图谱门面与 ``memory`` 分区落盘
- :mod:`st_agent.l2.memory.writer` —— ``MemoryWriter`` 写入接口（04 §3.2）
- :mod:`st_agent.l2.memory.reader` —— ``MemoryReader`` 上下文切片查询（04 §3.1）

对外统一从 ``st_agent.l2.memory`` import。
"""
