"""``Store`` 的 02-L0 §2.4 契约测试：并发访问口径 + 落盘原子性 + 崩溃残留回收。

T-L0-008（关闭 L0 遗留册 E1）：来源 ``T-L1-002.3`` GWT-4 全链路用例暴露 ——
``Store`` 的 ``put`` / ``delete`` 是「读内存清单缓存 → 重建 → 写回」三步且全程
无锁，同分区并发写者互相覆盖、条目静默丢失（该任务为此把用例退让成「并发窗口内
单一写者」）。

T-L0-009（关闭 L0 遗留册 E2）：原子替换的中间产物从「分区目录内」移到存储根下的
专属临时目录，并在 ``Store.open`` 时整体回收。

GWT 对照（T-L0-008 任务文件 5 条 + T-L0-009 任务文件 5 条）：
- GWT-1 同分区并发写不丢条目（核心回归；修复前实现必丢）
- GWT-2 并发删改混合不丢不残留：清单口径与磁盘事实一致
- GWT-3 同键并发读写零假损坏：只读到完整版本，从不抛 ``StorageCorruptionError``
- GWT-4 落盘原子：中断只留完整旧值 / 无害孤儿，不留半截有效状态
- GWT-5 删除窗口不制造损坏：先清单后删文件，孤儿文件不触发损坏报告
- 附① 原子替换的 Windows 瞬时句柄冲突：有界重试（T-L0-008 验证期实测补，见 D-016）
- 附② T-L0-009 的 GWT-1..5：中间产物承载位置与 ``open`` 时回收（见本文件末节）
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable, Iterable
from pathlib import Path

import pytest

from st_agent.l0.backup import WIPE_CONFIRM_TOKENS, wipe_all
from st_agent.l0.storage import StorageCorruptionError, Store
from st_agent.l0.storage.manifest import MANIFEST_NAME
from st_agent.l0.storage.store import TMP_DIR_NAME

PASS = "concurrency-passphrase"
PART = "execution_log"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


def root_of(store: Store) -> Path:
    return store._root  # noqa: SLF001 — 用例需直接查盘上事实（与 test_market 同口径）


def _run_concurrently(fns: Iterable[Callable[[], None]]) -> list[BaseException]:
    """并发跑一组无参调用；返回全部逃逸异常（原样带回，不吞）。"""
    errors: list[BaseException] = []

    def wrap(fn: Callable[[], None]) -> None:
        try:
            fn()
        except Exception as exc:  # 用例要看到真实异常，而非被吞成超时
            errors.append(exc)

    threads = [threading.Thread(target=wrap, args=(fn,)) for fn in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors


# ───────────────────────── GWT-1 同分区并发写不丢条目 ─────────────────────────

def test_gwt1_concurrent_puts_in_one_partition_keep_every_entry(store: Store) -> None:
    """修复前：后写者的快照不含前写者的条目 → 条目静默丢失。"""
    count = 16
    barrier = threading.Barrier(count)
    expected = {f"net/entry-{i}.bin" for i in range(count)}

    def writer(i: int) -> Callable[[], None]:
        def run() -> None:
            barrier.wait()  # 尽量同时进入写路径，放大竞争窗口
            store.put(PART, f"net/entry-{i}.bin", f"payload-{i}".encode())
        return run

    assert _run_concurrently([writer(i) for i in range(count)]) == []

    assert set(store.list_files(PART)) == expected
    for i in range(count):
        assert store.get(PART, f"net/entry-{i}.bin") == f"payload-{i}".encode()

    # 落盘清单与内存口径一致（重开不丢、不报损坏）
    reopened = Store.open(root_of(store), PASS)
    assert set(reopened.list_files(PART)) == expected
    assert reopened.partition_state(PART).state == "ok"


# ───────────────────────── GWT-2 并发删改混合不丢不残留 ─────────────────────────

def test_gwt2_concurrent_delete_and_put_keep_manifest_consistent(store: Store) -> None:
    keep = [f"keep-{i}.bin" for i in range(4)]
    doomed = [f"doomed-{i}.bin" for i in range(4)]
    fresh = [f"fresh-{i}.bin" for i in range(4)]
    for name in keep + doomed:
        store.put(PART, name, b"seed")

    barrier = threading.Barrier(len(doomed) + len(fresh))

    def deleter(name: str) -> Callable[[], None]:
        def run() -> None:
            barrier.wait()
            store.delete(PART, name)
        return run

    def writer(name: str) -> Callable[[], None]:
        def run() -> None:
            barrier.wait()
            store.put(PART, name, b"new")
        return run

    fns = [deleter(n) for n in doomed] + [writer(n) for n in fresh]
    assert _run_concurrently(fns) == []

    wanted = set(keep) | set(fresh)
    assert set(store.list_files(PART)) == wanted
    # 无「在册取不到」，也无「取得到不在册」
    for name in sorted(wanted):
        assert store.get(PART, name)
    for name in doomed:
        on_disk = (root_of(store) / PART / name).exists()
        assert not on_disk

    reopened = Store.open(root_of(store), PASS)
    assert set(reopened.list_files(PART)) == wanted
    assert reopened.partition_state(PART).state == "ok"


# ───────────────────────── GWT-3 同键并发读写零假损坏 ─────────────────────────

def test_gwt3_same_key_concurrent_read_write_never_reports_false_corruption(
    store: Store,
) -> None:
    """写者的「换文件」与「改清单」之间曾被读方观察到不一致配对 → 假损坏。

    修复后读方要么读到某个**完整**版本、要么在该键尚不存在时 ``KeyError``，
    绝不抛 ``StorageCorruptionError``（本用例把该不变量钉住）。
    """
    name = "net/hot.json"
    store.put(PART, name, b"v0")

    rounds = 400
    stop = threading.Event()
    seen_broken: list[bytes] = []
    corrupted: list[BaseException] = []

    def writer() -> None:
        try:
            for i in range(rounds):
                store.put(PART, name, f"v{i + 1}".encode())
        finally:
            stop.set()

    def reader() -> None:
        while not stop.is_set():
            try:
                value = store.get(PART, name)
            except StorageCorruptionError as exc:  # 假损坏 —— 本用例要消灭的
                corrupted.append(exc)
                return
            except KeyError:  # 覆写窗口内该键尚未登记（合法）
                continue
            if not value.removeprefix(b"v").isdigit():
                seen_broken.append(value)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    writer()
    thread.join(10)

    assert corrupted == [], f"出现假损坏：{corrupted}"
    assert seen_broken == [], f"读到非完整版本：{seen_broken}"
    assert store.get(PART, name) == f"v{rounds}".encode()


# ───────────────────────── GWT-4 落盘原子 ─────────────────────────

def test_gwt4_interrupted_write_keeps_complete_old_version(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.replace`` 之前中断：目标保持完整旧值，分区不得因半截文件被判损坏。"""
    name = "config/settings.json"
    store.put(PART, name, b"old-complete")
    real_replace = os.replace

    def always_failing(src, dst):  # 注入：任何原子替换都失败
        raise OSError("injected: 在原子替换之前中断")

    monkeypatch.setattr(os, "replace", always_failing)
    with pytest.raises(OSError):
        store.put(PART, name, b"new-complete")
    monkeypatch.setattr(os, "replace", real_replace)

    assert store.get(PART, name) == b"old-complete"       # 旧内容完整
    assert store.list_files(PART) == (name,)
    # 中断留下的临时文件已被清理，临时目录为空
    assert list((root_of(store) / TMP_DIR_NAME).iterdir()) == []

    reopened = Store.open(root_of(store), PASS)
    assert reopened.partition_state(PART).state == "ok"
    assert reopened.get(PART, name) == b"old-complete"


