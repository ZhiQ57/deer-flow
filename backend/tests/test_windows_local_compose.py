"""Windows 本地编排的数据卷与端口契约。"""

from pathlib import Path

import yaml

COMPOSE_PATH = Path(__file__).resolve().parents[2] / "docker" / "docker-compose-windows-local.yaml"


def test_windows_local_compose_keeps_existing_postgres_data_and_only_three_containers():
    """确认数据库数据卷直接挂载到 PGDATA，且五个宿主机端口不会误暴露到所有网卡。

    Args:
        无。
    Returns:
        None: 断言三个容器及数据卷、端口的配置。
    """
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]

    assert compose["name"] == "deerflow-dev"
    assert set(services) == {"postgres", "redis", "nginx"}
    assert services["postgres"]["volumes"] == ["postgres_data:/var/lib/postgresql/data"]
    assert compose["volumes"]["postgres_data"] == {"external": True, "name": "deerflow-dev-postgres-data"}
    assert compose["volumes"]["redis_data"] == {"external": True, "name": "deer-flow-redis-data"}
    assert {name: service["ports"] for name, service in services.items()} == {
        "postgres": ["127.0.0.1:7432:5432"],
        "redis": ["127.0.0.1:7379:6379"],
        "nginx": ["127.0.0.1:2026:2026"],
    }
