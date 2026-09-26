"""权限作用域匹配（03 §1.5 文件/网络范围；01 §10 权限声明语法）。

三项权限的作用域语义（``parse_permission`` 已拆出 action 与 scope）：

- ``local_read:<path-scope>`` —— 路径作用域，支持 ``**``（任意层级）与 ``*``：
  ``data/cache/**`` 匹配该目录及其下全部文件。匹配前先归一化（统一 ``/``、
  折叠 ``.``/``..``），归一化后仍向上逃逸（``..`` 开头）即判越界。
- ``net_access:<host-pattern>`` —— 主机作用域，glob 匹配（大小写不敏感）；
  约定 ``*.example.com`` 同时匹配裸域 ``example.com``。

本模块为纯函数，便于单测；匹配只判「声明范围」，「是否获用户批准」由
``SkillSandbox`` 会话核对（§10 逐项批准）。
"""

from __future__ import annotations

import fnmatch
import posixpath

from st_agent.l1.sandbox.errors import SandboxValidationError

__all__ = [
    "host_in_scope",
    "is_escaping",
    "normalize_path",
    "path_in_scope",
]


def normalize_path(path: str) -> str:
    """归一化路径为分区内相对 POSIX 形态（统一分隔符、折叠 ``.``/``..``）。"""
    if not isinstance(path, str) or not path.strip():
        raise SandboxValidationError("路径不得为空")
    return posixpath.normpath(path.replace("\\", "/").strip()).lstrip("/")


def is_escaping(normalized: str) -> bool:
    """归一化后是否向上逃逸出声明根（``..`` / ``../x``）。"""
    return normalized == ".." or normalized.startswith("../")


def path_in_scope(path: str, scope: str) -> bool:
    """``path`` 是否落在 ``local_read`` 声明的 ``scope`` 内（越界/逃逸即 False）。"""
    norm = normalize_path(path)
    if is_escaping(norm):
        return False
    s = (scope or "").replace("\\", "/").strip().lstrip("/")
    if not s or is_escaping(posixpath.normpath(s)):
        return False
    if s.endswith("/**"):
        prefix = s[:-3].rstrip("/")
        if not prefix:
            return False
        return norm == prefix or norm.startswith(prefix + "/")
    return fnmatch.fnmatchcase(norm, s)


def host_in_scope(host: str, pattern: str) -> bool:
    """``host`` 是否匹配 ``net_access`` 声明的 ``pattern``（大小写不敏感）。

    额外约定：``*.example.com`` 同时匹配裸域 ``example.com``（便于把
    「某子域集合 + 其主域」写成一条声明）。
    """
    h = (host or "").strip().lower().rstrip(".")
    p = (pattern or "").strip().lower().rstrip(".")
    if not h or not p:
        return False
    if fnmatch.fnmatchcase(h, p):
        return True
    if p.startswith("*.") and h == p[2:]:
        return True
    return False
