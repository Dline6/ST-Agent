"""LLM 端点抽象错误类型（02 §4；失败显式化，01 §5）。

- 配置面（fail-fast 异常）：``LlmNotFoundError`` / ``LlmExistsError`` /
  ``LlmValidationError``
- 执行面（运行时失败一律走 ``ResultEnvelope``，不抛异常）：
  端点宕机 → ``unavailable``；超时/取消/其他执行失败 → ``failed``；
  上游凭据缺失 → ``dependency_failed``；输入/档位非法 → ``validation_failed``
"""

__all__ = [
    "LlmError",
    "LlmExistsError",
    "LlmNotFoundError",
    "LlmValidationError",
]


class LlmError(Exception):
    """LLM 端点子系统错误基类。"""


class LlmNotFoundError(LlmError, KeyError):
    """端点不存在（调用方传了未注册的 endpoint_id，属调用方缺陷）。"""


class LlmExistsError(LlmError):
    """同标识端点已存在——更新走 ``update_priority`` / ``replace``，不得静默覆盖。"""


class LlmValidationError(LlmError, ValueError):
    """端点标识 / 档位 / 优先级 / 超时等配置项非法。"""
