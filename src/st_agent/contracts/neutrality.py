"""01-平台共享契约 §6 中性化校验（Neutrality Guard）。

契约要点（§6）——产品硬约束「严格中性、非拟人化」的契约级实现，三个**强制执行点**，
任何实现不得省略：

1. **命名校验**（保存自定义视角、自建 Skill、快捷指令命名时）：
   名称必须为功能化描述（如「机会视角」「流动性视角」）；
   拒绝人名、性格标签（如「激进派」）、拟人后缀；
   描述文本禁用第一人称与情感动词
2. **输出校验**（LensOpinion、Skill 文案、推送文案渲染前）：
   禁用第一人称代词、情感表达（「我担心/我认为/你说得对」）、对话体措辞；
   命中即阻断渲染并记录校验失败
3. **命名建议**（对话生成配置草稿、新建视角引导时）：生成器只产中性候选名

规则库（否定词表 + 结构校验）可随官方 Pack 更新，但**校验点本身不可配置关闭**
（``checkpoints`` 无开关参数、``NeutralityGuard`` 构造不接受绕行标志——
这是 API 形状上的强制，而非运行时可调项）。合规背景见 PRD §7（规避「AI 荐股」定性，
docs/PRD-v2-Agent/05-constraints.md）。

词表初版口径来自 PRD 明文禁止项（story-04 核心约束 / 10-platform-capabilities 契约 9）：
人名、性格标签、第一人称（我认为）、情感表达（我担心）、对话体（你说得对）、
拟人后缀（师/家/官/派/Agent 人格化用法）。
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = [
    "NeutralityFinding",
    "NeutralityGuard",
    "NeutralityVerdict",
    "RULEPACK_KIND",
    "default_rulepack",
    "official_rulepack",
    "set_official_rulepack",
]

RULEPACK_KIND = "rulepack"
"""中立性规则库的官方资源包 kind（[01 §13](技术架构-v2/01-平台共享契约.md)）。

