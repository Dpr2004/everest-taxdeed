"""Gera web/fila-analise.json com candidatos top ranqueados pra
analise profunda no LOTES Analyzer.

Filtros rigorosos (zero simulacao, so dados reais do DB):
- Ratio just_value / min_bid >= 2.0 (max_bid <= 50% market value, mandato Daniel)
- min_bid > 0 (descartar lotes sem bid definido)
- just_value > 0 (descartar lotes sem avaliacao)
- FEMA X, AE, ou desconhecido (NAO V/VE zona catastrofica)
- sale_date futuro (nao analisar o que ja passou)

Ranking (Fase 6 — auto-prospecting):
- Score = (just_value / min_bid) * weight_fema * weight_condado
- Top N **por (condado, data_leilao)** — default 10 (1 leilao = 10 melhores)
- Pra cada proximo leilao de cada condado, isola as 10 melhores oportunidades

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
# RATIO_MIN = 2.0 -> opening_bid <= 50% just_value (mandato Daniel: max bid <= 50% market)
# Pode ser overridden via env var pra ser mais conservador (3.0 = 33%)
RATIO_MIN = float(os.environ.get("FILA_RATIO_MIN", "2.0"))
FEMA_BANIDOS = {"V", "VE"}  # zonas catastroficas de seguro caro/impossivel

# Fase 6: Top N **por leilao** (county + sale_date), nao top global
TOP_PER_AUCTION = int(os.environ.get("FILA_TOP_PER_AUCTION", "10"))

# Cap global de seguranca (caso muitos condados tenham leiloes simultaneos)
# Default 110 = 11 condados Tier Everest x 10 lotes
# Aceita ambos nomes (FILA_TOP_N e FILA_TOP_N_GLOBAL) por historico —
# workflow define apenas FILA_TOP_N, antes ignorado por mismatch de nome.
TOP_N_GLOBAL = int(
    os.environ.get("FILA_TOP_N")
    or os.environ.get("FILA_TOP_N_GLOBAL")
    or "110"
)

# Integracao LOTES Analyzer
LOTES_TUNNEL_URL = os.environ.get("LOTES_TUNNEL_URL", "").rstrip("/")
LOTES_API_KEY = os.environ.get("LOTES_API_KEY", "")
AUTO_SEND = os.environ.get("FILA_AUTO_SEND", "false").lower() == "true"

# Pesos por condado (1.0 = neutro). Ajuste manual baseado em experiencia.
COUNTY_WEIGHTS = {
    # === Tier Everest original (11) ===
    "POLK": 1.0,
    "MARION": 1.0,
    "HIGHLANDS": 1.1,    # GDC lots tem upside
    "LAKE": 1.0,
    "ORANGE": 0.9,       # mercado competitivo, menos margem
    "OSCEOLA": 1.0,
    "PUTNAM": 1.1,       # rural, menor competicao
    "ST_LUCIE": 1.1,     # GDC Port St Lucie
    "LEE": 0.85,         # pos-Ian risco alto
    "BREVARD": 1.0,
    "CITRUS": 1.05,
    # === Expansao Centro/Costa Atlantica + Norte (8) — ajustar pesos com dados reais ===
    "HILLSBOROUGH": 0.85, # Tampa, mercado super competitivo
    "PASCO": 1.05,        # Wesley Chapel/Dade City — crescimento + margem
    "HERNANDO": 1.05,     # rural-suburb, menor competicao
    "VOLUSIA": 0.95,      # Daytona — competicao media
    "FLAGLER": 1.0,
    "ALACHUA": 1.0,       # Gainesville — universidade, demanda estavel
    "DUVAL": 0.9,         # Jacksonville — alto volume mas concorrido
    "LEVY": 1.1,          # rural, baixa competicao
}


def score_lote(row):
    # Fallback assessed_value quando just_value vazio.
    # Regrid as vezes retorna so assessval (assessed) sem parval (just).
    # Em FL, assessed <= just (cap homestead/SOH), entao mais conservador.
    jv = row["just_value"] or row["assessed_value"] or 0
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
                l.min_bid, l.just_value, l.assessed_value,
                s.sale_date, c.codigo as condado, c.state as estado,
                d.fema_flood_zone as flood_zone,
                sc.final_score as workers_score, sc.decision as workers_decision
            FROM lots l
            JOIN sales s ON s.id = l.sale_id
            JOIN counties c ON c.id = s.county_id
            LEFT JOIN dd d ON d.lot_id = l.id
            LEFT JOIN scores sc ON sc.lot_id = l.id
            WHERE s.sale_date >= DATE('now')
              AND l.parcel_id NOT LIKE 'AID_%'
              AND l.min_bid > 0
              AND (l.just_value > 0 OR l.assessed_value > 0)
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

    # Filtra + scora
    ranqueados = []
    for c in candidatos:
        if ja_analisado(c["parcel_id"], analisados):
            continue
        s = score_lote(c)
        if s > 0:
            c["ranking_score"] = round(s, 2)
            ranqueados.append(c)

    print(f"[fila] Apos filtros (ratio>={RATIO_MIN}, FEMA, etc): {len(ranqueados)}")

    # Fase 6: Group by (condado, sale_date), top N por grupo
    grupos = {}
    for c in ranqueados:
        key = (c["condado"], c["sale_date"])
        grupos.setdefault(key, []).append(c)

    top = []
    for (condado, sale_date), lotes in grupos.items():
        lotes.sort(key=lambda x: x["ranking_score"], reverse=True)
        top_grupo = lotes[:TOP_PER_AUCTION]
        print(f"[fila]   {condado} {sale_date}: {len(lotes)} candidatos -> top {len(top_grupo)}")
        top.extend(top_grupo)

    # Re-sort global: SEMPRE prioridade leiloes mais proximos primeiro
    # (regra Daniel 2026-05-03). Dentro do mesmo dia, melhor ranking_score primeiro.
    # Override por condado: se FILA_COUNTY_FIRST=POLK,etc setado, esses condados
    # vao no topo da fila independente da data.
    county_priority = [c.strip().upper() for c in os.environ.get("FILA_COUNTY_FIRST", "").split(",") if c.strip()]
    def _sort_key(lot):
        cond_priority = 0 if lot["condado"].upper() in county_priority else 1
        return (cond_priority, lot["sale_date"], -lot["ranking_score"])
    top.sort(key=_sort_key)

    # Cap global de seguranca
    if len(top) > TOP_N_GLOBAL:
        print(f"[fila] Cap global aplicado: {len(top)} -> {TOP_N_GLOBAL}")
        top = top[:TOP_N_GLOBAL]

    print(f"[fila] Total final na fila: {len(top)} lotes ({len(grupos)} leiloes distintos)")

    # Formato publico (dashboard consome)
    payload = {
        "gerado_em": date.today().isoformat(),
        "total_candidatos": len(top),
        "total_leiloes": len(grupos),
        "ratio_minimo": RATIO_MIN,
        "top_per_auction": TOP_PER_AUCTION,
        "max_bid_pct_market": "<= 50% just_value (ratio >= 2.0)",
        "lotes": [
            {
                "parcel": c["parcel_id"],
                "county": c["condado"].replace("_", " ").title(),
                "state": c["estado"] or "FL",
                "address": c["address"] or "",
                "city": c["city"] or "",
                "bid": c["min_bid"],
                "just_value": c["just_value"] or c["assessed_value"],
                "ratio": round((c["just_value"] or c["assessed_value"] or 0) / c["min_bid"], 2),
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
