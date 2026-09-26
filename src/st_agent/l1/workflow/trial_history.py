"""试跑留存与对比（T-L1-003.3；03 §4「试跑留存历史可对比」）。

布局（只经 ``Store`` 读写）：试跑记录 → ``execution_log`` 分区
``workflow-trial/<trial_id>.json``——与 §1.2 的 ``skill-run/`` **同分区不同前缀**，
两者互不串扫（试跑不被误当生产执行，[D-020]）。

对比口径（03 §4 落地口径）：按 ``node_id`` 对齐；「不同」的判据是**输出信封的
``status`` 与载荷 ``data``**——``as_of`` / ``last_updated_at`` 等运行元信息不参与，
否则每次试跑都会显示全量差异。比对只读留存，不重跑。
"""

from __future__ import annotations

from pydantic import ValidationError

from st_agent.contracts.identifiers import TrialId
from st_agent.contracts.trace import digest_of
from st_agent.l1.workflow.errors import TrialNotFoundError, WorkflowValidationError
from st_agent.l1.workflow.trial import TrialDiff, TrialRecord, TrialStep

__all__ = [
    "PARTITION",
    "TRIAL_PREFIX",
    "TrialHistory",
]

PARTITION = "execution_log"
"""试跑记录所在分区（与 ``skill-run/`` 同分区、不同前缀）。"""

TRIAL_PREFIX = "workflow-trial/"
"""``execution_log`` 分区内试跑记录的目录前缀。"""


def _signature(step: TrialStep) -> tuple[str, str]:
    """一步的输出去噪签名：``(状态码, 载荷摘要)``（时间等元信息不参与）。"""
    return (step.envelope.status, digest_of(step.envelope.data))


class TrialHistory:
    """试跑记录库门面（03 §4；持久化只经 ``Store`` 的 ``execution_log`` 分区）。"""

    def __init__(self, store) -> None:
        self._store = store

    def save(self, record: TrialRecord) -> TrialRecord:
        """落一条试跑记录（同 ``trial_id`` 覆盖——试跑期间按步增量刷新）。"""
        self._store.put(
            PARTITION, self._path(record.trial_id),
            record.model_dump_json().encode("utf-8"))
        return record

    def get(self, trial_id: str) -> TrialRecord:
        """读取一条试跑记录（不存在 / 损坏 → ``TrialNotFoundError``）。"""
        try:
            raw = self._store.get(PARTITION, self._path(trial_id))
        except KeyError as exc:
            raise TrialNotFoundError(f"试跑记录 {trial_id!r} 不存在") from exc
        try:
            return TrialRecord.model_validate_json(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise TrialNotFoundError(
                f"试跑记录 {trial_id!r} 损坏无法解析：{exc}") from exc

    def list_all(self) -> tuple[TrialRecord, ...]:
        """全部试跑记录（按「起跑时刻 + trial_id」升序）。"""
        out = [self.get(tid) for tid in self._trial_ids()]
        return tuple(sorted(out, key=lambda r: (r.started_at, r.trial_id)))

    def list_for_flow(self, flow_id: str) -> tuple[TrialRecord, ...]:
        """某条工作流的全部试跑记录（同一版本 —— 以 ``flow_id`` 为界，升序）。"""
        return tuple(r for r in self.list_all() if r.flow_id == flow_id)

    def compare(self, trial_id_a: str, trial_id_b: str) -> TrialDiff:
        """两次试跑的逐节点输出差异（新增 / 消失 / 不同；按 ``node_id`` 对齐）。"""
        a, b = self.get(trial_id_a), self.get(trial_id_b)
        sa = {s.node_id: s for s in a.steps}
        sb = {s.node_id: s for s in b.steps}
        return TrialDiff(
            trial_id_a=a.trial_id, trial_id_b=b.trial_id,
            flow_id_a=a.flow_id, flow_id_b=b.flow_id,
            added_nodes=tuple(sorted(set(sb) - set(sa))),
            removed_nodes=tuple(sorted(set(sa) - set(sb))),
            changed_nodes=tuple(sorted(
                nid for nid in set(sa) & set(sb)
                if _signature(sa[nid]) != _signature(sb[nid]))),
        )

    # ───────────────────────── 内部工具 ─────────────────────────

    def _trial_ids(self) -> tuple[str, ...]:
        return tuple(sorted(
            name[len(TRIAL_PREFIX):-len(".json")]
            for name in self._store.list_files(PARTITION)
            if name.startswith(TRIAL_PREFIX) and name.endswith(".json")))

    @staticmethod
    def _path(trial_id: str) -> str:
        try:
            TrialId.of(trial_id)
        except ValidationError as exc:
            raise WorkflowValidationError(
                f"非法 trial_id {trial_id!r}（01 §1）") from exc
        return f"{TRIAL_PREFIX}{trial_id}.json"
