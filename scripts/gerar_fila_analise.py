"""Gera web/fila-analise.json com candidatos top ranqueados pra
analise profunda no LOTES Analyzer.

Filtros rigorosos (zero simulacao, so dados reais do DB):
- Ratio just_value / min_bid >= 3.0 (oportunidade financeira real)
- min_bid > 0 (descartar lotes sem bid definido)
- just_value > 0 (descartar lotes sem avaliacao)
- FEMA X, AE, ou desconhecido (NAO V/VE zona catastrofica)
- sale_date futuro (nao analisar o que ja passou)

Ranking:
- Score = (just_value / min_bid) * weight_fema * weight_condado
- Top 50 por dia entram na fila

Integracao:
- Se FILA_AUTO_SEND=true e LOTES_TUNNEL_URL estiver setado, faz POST /api/queue
- Senao, so publica o JSON pro dashboard consumir manualmente
"""
import json
import os
import sys
import urllib.request
import urllib.error
from datetime import date
from pathlib import Path

# Adiciona a raiz do repo ao sys.path para importar src.*
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.db.connection import cursor


# Filtros duros
RATIO_MIN = float(os.environ.get("FILA_RATIO_MIN", "3.0"))
FEMA_BANIDOS = {"V", "VE"}  # zonas catastroficas de seguro caro/impossivel
TOP_N = int(os.environ.get("FILA_TOP_N", "50"))

# Integracao LOTES Analyzer
LOTES_TUNNEL_URL = os.environ.get("LOTES_TUNNEL_URL", "").rstrip("/")
LOTES_API_KEY = os.environ.get("LOTES_API_KEY", "")
AUTO_SEND = os.environ.get("FILA_AUTO_SEND", "false").lower() == "true"

# Pesos por condado (1.0 = neutro). Ajuste manual baseado em experiencia.
COUNTY_WEIGHTS = {
    "POLK": 1.0,
    "MARION": 1.0,
    "HIGHLANDS": 1.1,   # GDC lots tem upside
    "LAKE": 1.0,
    "ORANGE": 0.9,      # mercado competitivo, menos margem
    "OSCEOLA": 1.0,
    "PUTNAM": 1.1,      # rural, menor competicao
    "ST_LUCIE": 1.1,    # GDC Port St Lucie
    "LEE": 0.85,        # pos-Ian risco alto
    "BREVARD": 1.0,
    "CITRUS": 1.05,
}


def score_lote(row):
    jv = row["just_value"] or 0
    bid = row["min_bid"] or 0
    if bid <= 0 or jv <= 0:
        return 0
    ratio = jv / bid
    if ratio < RATIO_MIN:
        return 0

    # Weight FEMA
    fz = (row.get("flood_zone") or "").upper().strip()
    if fz in FEMA_BANIDOS:
        return 0  # descarta
    weight_fema = 1.0 if fz in ("X", "") else 0.85  # AE/A penaliza um pouco

    # Weight condado
    condado = (row.get("condado") or "").upper().strip().replace(".", "").replace(" ", "_")
    weight_cond = COUNTY_WEIGHTS.get(condado, 1.0)

    return ratio * weight_fema * weight_cond


def buscar_candidatos():
    with cursor() as cur:
        cur.execute("""
            SELECT
                l.id as lot_id, l.parcel_id, l.address, l.city,
                l.min_bid, l.just_value,
                s.sale_date, c.codigo as condado, c.state as estado,
                d.fema_flood_zone as flood_zone,
                sc.final_score as workers_score, sc.decision as workers_decision
            FROM lots l
            JOIN sales s ON s.id = l.sale_id
            JOIN counties c ON c.id = s.county_id
            LEFT JOIN dd d ON d.lot_id = l.id
            LEFT JOIN scores sc ON sc.lot_id = l.id
            WHERE s.sale_date >= DATE('now')
              AND l.min_bid > 0
              AND l.just_value > 0
            ORDER BY s.sale_date ASC
        """)
        return [dict(r) for r in cur.fetchall()]


def ja_analisado(parcel_id, analisados):
    """Verifica se ja foi analisado recentemente (evita re-enfileirar)."""
    return parcel_id in analisados


def carregar_analisados_recentes():
    """Le reports/PARCEL.json para saber quem ja foi analisado."""
    reports_dir = Path("web/reports")
    if not reports_dir.exists():
        return set()
    return {p.stem for p in reports_dir.glob("*.json")}


def enviar_fila_lotes(lotes):
    """POST /api/queue no tunnel do LOTES com lista de lotes."""
    if not LOTES_TUNNEL_URL:
        print("[fila] LOTES_TUNNEL_URL nao setado — skip auto-send")
        return False

    payload = json.dumps({"lotes": lotes}).encode("utf-8")
    req = urllib.request.Request(
        f"{LOTES_TUNNEL_URL}/api/queue",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-API-Key": LOTES_API_KEY,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            print(f"[fila] LOTES respondeu {resp.status}: {body[:200]}")
            return True
    except urllib.error.HTTPError as e:
        print(f"[fila] HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
    except Exception as e:
        print(f"[fila] Falha ao enviar: {e}")
    return False


def main():
    candidatos = buscar_candidatos()
    print(f"[fila] Candidatos brutos: {len(candidatos)}")

    analisados = carregar_analisados_recentes()
    print(f"[fila] Ja analisados (reports existentes): {len(analisados)}")

    ranqueados = []
    for c in candidatos:
        if ja_analisado(c["parcel_id"], analisados):
            continue
        s = score_lote(c)
        if s > 0:
            c["ranking_score"] = round(s, 2)
            ranqueados.append(c)

    ranqueados.sort(key=lambda x: x["ranking_score"], reverse=True)
    top = ranqueados[:TOP_N]
    print(f"[fila] Top {TOP_N} candidatos apos filtros: {len(top)}")

    # Formato publico (dashboard consome)
    payload = {
        "gerado_em": date.today().isoformat(),
        "total_candidatos": len(top),
        "ratio_minimo": RATIO_MIN,
        "lotes": [
            {
                "parcel": c["parcel_id"],
                "county": c["condado"].replace("_", " ").title(),
                "state": c["estado"] or "FL",
                "address": c["address"] or "",
                "city": c["city"] or "",
                "bid": c["min_bid"],
                "just_value": c["just_value"],
                "ratio": round(c["just_value"] / c["min_bid"], 2),
                "sale_date": c["sale_date"],
                "flood_zone": c.get("flood_zone") or "",
                "workers_score": c.get("workers_score"),
                "workers_decision": c.get("workers_decision"),
                "ranking_score": c["ranking_score"],
                "status": "pending",  # pending | queued | analyzing | done
            }
            for c in top
        ],
    }

    out = Path("web/fila-analise.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[fila] Salvo: {out} ({len(top)} lotes)")

    # Envio automatico (se configurado)
    if AUTO_SEND and top and LOTES_TUNNEL_URL:
        lotes_para_lotes = [
            {
                "parcel": c["parcel_id"],
                "county": c["condado"].replace("_", " ").title(),
                "state": c["estado"] or "FL",
                "bid": str(c["min_bid"]),
                "sale_date": c["sale_date"],
            }
            for c in top
        ]
        enviar_fila_lotes(lotes_para_lotes)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[fila] ERRO: {e}", file=sys.stderr)
        sys.exit(1)
