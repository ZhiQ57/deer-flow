"""连接图检索器模块。"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence

from ..base import JoinGraphRetrieverBase
from .postgres_common import ConnectionProvider, execute_sql, qualified_table, require_sql
from ...schemas import (
    JoinEdge,
    JoinGraphRetrievalResult,
    JoinPath,
    RetrievalOptions,
)
from ...configs import IndexStoreSettings, JoinGraphRetrievalSettings


class PostgresJoinGraphRetriever(JoinGraphRetrieverBase):
    """PostgreSQL Schema Join Graph 召回器，用于补全候选表之间的 JOIN 路径。"""

    def __init__(
        self,
        connection_provider: ConnectionProvider,
        index_store: IndexStoreSettings,
        retrieval_settings: JoinGraphRetrievalSettings,
    ):
        """初始化 Join Graph 召回器。

        Args:
            connection_provider: 外部注入的 Schema 索引库连接提供器。
            index_store: 索引存储配置。
            retrieval_settings: Join Graph 召回配置。

        Returns:
            None。
        """
        self.connection_provider = connection_provider
        self.index_store = index_store
        self.retrieval_settings = retrieval_settings

    def expand_paths(self, table_names: Sequence[str], options: RetrievalOptions) -> list[JoinGraphRetrievalResult]:
        """查找候选表之间的 JOIN 路径。

        Args:
            table_names: 候选表名列表。
            options: 包含最大跳数的检索参数。

        Returns:
            Join Graph 召回结果列表。
        """
        seeds = [name for name in dict.fromkeys(table_names) if name]
        if len(seeds) < 2:
            return []

        edges = self._load_edges()
        paths = find_join_paths(seeds, edges, options.join_max_hops)
        if not paths:
            return []

        return [
            JoinGraphRetrievalResult(
                node="schema_join_graph",
                edges=_edges_from_paths(paths),
                paths=paths,
                metadata={"seed_tables": seeds, "join_path_count": len(paths)},
            )
        ]

    def _load_edges(self) -> list[JoinEdge]:
        """从 PostgreSQL 加载 Join Graph 边。

        Args:
            无。

        Returns:
            JoinEdge 列表。
        """
        sql = require_sql()
        statement = sql.SQL(
            """
            SELECT source_table, target_table, join_condition, edge_type, weight
            FROM {table}
            ORDER BY weight DESC, source_table, target_table
            LIMIT %(limit)s::integer
            """
        ).format(table=self._table())
        with self.connection_provider.connect() as conn:
            with conn.cursor() as cur:
                execute_sql(cur, statement, {"limit": self.retrieval_settings.max_edges})
                rows = cur.fetchall()
        return [
            JoinEdge(
                source_table=row[0],
                target_table=row[1],
                join_condition=row[2],
                edge_type=row[3],
                weight=float(row[4] or 0.0),
                metadata={},
            )
            for row in rows
        ]

    def _table(self):
        """获取 Join Graph 边表的安全 SQL 标识符。

        Args:
            无。

        Returns:
            psycopg.sql.SQL 对象。
        """
        return qualified_table(self.index_store.schema_name, self.index_store.join_edge_table_name)


def find_join_paths(table_names: Sequence[str], edges: Sequence[JoinEdge], max_hops: int) -> list[JoinPath]:
    """在候选表集合内查找限定跳数的 Join 路径。

    Args:
        table_names: 候选表名列表。
        edges: Join Graph 边列表。
        max_hops: 最大边数。

    Returns:
        JoinPath 列表。
    """
    seeds = [name for name in dict.fromkeys(table_names) if name]
    if len(seeds) < 2 or max_hops <= 0:
        return []

    adjacency = _build_adjacency(edges)
    paths: list[JoinPath] = []
    seen: set[tuple[str, ...]] = set()
    for index, source in enumerate(seeds):
        for target in seeds[index + 1 :]:
            for path in _find_paths(source, target, adjacency, max_hops):
                key = tuple(path.tables)
                if key in seen:
                    continue
                seen.add(key)
                paths.append(path)
    return sorted(paths, key=lambda item: item.score, reverse=True)


def _build_adjacency(edges: Sequence[JoinEdge]) -> dict[str, list[JoinEdge]]:
    """构建无向邻接表，便于从任意候选表扩展路径。

    Args:
        edges: Join Graph 边列表。

    Returns:
        表名到边列表的邻接表。
    """
    adjacency: dict[str, list[JoinEdge]] = {}
    for edge in edges:
        adjacency.setdefault(edge.source_table, []).append(edge)
        # JOIN 路径检索按无向图处理，但保留原始 join_condition 文本。
        reverse = JoinEdge(
            source_table=edge.target_table,
            target_table=edge.source_table,
            join_condition=edge.join_condition,
            edge_type=edge.edge_type,
            weight=edge.weight,
            metadata=edge.metadata,
        )
        adjacency.setdefault(edge.target_table, []).append(reverse)
    return adjacency


def _find_paths(
    source: str,
    target: str,
    adjacency: dict[str, list[JoinEdge]],
    max_hops: int,
) -> list[JoinPath]:
    """在限定跳数内查找两张表之间的路径。

    Args:
        source: 起点表。
        target: 终点表。
        adjacency: Join Graph 邻接表。
        max_hops: 最大边数。

    Returns:
        JoinPath 列表。
    """
    queue = deque([(source, [source], [])])
    paths: list[JoinPath] = []
    while queue:
        current, tables, path_edges = queue.popleft()
        if len(path_edges) >= max_hops:
            continue
        for edge in adjacency.get(current, []):
            if edge.target_table in tables:
                continue
            next_tables = [*tables, edge.target_table]
            next_edges = [*path_edges, edge]
            if edge.target_table == target:
                paths.append(JoinPath(tables=next_tables, edges=next_edges, score=_path_score(next_edges)))
                continue
            queue.append((edge.target_table, next_tables, next_edges))
    return paths


def _path_score(edges: Sequence[JoinEdge]) -> float:
    """计算 Join 路径得分。

    Args:
        edges: 路径中的边列表。

    Returns:
        路径分数；边越少、平均权重越高，分数越高。
    """
    if not edges:
        return 0.0
    avg_weight = sum(edge.weight for edge in edges) / len(edges)
    return avg_weight / len(edges)


def _edges_from_paths(paths: Sequence[JoinPath]) -> list[JoinEdge]:
    """从路径集合中提取去重边。

    Args:
        paths: Join 路径列表。

    Returns:
        去重后的 JoinEdge 列表。
    """
    unique: dict[tuple[str, str, str], JoinEdge] = {}
    for path in paths:
        for edge in path.edges:
            key = (edge.source_table, edge.target_table, edge.join_condition)
            unique.setdefault(key, edge)
    return list(unique.values())