规则库的「官方缺省」经官方 Pack 的该 kind 承载、由装配层注入（见
:func:`set_official_rulepack`）——本模块是最底层，不自持 Pack（[铁律 7]）。
"""

ViolationKind = Literal[
    "person_name",      # 人名
    "personality_tag",  # 性格标签
    "first_person",     # 第一人称
    "emotion",          # 情感表达
    "dialogue",         # 对话体
    "anthro_suffix",    # 拟人后缀
]

class NeutralityFinding(BaseModel):
    """一次命中的违规（供 UI 阻断提示与校验失败留痕）。"""

    model_config = ConfigDict(frozen=True)

    kind: ViolationKind
    matched: str
    position: int
    hint: str
    """中性化修改建议（中性措辞，指向合规写法而非指责）。"""


class NeutralityVerdict(BaseModel):
    """校验结论。``passed=False`` 时 findings 必非空（阻断渲染）。"""

    model_config = ConfigDict(frozen=True)

    passed: bool
    findings: tuple[NeutralityFinding, ...] = ()


class RulePack:
    """否定词表 + 结构规则（可随官方 Pack 更新：换实例即可，校验点不动）。"""

    __slots__ = ("person_names", "person_name_pattern", "first_person", "emotion",
                 "dialogue", "personality_tags", "anthro_suffixes", "functional_hint")

    def __init__(self, *, person_names: tuple[str, ...], person_name_pattern: "re.Pattern[str]",
                 first_person: tuple[str, ...], emotion: tuple[str, ...],
                 dialogue: tuple[str, ...], personality_tags: tuple[str, ...],
                 anthro_suffixes: tuple[str, ...], functional_hint: str) -> None:
        self.person_names = person_names
        self.person_name_pattern = person_name_pattern
        self.first_person = first_person
        self.emotion = emotion
        self.dialogue = dialogue
        self.personality_tags = personality_tags
        self.anthro_suffixes = anthro_suffixes
        self.functional_hint = functional_hint


def default_rulepack() -> RulePack:
    """初版规则库：PRD 明文禁止项 + 高危拟人模式（官方 Pack 可整体替换）。"""
    return RulePack(
        # 人名词表（story-04 明例 + 常见称谓）
        person_names=("老张", "小李", "老王", "小刘", "张三", "李四", "王五",
                      "老师傅", "大佬", "大神"),
        # 结构规则：称呼前缀 + 姓氏（PRD「老张」「小李」的泛化模式）
        person_name_pattern=re.compile(r"(老|小|大)[王李张刘陈杨赵黄周吴徐孙马朱胡]"),
        # 第一人称代词（含常见变体）
        first_person=("我", "咱们", "人家", "本人", "俺"),
        # 情感表达动词/短语（PRD 例：「我担心」「我认为」中的情感部分）
        emotion=("担心", "害怕", "欣慰", "兴奋", "懊悔", "喜欢", "讨厌",
                 "我相信", "我确信", "很遗憾", "不幸地"),
        # 对话体措辞（PRD 例：「你说得对，但是」）
        dialogue=("你说得对", "你说得没错", "正如您所说", "谢谢提问", "请问",
                  "好的，", "没问题，", "让我来"),
        # 性格标签（story-04：激进派/保守派 ❌）
        personality_tags=("激进派", "保守派", "乐观派", "悲观派", "稳健派",
                          "老练", "胆大", "谨慎人", "急性子"),
        # 拟人后缀：把能力拟作职位/人（师/家/官/员/士 等接在能力名后）
        anthro_suffixes=("分析师", "投资顾问", "顾问", "专家", "大师", "老手",
                         "助理", "秘书", "管家", "操盘手"),
        functional_hint="改用功能化描述，如「机会视角」「流动性视角」「基本面分析」",
    )


_official_rulepack: "RulePack | None" = None
_builtin_rulepack: "RulePack | None" = None


def set_official_rulepack(rulepack: "RulePack | None") -> None:
    """装配层设置**官方 Pack** 的规则库（[01 §13](技术架构-v2/01-平台共享契约.md)）。

    ``None`` 清除、回到内置缺省。该设置对**全部**未显式传规则库的
    :class:`NeutralityGuard` 生效——含 import 期即构造的模块级常量（如 L4 的
    ``_OUTPUT_CHECK``），故 :class:`NeutralityGuard` **取用时**解析，而非构造时固化。

    本模块是最底层、不自持 Pack（[铁律 7]）——值由装配层（组合根）经官方 Pack 的
    ``rulepack`` kind loader 注入。
    """
    global _official_rulepack
    _official_rulepack = rulepack


def official_rulepack() -> "RulePack | None":
    """当前生效的官方 Pack 规则库；未设置 → ``None``（回落到内置缺省）。"""
    return _official_rulepack


def _resolved_rulepack() -> "RulePack":
    """未显式传规则库时的取值面：官方 Pack 规则库优先，否则内置缺省。"""
    global _builtin_rulepack
    if _official_rulepack is not None:
        return _official_rulepack
    if _builtin_rulepack is None:
        _builtin_rulepack = default_rulepack()
    return _builtin_rulepack


class NeutralityGuard:
    """§6 校验器。三个强制执行点各为一个方法；**不存在启用/关闭开关**。

    - ``check_name``：命名校验（执行点 1）
    - ``check_output``：输出校验（执行点 2）
    - ``suggest_names``：命名建议（执行点 3，只产中性候选）
    """

    def __init__(self, rulepack: RulePack | None = None) -> None:
        # 校验点不可配置关闭：构造参数只有规则库，无 bypass/enabled 形参。
        # rulepack 为 None 时**取用时**解析（官方 Pack 规则库优先，否则内置缺省）
        # ——见 :func:`set_official_rulepack` 与 ``_rules``。
        self._explicit = rulepack

    @property
    def _rules(self) -> RulePack:
        """生效规则库：显式传入者优先；否则官方 Pack（经注入）→ 内置缺省。

        取用时解析，故装配层设置官方规则库对其后**及之前**构造的守卫都生效——
        含 import 期固化的模块级常量。
        """
        return self._explicit if self._explicit is not None else _resolved_rulepack()

    # ── 执行点 1：命名校验 ──────────────────────────────────────────
    def check_name(self, name: str, *, description: str | None = None) -> NeutralityVerdict:
        """名称必须为功能化描述；描述文本禁用第一人称与情感动词。"""
        findings: list[NeutralityFinding] = []
        findings += self._scan(name, kinds=("person_name", "personality_tag", "anthro_suffix"))
        if description:
            findings += self._scan(description, kinds=("first_person", "emotion", "dialogue"))
        return NeutralityVerdict(passed=not findings, findings=tuple(findings))

    # ── 执行点 2：输出校验 ──────────────────────────────────────────
    def check_output(self, text: str) -> NeutralityVerdict:
        """LensOpinion / Skill 文案 / 推送文案渲染前调用；命中即阻断渲染。"""
        findings = self._scan(text, kinds=("first_person", "emotion", "dialogue"))
        return NeutralityVerdict(passed=not findings, findings=tuple(findings))

    # ── 执行点 3：命名建议 ──────────────────────────────────────────
    def suggest_names(self, seed: str) -> tuple[str, ...]:
        """只产中性候选名：若 seed 本身合规，返回其 + 功能化变体；
        若不合规，返回纯功能化候选（不含违规片段）。"""
        verdict = self.check_name(seed)
        base = seed if verdict.passed else self.functional_fallback(seed)
        candidates = [base, f"{base}视角", f"{base}分析"]
        return tuple(dict.fromkeys(c for c in candidates if self.check_name(c).passed))

    def functional_fallback(self, text: str) -> str:
        """给出中性替代：剔除违规片段后保留的功能化主干（供拒绝时的建议）。"""
        cleaned = self._rules.person_name_pattern.sub("", text)
        for tok in (*self._rules.person_names, *self._rules.personality_tags,
                    *self._rules.anthro_suffixes, *self._rules.first_person,
                    *self._rules.emotion, *self._rules.dialogue):
            cleaned = cleaned.replace(tok, "")
        cleaned = re.sub(r"^[（(【\[]+|[）)】\]]+$", "", cleaned).strip("　·-— ")
        return cleaned or "功能化命名"

    # ── 内部：词表扫描 ──────────────────────────────────────────────
    def _scan(self, text: str, *, kinds: tuple[str, ...]) -> list[NeutralityFinding]:
        findings: list[NeutralityFinding] = []
        tables: dict[str, tuple[str, ...]] = {
            "person_name": self._rules.person_names,
            "personality_tag": self._rules.personality_tags,
            "anthro_suffix": self._rules.anthro_suffixes,
            "first_person": self._rules.first_person,
            "emotion": self._rules.emotion,
            "dialogue": self._rules.dialogue,
        }
        for kind in kinds:
            for tok in tables[kind]:
                start = 0
                while (idx := text.find(tok, start)) != -1:
                    findings.append(NeutralityFinding(
                        kind=kind,  # type: ignore[arg-type]
                        matched=tok, position=idx, hint=self._rules.functional_hint,
                    ))
                    start = idx + len(tok)
            if kind == "person_name":
                # 结构规则：称呼前缀 + 姓氏（老张/小李 泛化）
                for m in self._rules.person_name_pattern.finditer(text):
                    findings.append(NeutralityFinding(
                        kind="person_name", matched=m.group(), position=m.start(),
                        hint=self._rules.functional_hint,
                    ))
        return findings
