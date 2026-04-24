"""Debug do estado do DB - util pra ver totais, campos preenchidos, sales futuros."""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "taxdeed.db"
c = sqlite3.connect(str(DB))
c.row_factory = sqlite3.Row


def section(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)


section("TOTAIS DE LOTS")
for row in c.execute("""
    SELECT
        COUNT(*) AS total,
        SUM(CASE WHEN just_value > 0 THEN 1 ELSE 0 END) AS com_jv,
        SUM(CASE WHEN min_bid > 0 THEN 1 ELSE 0 END) AS com_bid,
        SUM(CASE WHEN just_value > 0 AND min_bid > 0 THEN 1 ELSE 0 END) AS com_ambos
    FROM lots
"""):
    print(dict(row))

section("SALES POR DATA")
for row in c.execute("""
    SELECT
        s.sale_date,
        c.codigo AS condado,
        COUNT(l.id) AS lotes,
        SUM(CASE WHEN l.just_value > 0 THEN 1 ELSE 0 END) AS com_jv,
        SUM(CASE WHEN l.min_bid > 0 THEN 1 ELSE 0 END) AS com_bid
    FROM sales s
    LEFT JOIN lots l ON l.sale_id = s.id
    JOIN counties c ON c.id = s.county_id
    GROUP BY s.id
    ORDER BY s.sale_date
"""):
    print(dict(row))

section("AMOSTRA DE 3 LOTES COM DADOS")
for row in c.execute("""
    SELECT parcel_id, address, city, min_bid, assessed_value
    FROM lots
    WHERE min_bid > 0
    ORDER BY min_bid DESC
    LIMIT 3
"""):
    print(dict(row))

section("CAMPOS PREENCHIDOS")
total = c.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
for col in ["parcel_id", "min_bid", "just_value", "address", "city", "zip",
            "assessed_value", "case_num", "tax_cert_num"]:
    try:
        row = c.execute(
            f"SELECT SUM(CASE WHEN {col} IS NOT NULL AND {col} != '' AND {col} != 0 THEN 1 ELSE 0 END) FROM lots"
        ).fetchone()
        print(f"  {col}: {row[0] or 0}/{total}")
    except Exception as e:
        print(f"  {col}: ERRO ({e})")

section("RUN LOGS RECENTES")
for row in c.execute("""
    SELECT worker, status, started_at
    FROM run_logs
    ORDER BY started_at DESC
    LIMIT 10
"""):
    print(dict(row))

c.close()
