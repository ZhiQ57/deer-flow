"""VSSAG 文档虚拟目录访问上下文。"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class VSSAGAccessContext(BaseModel):
    """VSSAG 文档虚拟目录的调用方访问上下文。

    `user_id` 仅用于 SDK 内部的权限判断和隔离标签计算，不能出现在
    模型可见的虚拟文件路径中。
    """

    model_config = ConfigDict(extra="forbid")

    user_id: str
    user_role: str
    authz_attributes: dict[str, Any]
    thread_id: str
    run_id: str

    @field_validator("user_id", "user_role", "thread_id", "run_id")
    @classmethod
    def _validate_required_value(cls, value: str) -> str:
        """校验调用上下文中的必填字符串。

        Args:
            value: 待校验的字段值。

        Returns:
            去除首尾空白后的字段值。

        Raises:
            ValueError: 字段为空。
        """

        normalized = str(value).strip()
        if not normalized:
            raise ValueError("VSSAGAccessContext 字段不能为空")
        return normalized

    @property
    def user_label(self) -> str:
        """生成只用于内部路径映射的不透明用户标签。

        Returns:
            与当前授权和运行上下文绑定的安全路径组件。
        """

        payload = json.dumps(
            {
                "authz_attributes": self.authz_attributes,
                "run_id": self.run_id,
                "thread_id": self.thread_id,
                "user_id": self.user_id,
                "user_role": self.user_role,
            },
            default=str,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return "u-" + hashlib.blake2s(
            payload.encode("utf-8"),
            digest_size=12,
        ).hexdigest()

    def allowed_source_ids(self) -> set[str] | None:
        """解析调用方授予的来源白名单。

        Returns:
            白名单未提供时返回空值；显式空列表返回空集合。
        """

        return self._attribute_values("source_ids")

    def allows_record(self, source_id: str, metadata: Mapping[str, Any]) -> bool:
        """根据调用上下文判断一条文档记录是否可见。

        Args:
            source_id: 文档来源标识。
            metadata: 索引记录的权限相关元数据。

        Returns:
            当前调用方可以读取记录时返回真。
        """

        source_ids = self.allowed_source_ids()
        if source_ids is not None and str(source_id) not in source_ids:
            return False

        allowed_roles = self._metadata_values(metadata, "allowed_roles")
        if allowed_roles and self.user_role not in allowed_roles:
            return False

        knowledge_base_ids = self._attribute_values("knowledge_base_ids")
        if knowledge_base_ids is not None:
            record_knowledge_bases = self._metadata_values(
                metadata,
                "knowledge_base_id",
                "kb_id",
            )
            if not record_knowledge_bases or not (
                record_knowledge_bases & knowledge_base_ids
            ):
                return False

        record_thread_id = metadata.get("thread_id") or metadata.get("session_id")
        if (
            record_thread_id is not None
            and str(record_thread_id) != self.thread_id
        ):
            return False
        record_run_id = metadata.get("run_id")
        if record_run_id is not None and str(record_run_id) != self.run_id:
            return False

        scope_type = str(
            self.authz_attributes.get(
                "scope_type",
                self.authz_attributes.get("kb_scope", "all"),
            )
        ).strip().lower()
        scope_type = {
            "personal": "own",
            "private": "own",
            "team": "workspace",
            "shared": "workspace",
            "session": "sessions",
        }.get(scope_type, scope_type)
        if scope_type not in {"all", "own", "workspace", "sessions"}:
            return False

        owner = metadata.get("user_id")
        owner_matches = owner is not None and str(owner) == self.user_id
        workspace_ids = self._attribute_values("workspace_ids")
        record_workspaces = self._metadata_values(
            metadata,
            "workspace_id",
            "workspace_slug",
        )
        workspace_matches = bool(
            workspace_ids and record_workspaces and record_workspaces & workspace_ids
        )

        if scope_type == "own":
            return owner_matches
        if scope_type == "workspace":
            return workspace_matches
        if scope_type == "sessions":
            return owner_matches and record_thread_id is not None
        if source_ids is not None:
            return True
        return owner_matches or workspace_matches

    def _attribute_values(self, name: str) -> set[str] | None:
        """将授权属性中的字符串或字符串列表转换为集合。"""

        if name not in self.authz_attributes:
            return None
        value = self.authz_attributes[name]
        if isinstance(value, str):
            return {value} if value else set()
        if not isinstance(value, (list, tuple, set, frozenset)):
            return set()
        return {str(item) for item in value if str(item)}

    @staticmethod
    def _metadata_values(
        metadata: Mapping[str, Any],
        *names: str,
    ) -> set[str]:
        """读取记录元数据中的一个或多个字符串值。"""

        values: set[str] = set()
        for name in names:
            value = metadata.get(name)
            if isinstance(value, str) and value:
                values.add(value)
            elif isinstance(value, (list, tuple, set, frozenset)):
                values.update(str(item) for item in value if str(item))
        return values
