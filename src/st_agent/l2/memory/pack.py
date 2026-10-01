"""Onboarding 问题清单的官方资源包接线（[01 §13](../../../docs/技术架构-v2/01-平台共享契约.md)）。

把 [04 §7](../../../docs/技术架构-v2/04-L2-记忆图谱.md) 的**官方缺省**问题清单
（:data:`DEFAULT_ONBOARDING_QUESTIONS`）包成官方资源条目，交给容器按 ``kind`` 分发；
消费面是 :meth:`OnboardingProtocol.set_official_default`。

清单同时是 [01 §7](../../../docs/技术架构-v2/01-平台共享契约.md) 的配置条目
（``memory-policy/onboarding-questions``）——官方 Pack 只供其**缺省**，登记项的三能力
（双通道可改 / 变更留痕 / 可回滚）不因缺省来源改变而降级。

L2 在 L1 之上，向容器（`st_agent.l1.pack`）贡献条目属**向下依赖**（铁律 7）。
"""

from __future__ import annotations

from st_agent.contracts.registry_types import SemVer
from st_agent.l1.pack import ResourceEntry
from st_agent.l2.memory.onboarding import DEFAULT_ONBOARDING_QUESTIONS

__all__ = ["ONBOARDING_KIND", "official_onboarding_entry"]

ONBOARDING_KIND = "onboarding_questions"
"""Onboarding 问题清单的 kind（[01 §13](../../../docs/技术架构-v2/01-平台共享契约.md) kinds 表）。"""


def official_onboarding_entry() -> ResourceEntry:
    """官方 Onboarding 问题清单条目（payload ＝ :data:`DEFAULT_ONBOARDING_QUESTIONS`，引用非复制）。"""
    return ResourceEntry(
        kind=ONBOARDING_KIND, version=SemVer(major=1, minor=0),
        payload=DEFAULT_ONBOARDING_QUESTIONS,
    )