def test_gwt4_interrupted_manifest_write_leaves_only_harmless_orphan(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """数据文件已落盘、清单替换中断：孤儿不在清单口径内 → 分区仍 ``ok``。"""
    existing = "config/known.json"
    store.put(PART, existing, b"known")
    real_replace = os.replace

    def manifest_only_failing(src, dst):  # 注入：只让清单的替换失败
        if Path(dst).name == MANIFEST_NAME:
            raise OSError("injected: 清单替换中断")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", manifest_only_failing)
    with pytest.raises(OSError):
        store.put(PART, "config/orphan.json", b"new")
    monkeypatch.setattr(os, "replace", real_replace)

    assert store.list_files(PART) == (existing,)          # 孤儿未登记
    assert store.get(PART, existing) == b"known"          # 既有文件不受影响

    reopened = Store.open(root_of(store), PASS)
    assert reopened.partition_state(PART).state == "ok"   # 孤儿不触发损坏报告
    assert reopened.list_files(PART) == (existing,)


# ───────────────────────── GWT-5 删除窗口不制造损坏 ─────────────────────────

def test_gwt5_delete_interrupted_after_manifest_update_stays_clean(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """顺序为「先写清单、后删文件」：中断只留不在册的孤儿文件，重开无损坏。"""
    victim = "net/victim.bin"
    store.put(PART, victim, b"x")
    real_unlink = Path.unlink

    def failing_unlink(self: Path, *args, **kwargs):  # 注入：只让该文件的删除失败
        if self.name == "victim.bin":
            raise OSError("injected: 清单已更新、数据文件未删时中断")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", failing_unlink)
    with pytest.raises(OSError):
        store.delete(PART, victim)
    monkeypatch.setattr(Path, "unlink", real_unlink)

    assert store.list_files(PART) == ()                   # 清单已不含它
    assert (root_of(store) / PART / victim).exists()      # 孤儿文件仍在盘上

    reopened = Store.open(root_of(store), PASS)           # 孤儿不产生损坏报告
    assert reopened.partition_state(PART).state == "ok"
    assert reopened.list_files(PART) == ()


# ─────────── 原子替换的 Windows 瞬时句柄冲突（有界重试，T-L0-008 验证期补） ───────────

def test_atomic_replace_retries_transient_permission_error(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``os.replace`` 的瞬时 ``EACCES``（外部句柄短暂持有）被有界重试吸收。

    实测来源：全量跑 `tests/l1/test_runner_sandbox.py` 时 `Store.create` 的清单
    替换偶发 `PermissionError: [WinError 5]`（约 2000+ 次写盘中 1 次，不可稳定复现）。
    """
    real_replace = os.replace
    failures = {"left": 2}

    def flaky(src, dst):  # 注入：只让清单替换抖动前两次
        if Path(dst).name == MANIFEST_NAME and failures["left"] > 0:
            failures["left"] -= 1
            raise PermissionError(13, "injected: 外部句柄短暂持有")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    store.put(PART, "config/flaky.json", b"value")
    monkeypatch.setattr(os, "replace", real_replace)

    assert failures["left"] == 0                      # 确实抖动过，且被重试吸收
    assert store.get(PART, "config/flaky.json") == b"value"
    assert store.list_files(PART) == ("config/flaky.json",)


def test_atomic_replace_retry_is_bounded_and_leaves_no_tmp(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """持续冲突**不得**被吞：重试次数有界、耗尽即原样抛，并清掉残留临时文件。"""
    real_replace = os.replace
    attempts = {"n": 0}

    def always_denied(src, dst):  # 注入：任何替换都冲突
        attempts["n"] += 1
        raise PermissionError(13, "injected: 持续冲突")

    monkeypatch.setattr(os, "replace", always_denied)
    with pytest.raises(PermissionError):
        store.put(PART, "config/denied.json", b"value")
    monkeypatch.setattr(os, "replace", real_replace)

    assert attempts["n"] == 3                         # 有界（3 次即止）
    assert list((root_of(store) / TMP_DIR_NAME).iterdir()) == []   # 残留 tmp 已清
    assert store.list_files(PART) == ()               # 未登记
    assert store.partition_state(PART).state == "ok"


# ──────── 中间产物的承载位置与回收（T-L0-009 / 关闭 L0 册 E2） ────────

def test_tmp_product_never_lands_in_partition_dir(store: Store) -> None:
    """GWT-2：中间产物只出现在专属临时目录，分区目录保持「只有清单 + 数据文件」。"""
    store.put(PART, "a.bin", b"a")
    store.put(PART, "net/b.bin", b"b")
    store.delete(PART, "a.bin")

    partition_dir = root_of(store) / PART
    assert sorted(p.name for p in partition_dir.iterdir()) == [MANIFEST_NAME, "net"]
    assert sorted(p.name for p in (partition_dir / "net").iterdir()) == ["b.bin"]
    assert list((root_of(store) / TMP_DIR_NAME).iterdir()) == []   # 写完后临时目录为空


def test_stale_tmp_residue_is_reclaimed_on_open(store: Store) -> None:
    """GWT-1 + GWT-3：模拟「进程被强杀」留下的残留 → 下次 open 回收，数据面无损。"""
    store.put(PART, "net/keep.bin", b"payload")
    before = store.export_partition(PART)

    tmp_dir = root_of(store) / TMP_DIR_NAME
    stale = tmp_dir / "manifest.bin.tmp-0badc0ffee42"      # 半截密文
    stale.write_bytes(os.urandom(64))
    assert stale.exists()

    reopened = Store.open(root_of(store), PASS)

    assert list((root_of(store) / TMP_DIR_NAME).iterdir()) == []   # 残留整体回收
    assert reopened.export_partition(PART) == before               # 数据逐字节不变
    assert reopened.list_files(PART) == ("net/keep.bin",)
    assert reopened.partition_state(PART).state == "ok"


def test_open_recreates_missing_tmp_dir(store: Store) -> None:
    """GWT-4：早于该机制的既有存储（无临时目录）→ open 自动补建，不报错且可写。"""
    store.put(PART, "net/keep.bin", b"payload")
    shutil.rmtree(root_of(store) / TMP_DIR_NAME)
    assert not (root_of(store) / TMP_DIR_NAME).exists()

    reopened = Store.open(root_of(store), PASS)

    assert (root_of(store) / TMP_DIR_NAME).is_dir()
    assert reopened.get(PART, "net/keep.bin") == b"payload"
    reopened.put(PART, "net/after.bin", b"after")                  # 补建后可正常写
    assert reopened.get(PART, "net/after.bin") == b"after"


def test_wipe_all_removes_tmp_dir_too(store: Store) -> None:
    """GWT-5：完全清空走 ``rmtree(root)``，临时目录随存储根一并消失。"""
    store.put(PART, "net/keep.bin", b"payload")
    (root_of(store) / TMP_DIR_NAME / "residue.tmp-deadbeef").write_bytes(b"x")

    wipe_all(root_of(store), list(WIPE_CONFIRM_TOKENS))

    assert not root_of(store).exists()
