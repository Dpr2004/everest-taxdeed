"""Conexao SQLite centralizada. Migra p/ Postgres trocando so este arquivo."""
import os
import sqlite3
from pathlib import Path
from contextlib import contextmanager

DB_PATH = os.environ.get("DB_PATH", "./data/taxdeed.db")


def get_connection():
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@contextmanager
def cursor():
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_schema():
    schema_file = Path(__file__).parent / "schema.sql"
    with open(schema_file) as f:
        sql = f.read()
    conn = get_connection()
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()
