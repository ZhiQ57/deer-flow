FROM python:3.12-slim

WORKDIR /app
COPY mcp-extensions/sql-execute/pyproject.toml ./pyproject.toml
RUN pip install --no-cache-dir \
    "mcp>=1.20.0,<2" \
    "pymysql>=1.1.1" \
    "psycopg[binary]>=3.3.3" \
    "pyyaml>=6.0.3" \
    "sqlglot>=27.0.0"
COPY mcp-extensions/sql-execute/server.py ./server.py
COPY mcp-extensions/sql-execute/config.yaml ./config.yaml

ENV PYTHONUNBUFFERED=1
ENV SQL_EXECUTE_CONFIG_PATH=/app/config.yaml
EXPOSE 8003
CMD ["python", "server.py", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8003"]
