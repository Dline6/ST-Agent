"""文件原语（T-AGT-014.2 / T-AGT-014.3；01 §10 + 02 §2.1 + [D-146]）。

- 014.2 GWT-1：声明范围内的路径可读，返回内容
- 014.2 GWT-2：越界 / 未批准 → 拦截且不读取
- 014.2 GWT-3：超大文件截断并给续读指针
- 014.3 GWT-1：工作区内写入落盘、产品可再取回
- 014.3 GWT-2：未声明或未批准 local_write → 拦截
- 014.3 GWT-3：并发写同一文件串行化（Store 分区锁）
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from st_agent.l0.file_workspace import FileWorkspace
from st_agent.l0.storage import Store
from st_agent.l1.builtin import install_builtins
from st_agent.l1.builtin.files import (
    FILE_GLOB,
    FILE_GREP,
    FILE_READ,
    FILE_WRITE,
    READ_PERMISSIONS,
    WRITE_PERMISSIONS,
)
from st_agent.l1.runner import SkillRunner
from st_agent.l1.sandbox import SkillSandbox
from st_agent.l1.skills import SkillRegistry
from st_agent.l1.skills.permissions import SkillPermissionBook

PASS = "correct horse battery staple"
FILE_READ_ID = "sk_file_read_v1.0"
FILE_GLOB_ID = "sk_file_glob_v1.0"
FILE_GREP_ID = "sk_file_grep_v1.0"
FILE_WRITE_ID = "sk_file_write_v1.0"


class Env:
    def __init__(self, store: Store, files: FileWorkspace, runner: SkillRunner):
        self.store = store
        self.files = files
        self.runner = runner

    def read(self, path: str, *, approved=READ_PERMISSIONS, **inputs):
        return self.runner.run(
            FILE_READ_ID, {}, inputs={"path": path, **inputs},
            approved_permissions=approved,
        ).envelope

    def glob(self, pattern: str, *, approved=READ_PERMISSIONS, **inputs):
        return self.runner.run(
            FILE_GLOB_ID, {}, inputs={"pattern": pattern, **inputs},
            approved_permissions=approved,
        ).envelope

    def grep(self, pattern: str, *, approved=READ_PERMISSIONS, **inputs):
        return self.runner.run(
            FILE_GREP_ID, {}, inputs={"pattern": pattern, **inputs},
            approved_permissions=approved,
        ).envelope

    def write(self, path: str, content: str, *, approved=WRITE_PERMISSIONS):
        return self.runner.run(
            FILE_WRITE_ID, {}, inputs={"path": path, "content": content},
            approved_permissions=approved,
        ).envelope


def _env(tmp_path: Path) -> Env:
    store = Store.create(tmp_path / "root", PASS)
    registry = SkillRegistry(store)
    sandbox = SkillSandbox(store)
    runner = SkillRunner(store, registry, sandbox=sandbox)
    files = FileWorkspace(store)
    install_builtins(
        registry, runner, context={"files": files},
        primitives=(FILE_READ, FILE_GLOB, FILE_GREP, FILE_WRITE),
    )
    return Env(store, files, runner)


@pytest.fixture()
def env(tmp_path: Path) -> Env:
    return _env(tmp_path)


# ───────────────────────── 014.2 读 ─────────────────────────


class TestFileRead:
    def test_in_scope_read_returns_content(self, env):
        env.files.write("workspace/a.txt", "第一行\n第二行\n".encode("utf-8"))
        out = env.read("workspace/a.txt")
        assert out.status == "ok"
        assert out.data["text"] == "第一行\n第二行\n"
        assert out.data["truncated"] is False

    def test_inbox_read_is_allowed(self, env):
        inbox = env.store.root / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "notes.md").write_text("hello", encoding="utf-8")
        out = env.read("inbox/notes.md")
        assert out.status == "ok" and out.data["text"] == "hello"

    def test_out_of_scope_path_is_rejected_before_reading(self, env):
        out = env.read("data_cache/market.db")
        assert out.status == "validation_failed"
        assert "路径须为" in (out.reason or "")

    def test_traversal_is_rejected(self, env):
        out = env.read("workspace/../../secrets/key.json")
        assert out.status == "validation_failed"

    def test_unapproved_read_is_blocked(self, env):
        env.files.write("workspace/a.txt", b"secret")
        out = env.read("workspace/a.txt", approved=())
        assert out.status == "validation_failed"
        assert "缺少已批准权限" in (out.reason or "")

    def test_large_file_is_truncated_with_a_pointer(self, env):
        env.files.write("workspace/big.txt", b"0123456789" * 10)
        first = env.read("workspace/big.txt", max_chars=25)
        assert first.status == "ok"
        assert first.data["truncated"] is True
        assert first.data["next_offset"] == 25
        assert first.data["text"] == "0123456789012345678901234"
        second = env.read(
            "workspace/big.txt", offset=first.data["next_offset"], max_chars=25,
        )
        assert second.data["text"] == "5678901234567890123456789"
        assert second.data["truncated"] is True

    def test_binary_is_unavailable_not_fake_text(self, env):
        env.files.write("workspace/blob.bin", b"\xff\xfe\x00\x01")
        out = env.read("workspace/blob.bin")
        assert out.status == "unavailable"
        assert "不是 UTF-8" in (out.reason or "")

    def test_missing_file_is_empty(self, env):
        out = env.read("workspace/none.txt")
        assert out.status == "empty"
        assert "无此文件" in (out.reason or "")


class TestFileGlob:
    def test_lists_matching_paths(self, env):
        env.files.write("workspace/a.md", b"a")
        env.files.write("workspace/sub/b.md", b"b")
        env.files.write("workspace/c.txt", b"c")
        out = env.glob("*.md")
        assert out.status == "ok"
        assert out.data["paths"] == ["workspace/a.md", "workspace/sub/b.md"]

    def test_limit_truncates_but_reports(self, env):
        for index in range(5):
            env.files.write(f"workspace/f{index}.txt", b"x")
        out = env.glob("*.txt", limit=2)
        assert out.data["count"] == 2 and out.data["truncated"] is True

    def test_unapproved_listing_is_blocked(self, env):
        out = env.glob("*", approved=())
        assert out.status == "validation_failed"


class TestFileGrep:
    def test_finds_lines_and_reports_line_numbers(self, env):
        env.files.write("workspace/a.txt", "alpha\nbeta\nALPHA\n".encode("utf-8"))
        out = env.grep("alpha")
        assert out.status == "ok"
        assert [m["line"] for m in out.data["matches"]] == [1]
        casefold = env.grep("alpha", ignore_case=True)
        assert [m["line"] for m in casefold.data["matches"]] == [1, 3]

    def test_binary_and_large_are_skipped_with_counts(self, env):
        env.files.write("workspace/blob.bin", b"\xff\xfe\x00\x01")
        env.files.write("workspace/a.txt", b"needle\n")
        out = env.grep("needle")
        assert out.data["count"] == 1
        assert out.data["skipped_binary"] == 1

    def test_empty_needle_is_rejected(self, env):
        out = env.grep("")
        assert out.status == "validation_failed"


# ───────────────────────── 014.3 写 ─────────────────────────


class TestFileWrite:
    def test_write_lands_in_the_workspace_partition(self, env):
        out = env.write("workspace/out/report.md", "# 报告")
        assert out.status == "ok" and out.data["area"] == "workspace"
        assert env.store.get("workspace", "out/report.md") == "# 报告".encode("utf-8")
        assert env.read("workspace/out/report.md").data["text"] == "# 报告"

    def test_unapproved_write_is_blocked_and_nothing_is_written(self, env):
        out = env.write("workspace/a.txt", "x", approved=())
        assert out.status == "validation_failed"
        assert "缺少已批准权限" in (out.reason or "")
        assert env.store.list_files("workspace") == ()

    def test_inbox_is_not_writable(self, env):
        out = env.write("inbox/a.txt", "x")
        assert out.status == "validation_failed"
        assert "只写 workspace" in (out.reason or "")

    def test_absolute_path_is_rejected(self, env):
        out = env.write("C:/Windows/a.txt", "x")
        assert out.status == "validation_failed"

    def test_concurrent_writes_are_serialized(self, env):
        def attempt(index: int) -> str:
            out = env.write("workspace/same.txt", f"content-{index}")
            return out.status

        with ThreadPoolExecutor(max_workers=4) as pool:
            statuses = list(pool.map(attempt, range(8)))
        assert statuses == ["ok"] * 8
        final = env.store.get("workspace", "same.txt").decode("utf-8")
        assert final.startswith("content-")          # 串行化后终态是某一完整版本


class TestWiring:
    def test_without_sandbox_the_primitive_is_unavailable(self, tmp_path):
        store = Store.create(tmp_path / "nosandbox", PASS)
        registry = SkillRegistry(store)
        runner = SkillRunner(store, registry)      # 不接沙箱
        files = FileWorkspace(store)
        install_builtins(registry, runner, context={"files": files}, primitives=(FILE_READ,))
        out = runner.run(
            FILE_READ_ID, {}, inputs={"path": "workspace/a.txt"},
            approved_permissions=READ_PERMISSIONS,
        ).envelope
        assert out.status == "unavailable"
        assert "沙箱" in (out.reason or "")

    def test_install_seeds_first_party_approvals_and_keeps_rejections(self, tmp_path):
        store = Store.create(tmp_path / "approvals", PASS)
        registry = SkillRegistry(store)
        runner = SkillRunner(store, registry)
        book = SkillPermissionBook(store)
        files = FileWorkspace(store)
        install_builtins(
            registry, runner, context={"files": files},
            primitives=(FILE_READ, FILE_WRITE), permissions=book,
        )
        assert book.approved_permissions("sk_file_read") == READ_PERMISSIONS
        assert book.approved_permissions("sk_file_write") == WRITE_PERMISSIONS

        book.reject("sk_file_write", WRITE_PERMISSIONS[0])
        install_builtins(
            registry, runner, context={"files": files},
            primitives=(FILE_READ, FILE_WRITE), permissions=book,
        )
        assert WRITE_PERMISSIONS[0] not in book.approved_permissions("sk_file_write")
