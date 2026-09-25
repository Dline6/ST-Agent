"""密钥与凭据子系统（02-L0 §3）。

- 凭据本体存 ``secrets`` 分区（加密 + 单独隔离，T-L0-001 保证）
- 使用记录存 ``execution_log`` 分区（凭据与审计分离）
- 对外统一从 ``st_agent.l0.secrets`` import；明文仅 ``CredentialVault.use()``
  一次性返回，其余形态一律掩码。
"""

from st_agent.l0.secrets.errors import (
    CredentialError,
    CredentialExistsError,
    CredentialNotFoundError,
    CredentialValidationError,
)
from st_agent.l0.secrets.masking import VISIBLE_TAIL, mask_secret
from st_agent.l0.secrets.models import (
    CredentialRecord,
    CredentialUsageRecord,
    CredentialView,
)
from st_agent.l0.secrets.vault import CRED_PREFIX, USAGE_PREFIX, CredentialVault

__all__ = [
    "CRED_PREFIX",
    "USAGE_PREFIX",
    "VISIBLE_TAIL",
    "CredentialError",
    "CredentialExistsError",
    "CredentialNotFoundError",
    "CredentialRecord",
    "CredentialUsageRecord",
    "CredentialValidationError",
    "CredentialVault",
    "CredentialView",
    "mask_secret",
]
