"""Native deterministic safety scanner for DeerFlow skills.

当前本机终端安全软件会隔离原始 ``orchestrator.py``，导致 Gateway 启动阶段
读取该文件失败。这里在包入口提供一个安全保守实现：不执行外部命令，不默认
放行技能安装/写入；当 SkillScan 启用时直接阻断相关操作。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deerflow.skills.skillscan.models import FindingSeverity, RuleSpec, ScanResult, SecurityFinding, StaticScanBlockedError, StaticScannerError


RULES: tuple[RuleSpec, ...] = (
    RuleSpec(
        rule_id="scanner-quarantine-policy",
        severity="CRITICAL",
        message="SkillScan 原规则实现被终端安全软件隔离，当前采用保守阻断策略。",
        remediation="使用经人工审核且不会被终端安全软件隔离的 SkillScan 实现后，再恢复技能安装或技能写入。",
    ),
)


def skill_scan_enabled(app_config: Any | None = None) -> bool:
    """判断 SkillScan 是否启用。

    描述作用:
        从传入配置读取 ``skill_scan.enabled``，缺省保持安全优先并返回启用。

    Args参数说明:
        app_config: DeerFlow 应用配置对象，可为空。

    Return返回值:
        bool: ``True`` 表示启用保守扫描策略，``False`` 表示显式跳过。
    """

    skill_scan = getattr(app_config, "skill_scan", None)
    enabled = getattr(skill_scan, "enabled", True)
    return bool(enabled)


def _blocked_result(target: Path | None = None) -> ScanResult:
    """生成保守阻断扫描结果。

    描述作用:
        基于统一规则生成结构化 CRITICAL 发现，确保扫描器不可用时不会默认放行。

    Args参数说明:
        target: 被扫描的技能目录或归档路径，可为空。

    Return返回值:
        ScanResult: 包含阻断发现的扫描结果。
    """

    rule = RULES[0]
    finding: SecurityFinding = {
        "rule_id": rule.rule_id,
        "severity": rule.severity,
        "file": str(target) if target is not None else None,
        "line": None,
        "message": rule.message,
        "remediation": rule.remediation,
        "evidence": None,
    }
    return {"findings": [finding], "blocked": True, "scanner_errors": []}


def scan_archive_preflight(archive_path: Path) -> ScanResult:
    """扫描技能归档。

    描述作用:
        当原始静态扫描实现不可用时，保守阻断所有技能归档安装。

    Args参数说明:
        archive_path: 待扫描的技能归档路径。

    Return返回值:
        ScanResult: 保守阻断结果。
    """

    return _blocked_result(archive_path)


def scan_skill_dir(skill_dir: Path) -> ScanResult:
    """扫描技能目录。

    描述作用:
        当原始静态扫描实现不可用时，保守阻断所有技能目录写入或安装。

    Args参数说明:
        skill_dir: 待扫描的技能目录路径。

    Return返回值:
        ScanResult: 保守阻断结果。
    """

    return _blocked_result(skill_dir)


def format_static_findings(findings: list[SecurityFinding]) -> str:
    """格式化静态扫描发现。

    描述作用:
        将结构化发现转换为短文本，供异常消息或接口响应使用。

    Args参数说明:
        findings: 静态扫描发现列表。

    Return返回值:
        str: 格式化后的文本。
    """

    return "; ".join(
        f"{finding['rule_id']} ({finding['severity']}) at {finding.get('file') or '<unknown>'}: {finding['message']}"
        for finding in findings
    )


def enforce_static_scan(skill_dir: Path, *, skill_name: str | None = None, app_config: Any | None = None) -> list[SecurityFinding]:
    """执行保守静态扫描策略。

    描述作用:
        如果 SkillScan 启用，则阻断技能安装/写入，避免安全扫描不可用时默认放行；
        如果配置显式关闭 SkillScan，则返回空发现列表。

    Args参数说明:
        skill_dir: 待扫描的技能目录路径。
        skill_name: 技能名称，可为空。
        app_config: DeerFlow 应用配置对象，可为空。

    Return返回值:
        list[SecurityFinding]: 显式关闭扫描时返回空列表。
    """

    if not skill_scan_enabled(app_config):
        return []

    result = scan_skill_dir(skill_dir)
    raise StaticScanBlockedError(result["findings"], skill_name=skill_name)

__all__ = [
    "RULES",
    "FindingSeverity",
    "RuleSpec",
    "ScanResult",
    "SecurityFinding",
    "StaticScanBlockedError",
    "StaticScannerError",
    "enforce_static_scan",
    "format_static_findings",
    "scan_archive_preflight",
    "scan_skill_dir",
    "skill_scan_enabled",
]
