"""凭据掩码规则（02 §3「界面掩码显示」）。

单条规则，全局一致：
- 长度 ≤ 4：全掩码（等长 ``*``，不泄露任何字符）
- 长度 > 4：保留尾部最多 4 位，其余 ``*``（Stripe 式 last4，可辨识但不可用）
"""

__all__ = ["VISIBLE_TAIL", "mask_secret"]

VISIBLE_TAIL = 4
"""掩码保留的最大尾部字符数。"""


def mask_secret(value: str) -> str:
    """对凭据明文做掩码；空串非法（上游校验拦截，此处断言）。"""
    assert value, "mask_secret 不接受空串"
    if len(value) <= VISIBLE_TAIL:
        return "*" * len(value)
    return "*" * (len(value) - VISIBLE_TAIL) + value[-VISIBLE_TAIL:]
