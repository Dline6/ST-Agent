"""T-SC-001.3 测试：01-平台共享契约 §6 中性化校验（Neutrality Guard）。"""

import pytest

from st_agent.contracts import NeutralityGuard, default_rulepack

LEGAL_NAMES = ["机会视角", "流动性视角", "基本面分析", "退市风险扫描", "情绪与资金流向分析"]


class TestSection6NeutralityGuard:
    """GWT-4：任一对外可见文本过中性化校验，拟人化表达被判失败。"""

    # ── 执行点 1：命名校验 ──────────────────────────────────────────
    def test_legal_functional_names_pass(self):
        """GWT-4a：合法中性命名通过（PRD 例 + 官方 Pack 风格）。"""
        guard = NeutralityGuard()
        for name in LEGAL_NAMES:
            assert guard.check_name(name).passed, name

    @pytest.mark.parametrize("bad", ["老张", "小李", "张三视角"])
    def test_person_name_rejected(self, bad):
        """人名 ❌（story-04：「老张」「小李」）。"""
        verdict = NeutralityGuard().check_name(bad)
        assert not verdict.passed

    @pytest.mark.parametrize("bad", ["激进派", "保守派", "乐观派视角"])
    def test_personality_tag_rejected(self, bad):
        """性格标签 ❌（story-04：「激进派」「保守派」）。"""
        verdict = NeutralityGuard().check_name(bad)
        assert not verdict.passed
        assert verdict.findings[0].kind == "personality_tag"

    @pytest.mark.parametrize("bad", ["股票分析师", "投资大师", "决策专家", "小助理"])
    def test_anthro_suffix_rejected(self, bad):
        """拟人后缀 ❌（把能力拟作职位/人）。"""
        verdict = NeutralityGuard().check_name(bad)
        assert not verdict.passed
        assert verdict.findings[0].kind == "anthro_suffix"

    def test_description_first_person_and_emotion_rejected(self):
        """描述文本禁用第一人称与情感动词（执行点 1 对 description 的要求）。"""
        guard = NeutralityGuard()
        assert not guard.check_name("机会视角", description="我认为它擅长挖掘").passed
        assert not guard.check_name("机会视角", description="担心错过机会时使用").passed
        assert guard.check_name("机会视角", description="识别潜在机会的视角").passed

    # ── 执行点 2：输出校验 ──────────────────────────────────────────
    @pytest.mark.parametrize("bad", [
        "我认为该股存在退市风险",
        "我担心流动性枯竭",
        "你说得对，但是估值偏高",
        "咱们来看一下这个信号",
    ])
    def test_output_personification_blocked(self, bad):
        """第一人称/情感表达/对话体 → 判失败，阻断渲染。"""
        verdict = NeutralityGuard().check_output(bad)
        assert not verdict.passed
        assert all(f.hint for f in verdict.findings)   # 每个命中带中性建议

    def test_output_neutral_pass(self):
        """中性输出（story-04 合规句式）通过。"""
        text = "从流动性视角看，证据显示换手率连续三日在板块后 10% 分位。"
        assert NeutralityGuard().check_output(text).passed

    def test_lens_opinion_style_reasons_pass(self):
        """LensOpinion key_reasons 的陈述式理由（无第一人称）通过。"""
        for reason in ("营收连续两季下滑", "经营现金流为负", "审计意见为保留"):
            assert NeutralityGuard().check_output(reason).passed

    # ── 执行点 3：命名建议 ──────────────────────────────────────────
    def test_suggest_only_neutral_candidates(self):
        """生成器只产中性候选名：违规 seed 得到合规建议。"""
        guard = NeutralityGuard()
        for cand in guard.suggest_names("激进派"):
            assert guard.check_name(cand).passed
        for cand in guard.suggest_names("股票分析师"):
            assert guard.check_name(cand).passed

    def test_suggest_pass_through_legal_seed(self):
        """合规 seed 保留为首选建议。"""
        cands = NeutralityGuard().suggest_names("流动性")
        assert cands[0] == "流动性"
        assert all(NeutralityGuard().check_name(c).passed for c in cands)

    # ── 校验点不可配置关闭 ──────────────────────────────────────────
    def test_guard_has_no_bypass_switch(self):
        """API 形状强制：构造参数只有规则库，无 enabled/bypass。"""
        import inspect
        sig = inspect.signature(NeutralityGuard.__init__)
        assert set(sig.parameters) == {"self", "rulepack"}

    def test_rulepack_updatable_but_checkpoints_stable(self):
        """规则库可整体替换（官方 Pack 更新路径），校验点方法不动。"""
        custom = default_rulepack()
        guard = NeutralityGuard(custom)
        assert guard.check_output("我认为不行").passed is False
        # 换库后三个执行点仍为同一组方法
        assert callable(guard.check_name) and callable(guard.check_output) \
            and callable(guard.suggest_names)
