"""`T-INT-004` GWT-10 · 真实渠道投递的 live 冒烟（默认排除，不进 CI）。

M3 关卡把渠道面接成**注入的传输端口**（[07 §3](../../docs/技术架构-v2/07-L5-主动触达.md)），
离线用例（``tests/integration/test_m3_delivery.py``）用替身验装配关系；本文件证明**同一条接线**
在真端点上成立——真 SMTP / 真 Webhook，经 L0 出网网关（唯一出口与唯一审计点），
凭据只经 ``CredentialVault.use()`` 一跳动用。

配置取自环境变量（不入库），**缺即 skip 并写明原因**（不许默默略过，
[工作流「测试分层」](../../项目管理/工作流.md) / [D-059](../../项目管理/决策日志.md)）：

| 变量 | 用途 |
|---|---|
| `ST_AGENT_SMTP_HOST` / `_PORT` | SMTP 服务器（端口缺省 587） |
| `ST_AGENT_SMTP_USER` / `_PASS` | SMTP 认证（口令经 vault 取用，不落 argv / 日志） |
| `ST_AGENT_SMTP_FROM` / `_TO` | 发件 / 收件地址 |
| `ST_AGENT_WEBHOOK_URL` | Webhook 完整地址（含 token 的用它自己的鉴权方式） |

跑法：

    python -m pytest -m live tests/live/test_channel_delivery_live.py
"""

from __future__ import annotations

import json
import os
import smtplib
import sys
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

# 复用集成关卡的播种与取信面（tests/integration 不在本目录的 import 路径上）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

from rig_m3 import NOW, seeded_m3  # noqa: E402
from rig import ROOT_NAME  # noqa: E402

from st_agent.contracts.identifiers import digest_id  # noqa: E402
from st_agent.l5.signal import SignalContent, SignalLensStance, signal_event  # noqa: E402

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).resolve().parents[2]
THROWAWAY_PASS = "m3-live-throwaway-passphrase"
_KEY = "channel-live"
_FROM = "st-agent@localhost"


def _config(name: str) -> str:
    """取环境变量；再退到仓库根 ``.env``（不入库）。"""
    value = (os.environ.get(name) or "").strip()
    if value:
        return value
    env_file = REPO_ROOT / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return ""


def _fail(message: str) -> None:
    pytest.skip(f"无可用真实端点：{message}（本机未配置，见工作流「测试分层」）")


def _signal(dedup_key: str = "l1:live:probe"):
    return signal_event(
        level="emergency",
        content_ref=SignalContent(
            conclusion="真实渠道投递冒烟：这条推送用于验证渠道接线可达",
            lens_stances=(SignalLensStance(
                lens_id=digest_id("lens", "l1:live-probe"),
                stance="neutral", summary="观察：live 用例发出的固定样例内容",
            ),),
        ),
        evidence_refs=("run_abcdef0123456789abcd",),
        dedup_key=dedup_key,
        source_trace_id=f"tr_{0x1:020x}",
        occurred_at=NOW,
    )


