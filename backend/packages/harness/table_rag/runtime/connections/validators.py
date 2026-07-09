"""运行时连接校验通用结构和入口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Literal

from ...configs import TableRAGConfig

IssueSeverity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class ConnectionValidationIssue:
    """连接校验问题，描述失败项、严重级别和修复提示。"""

    code: str
    message: str
    severity: IssueSeverity = "error"
    hint: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectionValidationResult:
    """连接校验结果，汇总数据库能力和索引结构检查情况。"""

    database_type: str
    issues: list[ConnectionValidationIssue] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        """返回校验是否没有错误级问题。

        Args:
            无。

        Returns:
            没有 error 级问题时返回 True。
        """
        return not any(issue.severity == "error" for issue in self.issues)

    @property
    def errors(self) -> list[ConnectionValidationIssue]:
        """返回错误级问题列表。

        Args:
            无。

        Returns:
            error 级问题列表。
        """
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[ConnectionValidationIssue]:
        """返回警告级问题列表。

        Args:
            无。

        Returns:
            warning 级问题列表。
        """
        return [issue for issue in self.issues if issue.severity == "warning"]

    def add_issue(
        self,
        code: str,
        message: str,
        *,
        severity: IssueSeverity = "error",
        hint: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """追加一个连接校验问题。

        Args:
            code: 稳定问题代码。
            message: 面向用户的错误描述。
            severity: 问题严重级别。
            hint: 可选修复提示。
            metadata: 可选附加上下文。

        Returns:
            None。
        """
        self.issues.append(
            ConnectionValidationIssue(
                code=code,
                message=message,
                severity=severity,
                hint=hint,
                metadata=metadata or {},
            )
        )


class ConnectionValidator(ABC):
    """连接校验器抽象类，用于检查外部注入连接是否满足 TableRAG 要求。"""

    @abstractmethod
    def validate(self, config: TableRAGConfig) -> ConnectionValidationResult:
        """执行连接校验。

        Args:
            config: TableRAG 总配置。

        Returns:
            连接校验结果。
        """


def validate_connection(validator: ConnectionValidator, config: TableRAGConfig) -> ConnectionValidationResult:
    """执行通用连接校验入口。

    Args:
        validator: 具体数据库连接校验器。
        config: TableRAG 总配置。

    Returns:
        连接校验结果。
    """
    return validator.validate(config)