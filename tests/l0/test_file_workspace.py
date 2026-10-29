"""用户文件面（T-AGT-014.2 / T-AGT-014.3；02 §2.1 + 09 §6）。

- GWT-W1：工作区分区读写往返，经 ``Store`` 落盘
- GWT-W2：收件目录只读；绝对路径 / 上跳 / 跨区写入一律拒
- GWT-W3：glob 与文本判定（二进制如实标注）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from st_agent.l0.file_workspace import (
    INBOX_DIR_NAME,
    BinaryFileError,
    FileAccessError,
    FileWorkspace,
)
from st_agent.l0.storage import Store

PASS = "correct horse battery staple"


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store.create(tmp_path / "root", PASS)


@pytest.fixture()
def files(store: Store) -> FileWorkspace:
    return FileWorkspace(store)


class TestWorkspaceRoundTrip:
    def test_write_then_read_goes_through_the_store(self, store, files):
        written = files.write("workspace/reports/a.md", "# 结论\n".encode("utf-8"))
        assert written == len("# 结论\n".encode("utf-8"))
        assert store.get("workspace", "reports/a.md") == "# 结论\n".encode("utf-8")
        assert files.read("workspace/reports/a.md") == "# 结论\n".encode("utf-8")
        assert "reports/a.md" in store.list_files("workspace")

    def test_backslashes_are_normalized(self, files):
        files.write("workspace\\notes\\x.txt", b"x")
        assert files.read("workspace/notes/x.txt") == b"x"

    def test_glob_lists_workspace_files(self, files):
        files.write("workspace/a.md", b"a")
        files.write("workspace/sub/b.md", b"b")
        files.write("workspace/c.txt", b"c")
        assert files.list_files("*.md") == ["workspace/a.md", "workspace/sub/b.md"]
        assert files.list_files("workspace/sub/**") == ["workspace/sub/b.md"]


class TestInbox:
    def test_inbox_is_readable_but_not_writable(self, store, files):
        inbox = store.root / INBOX_DIR_NAME
        inbox.mkdir(parents=True, exist_ok=True)
        (inbox / "in.csv").write_text("code\n600000\n", encoding="utf-8")
        assert files.read("inbox/in.csv").decode("utf-8").startswith("code")
        assert files.list_files("*.csv") == ["inbox/in.csv"]
        with pytest.raises(FileAccessError, match="只读"):
            files.write("inbox/in.csv", b"nope")

    def test_missing_inbox_file_is_explicit(self, files):
        with pytest.raises(FileAccessError, match="无此文件"):
            files.read("inbox/nope.txt")

    def test_inbox_defaults_to_storage_root(self, store, files):
        assert files.inbox_dir == store.root / INBOX_DIR_NAME


class TestPathBoundary:
    @pytest.mark.parametrize(
        "path",
        [
            "/etc/passwd",
            "C:/Windows/win.ini",
            "workspace/../secrets/key.json",
            "../workspace/a.txt",
            "data_cache/a.txt",
            "workspace/",
            "",
        ],
    )
    def test_illegal_paths_are_rejected(self, files, path):
        with pytest.raises(FileAccessError):
            files.split(path)

    def test_write_outside_workspace_is_rejected(self, files):
        with pytest.raises(FileAccessError):
            files.write("data_cache/x", b"x")
        with pytest.raises(FileAccessError):
            files.write("../x", b"x")


class TestText:
    def test_binary_is_flagged_not_faked(self, files):
        files.write("workspace/blob.bin", b"\xff\xfe\x00\x01")
        with pytest.raises(BinaryFileError):
            files.read_text("workspace/blob.bin")

    def test_utf8_text_round_trips(self, files):
        files.write("workspace/中文.txt", "你好".encode("utf-8"))
        text, raw = files.read_text("workspace/中文.txt")
        assert text == "你好" and len(raw) == len("你好".encode("utf-8"))