def test_real_smtp_channel_delivers_through_the_gateway(tmp_path: Path) -> None:
    """真 SMTP：渠道渲染 → 网关 `channel_delivery` → 真连接发送 → 审计留痕。"""
    host = _config("ST_AGENT_SMTP_HOST")
    user, password = _config("ST_AGENT_SMTP_USER"), _config("ST_AGENT_SMTP_PASS")
    recipient = _config("ST_AGENT_SMTP_TO")
    if not (host and recipient):
        _fail("缺 ST_AGENT_SMTP_HOST / ST_AGENT_SMTP_TO")
    if not (user and password):
        _fail("缺 ST_AGENT_SMTP_USER / ST_AGENT_SMTP_PASS（认证信息）")
    port = int(_config("ST_AGENT_SMTP_PORT") or "587")

    sent: list[bytes] = []

    def transport(target_host: str, message: bytes) -> int:
        """真发一封：认证口令取自渠道渲染进消息里的那一段（＝ vault 取用过的值）。"""
        credential = ""
        for line in message.decode("utf-8").splitlines():
            if line.startswith("X-ST-Auth: "):
                credential = line.split(": ", 1)[1]
        headers = {
            line.split(":", 1)[0].lower(): line.split(":", 1)[1].strip()
            for line in message.decode("utf-8").splitlines() if ":" in line
        }
        with smtplib.SMTP(target_host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, credential or password)
            smtp.sendmail(headers.get("from", _FROM), [recipient], message)
        sent.append(message)
        return 0

    rig = seeded_m3(
        tmp_path / ROOT_NAME,
        channels={"desktop": _Offline(), "tts": _Offline()},
        cloud_transport=transport, email_host=host,
        credentials={"email": _KEY}, audit=True,        # 首级桌面不可达 → 走真云端
    )
    rig.m3.m1.runtime.vault.add(_KEY, "smtp", password)

    rig.m3.events.publish(_signal())
    record = [r for r in rig.m3.l5.delivery.ledger() if r.dedup_key == "l1:live:probe"][-1]
    assert sent, "真 SMTP 未发出任何东西"
    assert record.channel == "email" and record.status == "delivered", record.detail
    # 审计留痕可查，且**明文口令不在其中**（凭据只在那一跳动用）
    assert rig.m3.m1.runtime.gateway.query(kind="channel_delivery")
    assert _config("ST_AGENT_SMTP_PASS") not in _audit_text(rig)


def test_real_webhook_channel_posts_through_the_gateway(tmp_path: Path) -> None:
    """真 Webhook：渲染 JSON → 网关 → HTTP POST → 审计留痕。"""
    url = _config("ST_AGENT_WEBHOOK_URL")
    if not url:
        _fail("缺 ST_AGENT_WEBHOOK_URL")
    host = urllib.parse.urlsplit(url).hostname or ""
    posted: list[bytes] = []

    def transport(target_host: str, message: bytes) -> int:
        assert target_host == host, "审计目标主机应与 Webhook 地址一致"
        request = urllib.request.Request(
            url, data=message, headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            posted.append(message)
            return len(response.read())

    rig = seeded_m3(
        tmp_path / ROOT_NAME,
        channels={"desktop": _Offline(), "tts": _Offline()},
        cloud_transport=transport, webhook_host=host,
        credentials={"im_webhook": _KEY}, audit=True,
    )
    rig.m3.m1.runtime.vault.add(_KEY, "webhook", "live-token")
    rig.m3.events.publish(_signal("l1:live:probe-webhook"))
    record = [
        r for r in rig.m3.l5.delivery.ledger() if r.dedup_key == "l1:live:probe-webhook"
    ][-1]
    assert posted, "Webhook 未发出任何东西"
    assert record.channel == "im_webhook" and record.status == "delivered", record.detail
    payload = json.loads(posted[0].decode("utf-8"))
    assert payload["signal_id"] and payload["trace_id"]


class _Offline:
    """本地渠道的确定性替身（live 用例只验云端真链路，不弹系统通知）。"""

    channel = "desktop"

    def __init__(self) -> None:
        self.channel = "desktop"
        self.ok = True

    def deliver(self, _payload):
        from st_agent.l5.channels import ChannelResult

        return ChannelResult(channel=self.channel, status="unavailable",
                             detail="live 用例：本地渠道不真弹")

    def health(self):
        from st_agent.l5.channels import ChannelHealth

        return ChannelHealth(channel=self.channel, available=False, offline_level="full",
                             reason="live 用例：本地渠道不真弹")

    def degrade(self, reason: str):
        from st_agent.l5.channels import ChannelResult

        return ChannelResult(channel=self.channel, status="unavailable", detail=reason)


def _audit_text(rig) -> str:
    """网关审计的可序列化文本（用于断言「明文不进审计」）。"""
    records = rig.m3.m1.runtime.gateway.query(kind="channel_delivery")
    return json.dumps(
        [r.model_dump(mode="json") for r in records], ensure_ascii=False, default=str,
    )
