from __future__ import annotations

from .memory_store import MemoryIndexStore


class PostgresGraphStore(MemoryIndexStore):
    """Graph store seam for production PostgreSQL tables.

    Reference operations remain available in memory until the host supplies a
    connection/transaction adapter; this keeps the SDK independent of ORM.
    """
