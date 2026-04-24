"""Remove lotes com parcel_id 'AID_*' do scraper antigo (sem dados reais)."""
import sqlite3
from pathlib import Path

DB = Path(__file__).parent.parent / "data" / "taxdeed.db"
c = sqlite3.connect(str(DB))

before = c.execute("SELECT COUNT(*) FROM lots WHERE parcel_id LIKE 'AID_%'").fetchone()[0]
print(f"Lotes AID_* encontrados: {before}")

c.execute("DELETE FROM dd WHERE lot_id IN (SELECT id FROM lots WHERE parcel_id LIKE 'AID_%')")
c.execute("DELETE FROM scores WHERE lot_id IN (SELECT id FROM lots WHERE parcel_id LIKE 'AID_%')")
c.execute("DELETE FROM comps WHERE lot_id IN (SELECT id FROM lots WHERE parcel_id LIKE 'AID_%')")
c.execute("DELETE FROM liens WHERE lot_id IN (SELECT id FROM lots WHERE parcel_id LIKE 'AID_%')")
c.execute("DELETE FROM decisions WHERE lot_id IN (SELECT id FROM lots WHERE parcel_id LIKE 'AID_%')")
c.execute("DELETE FROM results WHERE lot_id IN (SELECT id FROM lots WHERE parcel_id LIKE 'AID_%')")
c.execute("DELETE FROM alerts WHERE lot_id IN (SELECT id FROM lots WHERE parcel_id LIKE 'AID_%')")

r = c.execute("DELETE FROM lots WHERE parcel_id LIKE 'AID_%'")
print(f"Deletados: {r.rowcount} lotes")

total = c.execute("SELECT COUNT(*) FROM lots").fetchone()[0]
com_bid = c.execute("SELECT COUNT(*) FROM lots WHERE min_bid > 0").fetchone()[0]
print(f"Lotes restantes: {total} (com min_bid: {com_bid})")

c.commit()
c.close()
print("Limpeza OK")
