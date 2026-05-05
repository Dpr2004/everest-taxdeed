"""
test_regrid.py — testa REGRID_API_KEY e mostra exatamente o que API responde.

USO:
  # Local (com .env ou export REGRID_API_KEY=xxx):
  python scripts/test_regrid.py

  # Ou passa direto:
  REGRID_API_KEY=seu_token python scripts/test_regrid.py

  # Ou via CI:
  rodar como step e ver log
"""
import os
import sys
import json
import requests
from pathlib import Path

# Tenta carregar .env local
env_file = Path(__file__).resolve().parent.parent / ".env"
if env_file.exists():
    for line in env_file.read_text(encoding='utf-8').splitlines():
        if line.startswith("REGRID_API_KEY="):
            os.environ["REGRID_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
            break

TOKEN = os.environ.get("REGRID_API_KEY", "").strip()
URL = "https://app.regrid.com/api/v2/parcels/parcelnumb"

print("=" * 60)
print("TESTE REGRID API KEY")
print("=" * 60)

if not TOKEN:
    print("\n[FAIL] REGRID_API_KEY nao encontrado no env nem .env local")
    print("   Setar via: export REGRID_API_KEY=seu_token")
    sys.exit(1)

print(f"\nToken length: {len(TOKEN)}")
print(f"Token prefix: {TOKEN[:10]}...")
print(f"Token suffix: ...{TOKEN[-6:]}")

# Test cases reais com parcels conhecidos por condado
testes = [
    ("polk", "31-31-34-0000-0003-3040"),  # primeiro lot Polk no DB
    ("brevard", "2934586"),                # Brevard
    ("highlands", "C-04-34-28-160-3390-0090"),  # Highlands
    ("orange", "162231807902030"),         # Orange — 1910 Park Manor Dr
]

print("\n" + "-" * 60)
print("Testando 4 parcels reais de condados diferentes:")
print("-" * 60)

ok_count = 0
for slug, parcel in testes:
    print(f"\n[{slug}] parcel={parcel}")
    try:
        # Tenta com hifen
        for variation in [parcel, parcel.replace("-", "").replace(" ", "")]:
            r = requests.get(URL, params={
                "parcelnumb": variation,
                "path": f"/us/fl/{slug}",
                "token": TOKEN,
            }, timeout=20)
            print(f"  HTTP {r.status_code} (variation '{variation}')")
            if r.status_code == 200:
                data = r.json()
                feats = (data.get("parcels") or {}).get("features") or []
                print(f"    parcels.features count: {len(feats)}")
                if feats:
                    fields = feats[0].get("properties", {}).get("fields", {})
                    keys = list(fields.keys())[:8]
                    print(f"    fields keys (top 8): {keys}")
                    if fields.get("parval"):
                        print(f"    just_value: {fields.get('parval')}")
                    if fields.get("saddno") or fields.get("saddstr"):
                        print(f"    address: {fields.get('saddno')} {fields.get('saddstr')}")
                    ok_count += 1
                    break
            elif r.status_code == 401:
                print(f"    >>> TOKEN INVALIDO/EXPIRADO. Renovar em app.regrid.com <<<")
                sys.exit(2)
            elif r.status_code in (402, 403):
                print(f"    >>> QUOTA/PLAN issue: {r.text[:200]} <<<")
                sys.exit(3)
            elif r.status_code == 404:
                print(f"    parcel nao existe no Regrid (esperado pra alguns)")
            else:
                print(f"    body: {r.text[:200]}")
                break
    except Exception as e:
        print(f"  EXCEPTION: {type(e).__name__}: {e}")

print()
print("=" * 60)
print(f"RESULTADO: {ok_count}/4 parcels enriquecidos com sucesso")
if ok_count == 0:
    print("==> Token presente mas API nao retornou dados pra NENHUM teste.")
    print("==> Possiveis causas: slug FL alterado pelo Regrid; conta sem")
    print("    cobertura FL; rate limit; firewall.")
elif ok_count < 4:
    print("==> Token funciona PARCIALMENTE — alguns parcels nao existem no Regrid")
    print("    (esperado pra parcels muito antigos ou novos)")
else:
    print("==> Token + API 100% funcional. Bug deve estar no worker (slug map ou query).")
print("=" * 60)
