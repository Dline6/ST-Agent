"""确定性系统提示词（[`T-AGT-031.1`](../../../项目管理/tasks/T-AGT-031.1-系统提示词.md)；[05 §10](../../../../docs/技术架构-v2/05-L3-对话主入口.md)）。

每轮 LLM 请求携带一份**逐字节确定性**的系统提示词：角色中性、输出契约、当前日期 /
用户时区、数据时效、歧义处理、收敛条件与工具使用规则。时钟由调用方注入（组合根在
调用时刻取用户本地时区），本模块**不读时钟**，故同一入参两次装配结果一致。

提示词**不落盘明文**：用量记录只留版本（:data:`SYSTEM_PROMPT_VERSION`）与长度。
端点不接受 ``system`` 角色时由 L0 以确定性前缀并入 user 消息并标注（02 §4），
本模块不感知传输形态。
"""

from __future__ import annotations

from datetime import datetime, timedelta, tzinfo

from st_agent.l3.errors import AgentRuntimeValidationError

__all__ = ["SYSTEM_PROMPT_VERSION", "build_system_prompt"]

SYSTEM_PROMPT_VERSION = "1"
"""系统提示词版本（格式或规则变化时递增；旧版本号留在历史用量记录里）。"""


def build_system_prompt(*, now: datetime) -> str:
    """装配一份确定性系统提示词。

    :param now: 调用时刻（**必须带时区**；用户本地时区）。测试注入固定时钟即可
        得到逐字节一致的结果。
    :raises AgentRuntimeValidationError: ``now`` 无时区语义。
    """
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise AgentRuntimeValidationError("系统提示词需要带时区的时钟（01 §8）")
    date_text = now.date().isoformat()
    zone_text = _utc_offset(now.tzinfo, now)
    return (
        "ST Agent 查证运行时的系统提示词（版本 "
        f"{SYSTEM_PROMPT_VERSION}）。\n"
        "\n"
        "角色与边界：\n"
        "- 保持中性：不拟人化、不使用人名、不表达情感，不给出投资建议或方向研判。\n"
        "- 只依据工具结果与用户提供的信息作答；未取到的数据如实说明不可用。\n"
        "\n"
        "输出契约：\n"
        "- 需要取证时先调用工具，不以记忆或推测代替工具结果。\n"
        "- 引用结果时给出工具名、关键字段与数据时间（fetched_at / as_of）；"
        "本地数据标注其时效。\n"
        "\n"
        "歧义处理：\n"
        "- 信息不足时先声明假设并继续，或调用 sk_ask_user 提问；不自行臆造参数。\n"
        "\n"
        "收敛与结束：\n"
        "- 证据足够或无法继续时结束循环，给出带来源的结论，并说明未覆盖的部分。\n"
        "- 单个工具失败不终止循环，可改选其他能力；重复失败时如实说明。\n"
        "\n"
        "工具与上下文：\n"
        "- 工具目录是唯一可选来源，不调用目录外工具。\n"
        "- 被截断的结果按回读提示使用 sk_result_read 分片取回，不把预览当作完整结果。\n"
        "\n"
        "时间锚点：\n"
        f"- 当前日期：{date_text}；用户时区：{zone_text}。\n"
        "- 数据时效以工具返回的 as_of / fetched_at 为准。\n"
    )


def _utc_offset(zone: tzinfo, moment: datetime) -> str:
    """固定的 UTC 偏移文本（如 ``UTC+08:00``；不随平台本地化而变化）。"""
    offset: timedelta | None = moment.utcoffset()
    if offset is None:
        return "未知"
    total = int(offset.total_seconds())
    sign = "+" if total >= 0 else "-"
    hours, remainder = divmod(abs(total), 3600)
    return f"UTC{sign}{hours:02d}:{remainder // 60:02d}"
