"""设置区写面：设置落值、迁移、备份恢复与凭据。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from st_agent.contracts.result_envelope import ResultEnvelope
from st_agent.l0.bootstrap.dotenv import read_dotenv, write_dotenv
from st_agent.l0.llm.boot_config import load_boot_config, save_boot_config
from st_agent.l0.llm.http_transport import OpenAiRoute
from st_agent.l0.secrets.masking import mask_secret
from st_agent.l0.storage import Store
from st_agent.l1.runtime import LLM_CREDENTIAL_ID, LlmBootstrap
from st_agent.ui.envelope import envelope_payload

from st_agent.ui.surfaces.base import (
    SurfaceBase,
    _now,
    _optional_text,
    _required,
)
from st_agent.ui.surfaces.settings.shared import (
    _backup_partitions,
    _coerce_value,
    _default_backup_path,
    _is_inside,
    _stamp,
    _store_errors,
    _window_start,
)


class SettingsWriteSurface(SurfaceBase):
    """设置区写面：设置落值、迁移、备份恢复与凭据。"""

    def api_settings_data_sync(self, body: dict[str, Any]) -> dict[str, Any]:
        """发起手动同步（显式动作；后台线程执行，立即返回 ``job_id``）。"""
        face = None if self.settings is None else getattr(self.settings, "data_sync", None)
        items = body.get("items")
        custom = _optional_text(body.get("custom_codes_text")) or ""
        if isinstance(items, list):
            return self._call_face(
                "settings-data-sync", face, "L0 数据同步面",
                lambda sync: sync.start_items(items, custom_text=custom),
            )
        raw = body.get("task_keys")
        keys = [str(item) for item in raw] if isinstance(raw, list) else []
        start = _optional_text(body.get("start"))
        end = _optional_text(body.get("end"))
        scope = _optional_text(body.get("scope")) or "market"
        return self._call_face(
            "settings-data-sync", face, "L0 数据同步面",
            lambda sync: sync.start(keys, start=start, end=end, scope=scope),
        )

    def api_settings_data_clear(self, body: dict[str, Any]) -> dict[str, Any]:
        """一级清空市场数据（[D-133]；运行中批次由 L0 拒绝）。"""
        phrase = _optional_text(body.get("phrase")) or ""
        if phrase != "清除市场数据":
            return envelope_payload(
                ResultEnvelope.validation_failed(
                    "一级清库须输入确认词「清除市场数据」"
                )
            )
        face = None if self.settings is None else getattr(self.settings, "data_sync", None)
        return self._call_face(
            "settings-data-clear", face, "L0 数据同步面",
            lambda sync: sync.clear_market(),
        )

    def api_settings_data_sync_cancel(self, body: dict[str, Any]) -> dict[str, Any]:
        """请求停止当前批次（任务间与网关调用前后生效；[D-130]）。"""
        _ = body
        face = None if self.settings is None else getattr(self.settings, "data_sync", None)
        return self._call_face(
            "settings-data-sync-cancel", face, "L0 数据同步面",
            lambda sync: sync.cancel(),
        )

    def api_settings_store_relocate(self, body: dict[str, Any]) -> dict[str, Any]:
        """更改存储位置：迁移 / 仅切换 / 恢复默认（[02 §2.1]；[D-129]）。

        成功后置重启闸门——本进程手里的 `Store` 仍指向旧根，**不假装已切换**。
        """
        return self._call_face(
            "settings-store-relocate", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_relocate(face, body),
        )

    def api_settings_llm_bootstrap(self, body: dict[str, Any]) -> dict[str, Any]:
        """保存 LLM 引导配置（``base_url`` / ``model`` / ``api_key``）并热更新（[D-129]）。"""
        return self._call_face(
            "settings-llm-bootstrap", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_llm_bootstrap(face, body),
        )

    def api_settings_apply(self, body: dict[str, Any]) -> dict[str, Any]:
        """应用一条设置——经 [01 §7] 统一落值面生效并留痕（**不旁路写盘**，GWT-2）。

        动作三选一：`set`（取 `value`）/ `enable` / `disable`（布尔条目的两个动作）。
        表单控件一律以**字符串**回传（`<select>` / `<input>` 的值都是字符串），故此处按
        条目的 ``value_schema.type`` **就地还原成声明类型**再落值——类型不符即
        `validation_failed`，不猜测。
        """
        return self._call_face(
            "settings-apply", self.settings, "L0 存储 / 配置面",
            lambda face: ResultEnvelope.ok(self._apply_config(face, body)),
        )

    def api_settings_agent_rollback(self, body: dict[str, Any]) -> dict[str, Any]:
        """回滚一条运行期档位变更——按变更前取值回放，回放本身也是一次变更（[D-143]）。"""
        return self._call_face(
            "settings-agent-rollback", self.reflection, "L6 反思演进面",
            lambda face: ResultEnvelope.ok(
                face.rollback_agent_change(str(_required(body, "change_id")))
            ),
        )

    def api_settings_convert(self, body: dict[str, Any]) -> dict[str, Any]:
        """明密双向转换：**源不动、写新根**（[02 §2.2](../../docs/技术架构-v2/02-L0-本地优先基座.md)）。

        转换**不碰当前存储根**（[`Store.convert`] 收尾整目录 `os.replace` 到目标路径），
        故本进程的句柄仍然有效、不置闸门——但**切换存储根须重启进程并指向新根**，
        回报里明说这一点。目标落在当前根之内即拒：那等于把新根写进旧根里。
        """
        return self._call_face(
            "settings-convert", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_convert(face, body),
        )

    def api_settings_backup_run(self, body: dict[str, Any]) -> dict[str, Any]:
        """一键备份：**输入密码 → 单个加密文件**（[02 §8.1]；本体归 L0，本层只做门）。

        归档**不得落在存储根之内**——`wipe_all` 删的是整个存储根，把备份放进去等于
        清空时连备份一起删（[02 §8.3] 明说「备份文件位置由用户自行处理」）。
        """
        return self._call_face(
            "settings-backup-run", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_backup(face, body),
        )

    def api_settings_restore(self, body: dict[str, Any]) -> dict[str, Any]:
        """恢复：**输入密码 → 完整还原**；成功后置重启闸门（覆盖写的是运行中的存储根）。"""
        return self._call_face(
            "settings-restore", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_restore(face, body),
        )

    def api_settings_wipe(self, body: dict[str, Any]) -> dict[str, Any]:
        """完全清空：**三次确认**（三枚有序令牌）→ 彻底删除本地数据；成功后置重启闸门。"""
        return self._call_face(
            "settings-wipe", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_wipe(face, body),
        )

    def api_settings_credential(self, body: dict[str, Any]) -> dict[str, Any]:
        """凭据新增 / 重设 / 吊销（[02 §3]；`secrets` 分区恒加密、惰性解锁）。

        未解锁即 `unavailable` + 点名——**不静默当作「没有凭据」**。
        """
        return self._call_face(
            "settings-credential", self.settings, "L0 存储 / 配置面",
            lambda face: self._run_credential(face, body),
        )

    def api_settings_online(self, body: dict[str, Any]) -> dict[str, Any]:
        """同步在线态（[02 §7] 的检测面缺口由**出口侧**补上）。

        平台侧没有网络探测（`EgressGateway.online` 是手动态），而**唯一的探测器就在出口**：
        浏览器。壳把 `navigator.onLine` 的变化报到这里，经 `L1Runtime.set_online` 翻转—
        从离线翻回在线时**按策略补跑**待补到期点（03 §6）。重复报同一态无副作用。
        """
        return self._call_face(
            "settings-online", self.settings, "L0 存储 / 配置面",
            lambda face: self._set_online(face, body),
        )

    def api_settings_network_export(self, body: dict[str, Any]) -> dict[str, Any]:
        """导出网络活动报告到导出目录（[02 §6]「可导出」）；回路径与条数。"""
        return self._call_face(
            "settings-network-export", self._gateway_face(),
            "L0 出网网关面",
            lambda face: self._export_network(face, body),
        )

    def _gateway_face(self) -> Any:
        """出网网关的取数口（挂在 `settings` 门面上；门面缺席即 `None`，由 `_call_face` 点名）。"""
        return None if self.settings is None else getattr(self.settings, "gateway", None)

    def _apply_config(self, face: Any, body: dict[str, Any]) -> dict[str, Any]:
        registry = face.config_registry
        config_id = str(_required(body, "config_id"))
        action = str(body.get("action") or "set")
        entry = registry.entry(config_id)
        if entry is None:
            raise ValueError(f"该设置面不认这个条目：{config_id!r}（不在统一配置注册表内）")
        if action in ("enable", "disable"):
            value: Any = action == "enable"
        elif action == "set":
            if "value" not in body:
                raise ValueError(f"{config_id} 的 set 动作须给出 value")
            value = _coerce_value(entry, body.get("value"))
        else:
            raise ValueError(f"未知的设置动作：{action!r}（只认 set / enable / disable）")
        change = registry.set(config_id, value, trace_id=f"ui-settings/{config_id}")
        return {
            "config_id": config_id,
            "changed": change is not None,
            "change_id": getattr(change, "change_id", None),
        }

    def _run_convert(self, face: Any, body: dict[str, Any]) -> Any:
        to_format = str(_required(body, "to_format")).strip()
        target = str(_required(body, "target")).strip()
        root = Path(face.store.root)
        current = str(getattr(face.store, "mode", ""))
        if to_format not in ("plain", "encrypted"):
            raise ValueError(f"目标格式只认 plain / encrypted：{to_format!r}")
        if to_format == current:
            raise ValueError(f"当前存储已是该格式（{current}），无需转换")
        dest = Path(target)
        if _is_inside(dest, root):
            raise ValueError(
                f"目标根不得落在当前存储根之内（{dest}）——新根须与源并列或在他处"
            )
        if face.convert_store is None:
            return ResultEnvelope.unavailable(
                "明密转换口未注入设置面（装配归组合根）", last_updated_at=_now()
            )
        envelope = _store_errors(lambda: face.convert_store(
            root,
            dest,
            to_format=to_format,
            source_passphrase=str(body.get("source_passphrase") or "").strip() or None,
            target_passphrase=str(body.get("target_passphrase") or "").strip() or None,
        ))
        if isinstance(envelope, ResultEnvelope):
            return envelope
        return {
            "target": str(dest),
            "to_format": to_format,
            "note": "源根未改动；凭据分区原样搬运，凭据口令不变。"
                    "切换存储根须重启进程并指向新根",
        }

    def _run_relocate(self, face: Any, body: dict[str, Any]) -> Any:
        """迁移 / 切换 / 恢复默认存储位置（[02 §2.1]；[D-129]）。"""
        mode = str(body.get("mode") or "migrate").strip()
        if mode not in ("migrate", "switch", "reset"):
            raise ValueError(f"存储位置动作只认 migrate / switch / reset：{mode!r}")
        boot = getattr(face, "root_boot", None)
        if boot is None:
            return ResultEnvelope.unavailable(
                "存储根指针未接入设置面（装配归组合根）", last_updated_at=_now()
            )
        current = Path(str(face.store.root))
        env_override = str(os.environ.get("ST_AGENT_ROOT") or "").strip()
        env_note = (
            f"；注意环境变量 ST_AGENT_ROOT={env_override} 仍优先于启动指针，"
            "重启前请先移除它"
            if env_override else ""
        )
        if mode == "reset":
            changed = boot.clear()
            self.restart_gate.reason = (
                f"存储位置已恢复为平台默认 {boot.default}：请重启应用后再继续；"
                "当前进程仍指向旧根"
            )
            return {
                "target_root": str(boot.default),
                "mode": "reset",
                "restart_required": True,
                "note": ("已删除启动指针" if changed else "启动指针原本就不存在") + env_note,
            }
        target = Path(str(_required(body, "target")).strip()).expanduser()
        if target.resolve() == current.resolve():
            raise ValueError("目标根不得与当前存储根相同")
        if _is_inside(target, current):
            raise ValueError(f"目标根不得落在当前存储根之内（{target}）")
        if mode == "switch":
            if not Store.exists(target):
                raise ValueError(
                    f"目标根不是既有存储（{target}）：请选择含 store.json / keyfile.json 的目录，"
                    "或改用迁移"
                )
            boot.write(target)
            self.restart_gate.reason = (
                f"存储位置已切换至 {target}：请重启应用后再继续；当前进程仍指向旧根"
            )
            return {
                "target_root": str(target),
                "mode": "switch",
                "restart_required": True,
                "note": "只写指针，未复制任何数据" + env_note,
            }
        if target.exists() and any(target.iterdir()):
            raise ValueError(f"目标根 {target} 非空：迁移只写全新根")
        target.parent.mkdir(parents=True, exist_ok=True)
        if face.convert_store is None:
            return ResultEnvelope.unavailable(
                "明密转换口未注入设置面（装配归组合根）", last_updated_at=_now()
            )
        source_pass = str(body.get("passphrase") or "").strip() or None
        target_pass = source_pass if str(face.store.mode) == "encrypted" else None
        envelope = _store_errors(lambda: face.convert_store(
            current,
            target,
            to_format=str(face.store.mode),
            source_passphrase=source_pass,
            target_passphrase=target_pass,
        ))
        if isinstance(envelope, ResultEnvelope):
            return envelope
        boot.write(target)
        self.restart_gate.reason = (
            f"存储位置已迁移至 {target}：请重启应用后再继续；当前进程仍指向旧根 {current}"
        )
        return {
            "target_root": str(target),
            "mode": "migrate",
            "restart_required": True,
            "note": "源根未改动（可另行删除）；凭据分区原样搬运，凭据口令不变" + env_note,
        }

    def _run_llm_bootstrap(self, face: Any, body: dict[str, Any]) -> Any:
        """保存 LLM 引导配置并热更新（[02 §4]；[D-129]）。"""
        base_url = str(_required(body, "base_url")).strip()
        model = str(_required(body, "model")).strip()
        api_key = str(body.get("api_key") or "").strip()
        passphrase = str(body.get("passphrase") or "").strip() or None
        # 先校验 URL 形态（非法取值不得落进任何记录）
        OpenAiRoute(base_url=base_url, model=model)
        runtime = getattr(face, "llm_runtime", None)
        if runtime is None:
            return ResultEnvelope.unavailable(
                "LLM 运行时未接入设置面（装配归组合根）", last_updated_at=_now()
            )
        store = face.store
        if not bool(getattr(store, "secrets_unlocked", False)):
            if not passphrase:
                return ResultEnvelope.unavailable(
                    "凭据分区未解锁（凭据恒加密）：请填写凭据口令后再保存",
                    last_updated_at=_now(),
                )
            unlocked = _store_errors(lambda: store.unlock_secrets(passphrase))
            if isinstance(unlocked, ResultEnvelope):
                return unlocked
        config = LlmBootstrap(
            api_key=api_key or "unchanged", base_url=base_url, model=model
        )
        applied = runtime.apply_llm_bootstrap(config, rotate_key=bool(api_key))
        save_boot_config(store, base_url=base_url, model=model)
        updates = {"LLM_BASE_URL": base_url, "LLM_MODEL": model}
        if api_key:
            updates["LLM_API_KEY"] = api_key
        dotenv_written = False
        dotenv_note = "未接入引导 .env（跳过）"
        if face.dotenv_path:
            try:
                write_dotenv(face.dotenv_path, updates)
                dotenv_written = True
                dotenv_note = "已写回引导 .env（明文文件，仅开发 / 自用通道）"
            except Exception as exc:      # noqa: BLE001 —— 写不进 .env 不否定已生效的配置
                dotenv_note = f"未能写回 .env：{exc}；配置已存入本地配置并立即生效"
        return {
            "base_url": base_url,
            "model": model,
            "api_key_masked": mask_secret(api_key) if api_key else "",
            "host": applied.get("host"),
            "key_rotated": bool(api_key),
            "dotenv_written": dotenv_written,
            "dotenv": str(face.dotenv_path) if face.dotenv_path else None,
            "restart_required": False,
            "note": dotenv_note,
        }

    def _run_backup(self, face: Any, body: dict[str, Any]) -> Any:
        passphrase = str(_required(body, "passphrase"))
        root = Path(face.store.root)
        dest = str(body.get("dest") or "").strip()
        target = Path(dest) if dest else _default_backup_path(face, root)
        if _is_inside(target, root):
            raise ValueError(
                f"备份归档不得落在存储根之内（{target}）——存储根会被「完全清空」整体删除，"
                "备份放进去等于一起删"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        envelope = _store_errors(lambda: face.backup.create_backup(
            face.store, passphrase, target,
            include_data_cache=bool(body.get("include_data_cache")),
        ))
        if isinstance(envelope, ResultEnvelope):
            return envelope
        written = envelope
        return {
            "archive": str(written),
            "include_data_cache": bool(body.get("include_data_cache")),
            "partitions": list(_backup_partitions(face)),
            "note": "归档为独立加密容器（密码即本次输入者）；可复制到任何存储介质",
        }

    def _run_restore(self, face: Any, body: dict[str, Any]) -> Any:
        archive = str(_required(body, "archive"))
        passphrase = str(_required(body, "passphrase"))
        root = Path(face.store.root)
        target_root = str(body.get("target_root") or "").strip() or str(root)
        target_pass = str(body.get("target_passphrase") or "").strip() or None
        envelope = _store_errors(lambda: face.backup.restore_backup(
            archive, passphrase, target_root, target_passphrase=target_pass,
        ))
        if isinstance(envelope, ResultEnvelope):
            return envelope
        report = envelope
        over_live = Path(target_root) == root
        note = (
            "已覆盖写当前存储根：本进程手里的存储句柄随之失效，须重启后继续"
            if over_live else f"已还原到 {target_root}（当前存储根未动）"
        )
        if over_live:
            self.restart_gate.reason = (
                f"已从归档恢复并覆盖写存储根 {root}：本进程的存储句柄已失效，"
                "继续读写会作用于陈旧清单——请重启后再操作"
            )
        return {
            "restored_partitions": list(report.restored_partitions),
            "file_count": int(report.file_count),
            "archived_at": _stamp(report.archived_at),
            "target_root": str(target_root),
            "restart_required": over_live,
            "note": note,
        }

    def _run_wipe(self, face: Any, body: dict[str, Any]) -> Any:
        raw = body.get("confirmations")
        tokens = [str(item) for item in raw] if isinstance(raw, list) else []
        root = Path(face.store.root)
        envelope = _store_errors(lambda: face.backup.wipe_all(root, tokens))
        if isinstance(envelope, ResultEnvelope):
            return envelope
        report = envelope
        self.restart_gate.reason = (
            f"已完全清空存储根 {root}（含全部分区与派生数据）：本进程的数据面已停用，"
            "请重启后再操作；备份文件在存储根之外，由用户自行处理"
        )
        return {
            "deleted_partitions": list(report.deleted_partitions),
            "detail": str(report.detail),
            "restart_required": True,
            "note": "备份文件位置由用户自行处理——清空不触碰存储根之外的内容",
        }

    def _run_credential(self, face: Any, body: dict[str, Any]) -> Any:
        if not bool(getattr(face.store, "secrets_unlocked", False)):
            return ResultEnvelope.unavailable(
                "凭据分区未解锁（凭据恒加密、惰性解锁）：本次会话不可增改凭据——"
                "经启动参数提供凭据口令后可用",
                last_updated_at=_now(),
            )
        action = str(_required(body, "action"))
        credential_id = str(_required(body, "credential_id"))
        vault = face.vault
        if action == "add":
            view = vault.add(
                credential_id,
                str(_required(body, "kind")),
                str(_required(body, "value")),
                str(body.get("label") or ""),
            )
        elif action == "rotate":
            view = vault.rotate(credential_id, str(_required(body, "value")))
        elif action == "revoke":
            view = vault.revoke(credential_id)
        else:
            raise ValueError(f"未知的凭据动作：{action!r}（只认 add / rotate / revoke）")
        return {
            "credential_id": view.credential_id,
            "action": action,
            "masked": view.masked,
            "version": view.version,
            "note": "凭据明文不落日志、不出站；界面只显示掩码",
        }

    def _set_online(self, face: Any, body: dict[str, Any]) -> dict[str, Any]:
        flag = body.get("online")
        if not isinstance(flag, bool):
            raise ValueError(f"在线态须为布尔：{flag!r}")
        if face.set_online is None:
            return {"online": flag, "caught_up": 0,
                    "note": "在线态未接线（装配归组合根）：浏览器侧状态照常，后端未改"}
        runs = face.set_online(flag)
        return {"online": flag, "caught_up": len(runs or ()),
                "note": "由离线翻回在线时按策略补跑待补到期点；重复报同一态无副作用"}

    def _export_network(self, gateway: Any, body: dict[str, Any]) -> Any:
        exports_dir = getattr(self.settings, "exports_dir", None)
        if exports_dir is None:
            return ResultEnvelope.unavailable(
                "导出目录未注入设置面（装配归组合根）", last_updated_at=_now()
            )
        if not gateway.audit_enabled:
            return ResultEnvelope.unavailable(
                "出网审计未开启，无记录可导出（这是「未开启」，不是「没有请求」）",
                last_updated_at=_now(),
            )
        since = _window_start(body.get("hours"))
        report = gateway.export_report(since=since)
        target = Path(exports_dir) / f"network-activity-{_stamp(_now(), compact=True)}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"path": str(target), "count": int(report.get("count") or 0),
                "note": "报告只含计数与出网元数据（域名 / 目的 / 时间 / 数据量），不含请求内容"}
