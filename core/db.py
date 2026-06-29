from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine, text


def load_client_env() -> None:
    """
    Load KEY=VALUE pairs from client.env in the project root.
    Does not overwrite existing environment variables.
    """
    env_path = Path(__file__).resolve().parents[1] / "client.env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default


def get_db_type() -> str:
    load_client_env()
    return (env_first("DB_TYPE", default="mysql") or "mysql").lower()


def get_engine():
    """
    Current default: MySQL via PyMySQL.

    SQL Server-ready path for later:
        DB_TYPE=sqlserver
        DB_HOST=your-sql-host
        DB_NAME=genesys_analytics
        DB_USER=...
        DB_PASSWORD=...
        DB_DRIVER=ODBC Driver 18 for SQL Server
    """
    load_client_env()
    db_type = get_db_type()

    host = env_first("MYSQL_HOST", "DB_HOST", default="127.0.0.1")
    port = env_first("MYSQL_PORT", "DB_PORT", default="3306")
    database = env_first("MYSQL_DATABASE", "DB_NAME", default="genesys")
    user = env_first("MYSQL_USER", "DB_USER", default="root")
    password = env_first("MYSQL_PASSWORD", "DB_PASSWORD", default="password")

    if db_type in ("mssql", "sqlserver", "sql_server"):
        driver = env_first("DB_DRIVER", default="ODBC Driver 18 for SQL Server")
        # pyodbc driver name needs plus signs in URL format.
        driver_url = (driver or "").replace(" ", "+")
        url = (
            f"mssql+pyodbc://{user}:{password}@{host}/{database}"
            f"?driver={driver_url}&TrustServerCertificate=yes"
        )
    else:
        url = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"

    return create_engine(url, pool_pre_ping=True, future=True)


def get_dialect_name() -> str:
    engine = get_engine()
    return engine.dialect.name


def test_connection():
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True, "MySQL connection OK"
    except Exception as exc:
        return False, f"MySQL connection failed: {exc}"
