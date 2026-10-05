"""T-ECO-002.2 测试：官方 Skill 索引与生态边界（[09 §4](../../docs/技术架构-v2/09-生态与分享.md) · [09 §6](../../docs/技术架构-v2/09-生态与分享.md)）。

GWT 对照（任务文件 5 条）：
- GWT-1 索引浏览经 L0 网关以 `kind=index_browse` 发出并留痕（销 L0 册 `A1b`）
- GWT-2 条目含官方 Pack 更新与认证社区推荐（描述 + 外部下载地址 + 校验和），只读不下载
- GWT-3 外部下载地址只作展示与指引（本层不发生任何文件获取）
- GWT-4 空状态判据（未导入任何第三方 Skill）
- GWT-5 生态边界「不做」清单与 09 §6 逐条对应、措辞中性
"""

from __future__ import annotations

import json

import pytest

from eco_helpers import SHARER
from st_agent.contracts.capability_types import Provenance
from st_agent.eco import (
    ECOSYSTEM_BOUNDARY,
    EMPTY_STATE_TEXT,
    ShareIndexError,
    has_third_party,
)
from st_agent.eco.index import INDEX_FORMAT_ID, INDEX_INITIATOR, OfficialIndex

HOST = "index.example.com"
CHECKSUM = "a" * 64

DOCUMENT = json.dumps(
    {
        "format_id": INDEX_FORMAT_ID,
        "version": "1.0",
        "entries": [
            {
                "kind": "pack_update",
                "name": "官方 Pack",
                "description": "公共认知与主动服务两 Bundle 的例行更新",
                "version": "1.1",
                "changelog": "新增两项条件",
                "impact": "引用方标「待检查」",
            },
            {
                "kind": "community_skill",
                "name": "社区选股筛选",
                "description": "经认证的社区 Skill，输出筛选结果",
                "version": "1.0",
                "download_url": "https://example.com/sk.stskill",
                "checksum": CHECKSUM,
            },
        ],
    },
    ensure_ascii=False,
)


def _index(gateway, *, fetch=lambda: DOCUMENT) -> OfficialIndex:
    return OfficialIndex(gateway=gateway, host=HOST, fetch=fetch)


class TestGwt1Egress:
    """GWT-1：索引浏览是 `index_browse` 出网类目的**唯一调用点**（销 L0 册 `A1b`）。"""

    def test_browse_goes_through_gateway_and_is_audited(self, gateway):
        gateway.set_audit(True)

        entries = _index(gateway).browse()

        assert [e.kind for e in entries] == ["pack_update", "community_skill"]
        (event,) = gateway.query(kind="index_browse")
        assert event.kind == "index_browse"
        assert event.initiator == INDEX_INITIATOR
        assert event.target_host == HOST

    def test_audit_off_still_browses_but_leaves_no_record(self, gateway):
        """审计默认可关（[D-073]）——关的是留痕，不是浏览本身。"""
        assert gateway.audit_enabled is False

        assert len(_index(gateway).browse()) == 2
        assert gateway.query(kind="index_browse") == ()

    def test_offline_is_explicit(self, gateway):
        gateway.set_online(False)
        with pytest.raises(ShareIndexError, match="不可用"):
            _index(gateway).browse()

    def test_missing_gateway_is_explicit(self):
        with pytest.raises(ShareIndexError, match="EgressGateway"):
            OfficialIndex(host=HOST, fetch=lambda: DOCUMENT).browse()

    def test_missing_fetch_face_is_explicit(self, gateway):
        with pytest.raises(ShareIndexError, match="fetch"):
            OfficialIndex(gateway=gateway, host=HOST).browse()


class TestGwt2Entries:
    """GWT-2：条目三段齐（描述 + 外部下载地址 + 校验和）。"""

    def test_community_entry_carries_download_and_checksum(self, gateway):
        entries = _index(gateway).browse()
        community = entries[1]
        assert community.description
        assert community.download_url == "https://example.com/sk.stskill"
        assert community.checksum == CHECKSUM

    def test_community_entry_without_download_or_checksum_is_refused(self, gateway):
        for broken in (
            {"kind": "community_skill", "name": "x", "description": "d", "version": "1.0"},
            {
                "kind": "community_skill", "name": "x", "description": "d", "version": "1.0",
                "download_url": "https://example.com/x", "checksum": "not-a-checksum",
            },
        ):
            blob = json.dumps(
                {"format_id": INDEX_FORMAT_ID, "version": "1.0", "entries": [broken]},
                ensure_ascii=False,
            )
            with pytest.raises(ShareIndexError, match="社区 Skill 条目"):
                _index(gateway, fetch=lambda b=blob: b).browse()

    def test_pack_update_carries_changelog_and_needs_no_download_address(self, gateway):
        pack_update = _index(gateway).browse()[0]
        assert pack_update.download_url is None
        assert pack_update.changelog and pack_update.impact   # 01 §9 更新展示面

    def test_browsing_fetches_once_and_never_touches_the_download_url(self, gateway):
        """GWT-3：外部下载地址**只作展示与指引**——本层只拉索引本身，不发生文件获取。"""
        calls: list[int] = []

        def fetch() -> str:
            calls.append(1)
            return DOCUMENT

        _index(gateway, fetch=fetch).browse()

        assert len(calls) == 1

    def test_foreign_or_broken_document_is_refused(self, gateway):
        with pytest.raises(ShareIndexError, match="合法 JSON"):
            _index(gateway, fetch=lambda: "<html>").browse()
        with pytest.raises(ShareIndexError, match="格式标识"):
            _index(
                gateway,
                fetch=lambda: json.dumps(
                    {"format_id": "st-agent-share", "entries": []}, ensure_ascii=False
                ),
            ).browse()


class TestGwt4EmptyState:
    """GWT-4：空状态判据 = 库里一条第三方导入物都没有。"""

    def test_fresh_library_has_no_third_party(self, skills):
        assert has_third_party(skills) is False
        assert "只有官方 Pack" in EMPTY_STATE_TEXT

    def test_imported_skill_flips_the_flag(self, skills):
        skills.register(
            "sk_imported_demo", version="1.0",
            name="导入演示", description="第三方导入物",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {}},
            source="imported",
            provenance=Provenance(sharer=SHARER, imported_at="2026-10-05T18:00:00+08:00",
                                  checksum=CHECKSUM),
            permissions=(),
        )
        assert has_third_party(skills) is True


class TestGwt5Boundary:
    """GWT-5：生态边界与 [09 §6](../../docs/技术架构-v2/09-生态与分享.md) 逐条对应。"""

    def test_boundary_lists_the_three_nots(self):
        joined = " ".join(ECOSYSTEM_BOUNDARY)
        assert "中央 Skill 商店" in joined
        assert "付费" in joined
        assert "自动更新订阅" in joined
        assert all("我" not in line and "你" not in line for line in ECOSYSTEM_BOUNDARY)
