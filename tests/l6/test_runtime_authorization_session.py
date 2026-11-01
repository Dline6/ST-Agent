"""会话级运行期授权覆盖（[`T-AGT-028.1`](../项目管理/tasks/T-AGT-028.1-会话级覆盖与三档切换.md) / [01 §7](../../docs/技术架构-v2/01-平台共享契约.md)）。

GWT 对照：

- GWT-1：会话内切到 `manual` ⇒ 下一步**只读**动作也需确认（覆盖优先于全局档）
- GWT-2：切回 `autonomous` ⇒ 只读自动、写仍需确认
- GWT-3：覆盖是**进程内瞬态**——不落盘、不产 `change_id`、不改全局条目；进程结束即消失

另钉：覆盖只作用于**该会话**（别的会话 / 不带会话仍按全局档）；空会话标识与非法档位显式拒绝；
红线动作在任何档位下都不放开。
"""

from __future__ import annotations

import inspect

import pytest

from l6_helpers import NOW, PASS
from st_agent.l0.storage import Store
from st_agent.l6 import (
    AGENT_CHANGE_PREFIX,
    AGENT_TIER_CONFIG_ID,
    MEMORY_WRITE_ACTION,
    AuthorizationError,
    EvolutionAuthorization,
    RuntimeAuthorization,
    skill_action_id,
)

READ_ONLY = skill_action_id("sk_local_sql")
"""出厂清单显式登记为只读的内置原语（`autonomous-ok`，[D-143]）。"""

WRITE = skill_action_id("sk_local_write")
"""未登记的写类原语（`skill.` 通则判须协作）。"""

UNREGISTERED = skill_action_id("sk_something_new")
"""清单未命中的动作（保守：须协作）。"""

SESSION = "sess_0000000000000001"
OTHER_SESSION = "sess_0000000000000002"


def _runtime(tmp_path) -> tuple[Store, RuntimeAuthorization]:
    store = Store.create(tmp_path / "root", PASS)
    authz = EvolutionAuthorization(store=store, now=lambda: NOW)
    return store, RuntimeAuthorization(store=store, grading=authz, now=lambda: NOW)


def _change_files(store: Store) -> list[str]:
    return [
        name for name in store.list_files("execution_log")
        if name.startswith(AGENT_CHANGE_PREFIX)
    ]


# ───────────────────── GWT-1 · manual 覆盖让只读动作也需确认 ─────────────────────


def test_gwt1_manual_override_makes_read_only_need_confirmation(tmp_path):
    _store, runtime = _runtime(tmp_path)
    assert runtime.decide(READ_ONLY).verdict == "autonomous-ok"   # 全局出厂 autonomous

    runtime.set_session_tier(SESSION, "manual")
    decision = runtime.decide(READ_ONLY, session_id=SESSION)

    assert decision.verdict == "needs-confirmation"
    assert decision.tier == "manual"
    assert decision.risk_class == "autonomous-ok"


def test_override_only_affects_its_own_session(tmp_path):
    _store, runtime = _runtime(tmp_path)
    runtime.set_session_tier(SESSION, "manual")

    assert runtime.decide(READ_ONLY, session_id=OTHER_SESSION).verdict == "autonomous-ok"
    assert runtime.decide(READ_ONLY).verdict == "autonomous-ok"   # 无会话的单次查证
    assert runtime.session_tier(OTHER_SESSION) is None
    assert runtime.effective_tier(OTHER_SESSION) == "autonomous"


# ───────────────────── GWT-2 · 切回 autonomous 的边界 ─────────────────────


def test_gwt2_switching_back_restores_read_only_but_not_writes(tmp_path):
    _store, runtime = _runtime(tmp_path)
    runtime.set_session_tier(SESSION, "manual")
    assert runtime.decide(READ_ONLY, session_id=SESSION).verdict == "needs-confirmation"

    runtime.set_session_tier(SESSION, "autonomous")

    assert runtime.decide(READ_ONLY, session_id=SESSION).verdict == "autonomous-ok"
    assert runtime.decide(WRITE, session_id=SESSION).verdict == "needs-confirmation"
    assert runtime.decide(UNREGISTERED, session_id=SESSION).verdict == "needs-confirmation"


def test_collaborative_override_keeps_everything_confirming(tmp_path):
    _store, runtime = _runtime(tmp_path)
    runtime.set_session_tier(SESSION, "collaborative")

    assert runtime.decide(READ_ONLY, session_id=SESSION).verdict == "needs-confirmation"
    assert runtime.decide(WRITE, session_id=SESSION).verdict == "needs-confirmation"


def test_red_line_is_denied_in_every_tier(tmp_path):
    _store, runtime = _runtime(tmp_path)
    for tier in ("manual", "collaborative", "autonomous"):
        runtime.set_session_tier(SESSION, tier)
        assert runtime.decide(MEMORY_WRITE_ACTION, session_id=SESSION).verdict == "denied"


# ───────────────────── GWT-3 · 瞬态、不落盘、不改全局 ─────────────────────


def test_gwt3_override_leaves_the_global_entry_and_change_log_untouched(tmp_path):
    store, runtime = _runtime(tmp_path)
    runtime.set_session_tier(SESSION, "manual")

    assert runtime.tier() == "autonomous"                 # 全局档未被改
    assert runtime.tier_source() == "default"             # 仍取出厂缺省，不冒充用户选择
    assert runtime.changes() == ()                        # 不产 change_id
    assert _change_files(store) == []                     # `agent-change/` 零落盘
    assert runtime.entry(AGENT_TIER_CONFIG_ID).default == "autonomous"
    assert runtime.effective_tier(SESSION) == "manual"    # 只有该会话的生效值变了


def test_gwt3_override_disappears_with_the_process(tmp_path):
    store, runtime = _runtime(tmp_path)
    runtime.set_session_tier(SESSION, "manual")

    fresh = RuntimeAuthorization(store=store, grading=None, now=lambda: NOW)

    assert fresh.session_tier(SESSION) is None
    assert fresh.effective_tier(SESSION) == "autonomous"


def test_clear_returns_the_session_to_the_global_tier(tmp_path):
    _store, runtime = _runtime(tmp_path)
    runtime.set_session_tier(SESSION, "manual")

    assert runtime.clear_session_tier(SESSION) is True
    assert runtime.session_tier(SESSION) is None
    assert runtime.effective_tier(SESSION) == "autonomous"
    assert runtime.clear_session_tier(SESSION) is False   # 重复清除：无覆盖可清


# ───────────────────── 取值与能力标记 ─────────────────────


def test_empty_session_id_is_refused(tmp_path):
    _store, runtime = _runtime(tmp_path)
    with pytest.raises(AuthorizationError):
        runtime.set_session_tier("  ", "manual")
    assert runtime.session_tier("") is None
    assert runtime.clear_session_tier("") is False


def test_illegal_tier_is_refused(tmp_path):
    _store, runtime = _runtime(tmp_path)
    with pytest.raises(AuthorizationError):
        runtime.set_session_tier(SESSION, "god-mode")
    assert runtime.session_tier(SESSION) is None


def test_judgement_accepts_a_session_argument(tmp_path):
    """组合根按**签名**识别「该判据面能按会话判」（L3 闸门零改动）。"""
    _store, runtime = _runtime(tmp_path)
    assert "session_id" in inspect.signature(runtime.decide).parameters
