"""源码映射 Compose 的关键启动约束测试。"""

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "docker" / "docker-compose-source-host.yaml"


def test_source_compose_installs_postgres_extra_and_keeps_gateway_logs_visible():
    """源码映射部署必须安装 PostgreSQL extra，并把 Gateway 日志交给 Docker。"""
    content = COMPOSE_FILE.read_text(encoding="utf-8")

    assert "UV_EXTRAS: postgres,${UV_EXTRAS:-}" in content
    assert 'DEER_FLOW_LOG_TO_FILE: "0"' in content
    assert 'DEER_FLOW_GATEWAY_RELOAD: "0"' in content
    assert "network_mode: host" in content
