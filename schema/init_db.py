from pathlib import Path
import sys
from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import get_engine, test_connection

SCHEMA_DIR = Path(__file__).resolve().parent

def main():
    ok, msg = test_connection()
    print(msg)
    if not ok:
        return 1
    sql = (SCHEMA_DIR / "schema.sql").read_text(encoding="utf-8")
    with get_engine().begin() as conn:
        for stmt in [s.strip() for s in sql.split(";") if s.strip()]:
            conn.execute(text(stmt))
    print("Database schema initialized.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
