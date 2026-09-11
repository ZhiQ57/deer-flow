FROM python:3.12-slim

WORKDIR /app
COPY backend/pyproject.toml ./backend/pyproject.toml
COPY backend/packages ./backend/packages
COPY mcp-extensions/sqltable-rag ./mcp-extensions/sqltable-rag
RUN pip install --no-cache-dir \
    "mcp>=1.20.0,<2" \
    "psycopg[binary]>=3.3.3" \
    "pyyaml>=6.0.3" \
    "sqlglot>=27.0.0"

ENV PYTHONPATH=/app/backend/packages/harness
ENV PYTHONUNBUFFERED=1
CMD ["python", "mcp-extensions/sqltable-rag/server.py", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8004"]
