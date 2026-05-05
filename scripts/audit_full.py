"""
audit_full.py — gera web/saude.json com snapshot do estado real do sistema.

Inclui:
- Por condado: cobertura (lots, address, assessed, sqft, sale_date plausivel)
  + quality_score 0-100 + status (verde/amarelo/vermelho)
- Lista de problemas detectados em ordem de gravidade
- Snapshot dos workers (run_logs) — saudaveis vs degraded vs falhando
- Fila LOTES, reports recentes
- Heuristicas de plausibilidade: leiloes diarios = suspeito; 0% sqft = bug

Output: web/saude.json (consumido por web/saude.html)

USO:
  python scripts/audit_full.py
"""
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "taxdeed.db"
DATA_JSON = ROOT / "web" / "data.json"
FILA = ROOT / "web" / "fila-analise.json"
REPORTS = ROOT / "web" / "reports"
OUT = ROOT / "web" / "saude.json"

# Quality scoring weights (somam 100)
W_ADDR = 20      # % lots com address
W_ASSESSED = 15  # % com assessed_value (just_value tambem conta)
W_SQFT = 15      # % com building_sqft (so importa pra propriedades; lotes vagos OK sem)
W_DATES = 25     # leiloes plausiveis (nao diarios)
W_LOTES = 15     # % lots com analise LOTES (verdict real)
W_RECENCY = 10   # ultima atualizacao do scraper recente

# Condados confirmados SEM atividade Q2 2026 (verificado 2026-05-04 via
# browser direto). Nao sao bug — sao legitimos sem leiloes programados.
# Suprime do count de "vermelho" e nao gera issue critico.
SEM_ATIVIDADE_LEGITIMA = {"HERNANDO", "LEE", "LEVY", "ST_LUCIE"}


def main():
    if not DB.exists():
        print(f"[ERRO] DB nao existe: {DB}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    saude = {
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "resumo": {},
        "condados": [],
        "workers": [],
        "problemas": [],
        "fila_status": {},
    }

    # 1. Cobertura + qualidade por condado
    c.execute("""
        SELECT cs.codigo, cs.nome, cs.state, cs.status as cond_status, cs.url_sales,
          (SELECT COUNT(*) FROM sales s WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now')) as sales_fut,
          (SELECT MIN(s.sale_date) FROM sales s WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now')) as proxima_sale,
          (SELECT MAX(s.sale_date) FROM sales s WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now')) as ultima_sale,
          (SELECT COUNT(*) FROM lots l JOIN sales s ON s.id = l.sale_id
            WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now') AND l.parcel_id NOT LIKE 'AID_%') as lots_validos,
          (SELECT COUNT(*) FROM lots l JOIN sales s ON s.id = l.sale_id
            WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now') AND l.parcel_id NOT LIKE 'AID_%' AND l.address IS NOT NULL AND l.address != '') as lots_com_addr,
          (SELECT COUNT(*) FROM lots l JOIN sales s ON s.id = l.sale_id
            WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now') AND l.parcel_id NOT LIKE 'AID_%' AND (l.assessed_value IS NOT NULL OR l.just_value IS NOT NULL)) as lots_com_value,
          (SELECT COUNT(*) FROM lots l JOIN sales s ON s.id = l.sale_id
            WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now') AND l.parcel_id NOT LIKE 'AID_%' AND l.building_sqft IS NOT NULL) as lots_com_sqft,
          -- Classificacao por tipo:
          -- VACANT/LAND/LOT = terra vaga (precisa lot_sqft + zoning + flood)
          -- HOUSE/SINGLE/CONDO/MULTI/MOBILE = improved residencial (precisa building_sqft + year_built)
          -- COMMERCIAL/INDUSTRIAL/RETAIL/OFFICE = improved comercial (precisa building_sqft + zoning C-*)
          -- Daniel busca TODOS os tipos — todos sao oportunidades possiveis.
          (SELECT COUNT(*) FROM lots l JOIN sales s ON s.id = l.sale_id
            WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now') AND l.parcel_id NOT LIKE 'AID_%'
              AND l.property_type IS NOT NULL
              AND (LOWER(l.property_type) LIKE '%vacant%' OR LOWER(l.property_type) LIKE '%land%' OR LOWER(l.property_type) LIKE '%lot%')) as lots_vacant,
          (SELECT COUNT(*) FROM lots l JOIN sales s ON s.id = l.sale_id
            WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now') AND l.parcel_id NOT LIKE 'AID_%'
              AND l.property_type IS NOT NULL
              AND (LOWER(l.property_type) LIKE '%single%' OR LOWER(l.property_type) LIKE '%house%' OR LOWER(l.property_type) LIKE '%condo%'
                   OR LOWER(l.property_type) LIKE '%multi%' OR LOWER(l.property_type) LIKE '%mobile%' OR LOWER(l.property_type) LIKE '%residential improved%')) as lots_residencial,
          (SELECT COUNT(*) FROM lots l JOIN sales s ON s.id = l.sale_id
            WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now') AND l.parcel_id NOT LIKE 'AID_%'
              AND l.property_type IS NOT NULL
              AND (LOWER(l.property_type) LIKE '%commercial%' OR LOWER(l.property_type) LIKE '%retail%'
                   OR LOWER(l.property_type) LIKE '%office%' OR LOWER(l.property_type) LIKE '%industrial%')) as lots_comercial,
          (SELECT COUNT(*) FROM lots l JOIN sales s ON s.id = l.sale_id
            WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now') AND l.parcel_id NOT LIKE 'AID_%'
              AND l.property_type IS NULL) as lots_tipo_desconhecido,
          (SELECT COUNT(*) FROM lots l JOIN sales s ON s.id = l.sale_id
            WHERE s.county_id = cs.id AND DATE(s.sale_date) >= DATE('now') AND l.parcel_id LIKE 'AID_%') as lots_aid,
          (SELECT MAX(l.scraped_at) FROM lots l JOIN sales s ON s.id = l.sale_id WHERE s.county_id = cs.id) as ultimo_scrape
        FROM counties cs ORDER BY cs.codigo
    """)

    counties = list(c.fetchall())

    # Carrega data.json pra cross-check decisoes + reports webhook recebidos
    decisoes_por_cond = defaultdict(lambda: Counter())
    if DATA_JSON.exists():
        with open(DATA_JSON, encoding='utf-8') as f:
            data_json = json.load(f)
        for l in data_json.get('lots', []):
            decisoes_por_cond[l.get('condado')][l.get('decisao')] += 1

    # Reports webhook (verdict real) + lista detalhada pra UI
    parcels_com_verdict = set()
    analises_recentes = []
    if REPORTS.exists():
        for f in REPORTS.glob('*.json'):
            try:
                d = json.loads(f.read_text(encoding='utf-8'))
                v = d.get('verdict') or d.get('final_verdict')
                if v:
                    parcel = d.get('parcel_id') or f.stem
                    parcels_com_verdict.add(parcel)
                    analises_recentes.append({
                        "parcel": parcel,
                        "verdict": v,
                        "score": d.get('final_score') or d.get('score'),
                        "completed_at": d.get('completed_at'),
                        "max_bid": (d.get('recommendation') or {}).get('max_bid') or d.get('max_bid_recommended'),
                        "red_flags_count": len(d.get('red_flags', []) or []),
                    })
            except Exception:
                pass
    # Ordena por completed_at DESC (mais recente primeiro)
    analises_recentes.sort(key=lambda x: x.get('completed_at') or '', reverse=True)
    saude["analises_lotes"] = analises_recentes[:30]

    total_lots_valid = 0
    total_problems_critical = 0

    for row in counties:
        cod = row['codigo']
        sales_fut = row['sales_fut'] or 0
        lots_v = row['lots_validos'] or 0
        lots_addr = row['lots_com_addr'] or 0
        lots_value = row['lots_com_value'] or 0
        lots_sqft = row['lots_com_sqft'] or 0
        lots_vacant = row['lots_vacant'] or 0
        lots_residencial = row['lots_residencial'] or 0
        lots_comercial = row['lots_comercial'] or 0
        lots_tipo_unk = row['lots_tipo_desconhecido'] or 0
        lots_improved = lots_residencial + lots_comercial  # precisam sqft
        lots_aid = row['lots_aid'] or 0

        # Plausibilidade de datas — leiloes diarios = suspeito
        dates_suspeitas = False
        date_warning = ""
        if sales_fut >= 4 and row['proxima_sale'] and row['ultima_sale']:
            try:
                d1 = datetime.fromisoformat(row['proxima_sale']).date()
                d2 = datetime.fromisoformat(row['ultima_sale']).date()
                dias_intervalo = (d2 - d1).days
                # Se ratio sales/dias > 0.5 e' suspeito (>1 leilao por 2 dias)
                if dias_intervalo > 0 and (sales_fut / dias_intervalo) > 0.5:
                    dates_suspeitas = True
                    date_warning = f"{sales_fut} leiloes em {dias_intervalo} dias (densidade {sales_fut/dias_intervalo:.2f}/dia — provavel duplicacao)"
            except Exception:
                pass

        # Quality score 0-100
        addr_pct = (lots_addr / lots_v) if lots_v else 0
        value_pct = (lots_value / lots_v) if lots_v else 0
        # sqft_pct considera SO improved (terras vagas legitimamente sem sqft).
        # Se condado nao tem improved, sqft nao pesa (full score).
        if lots_improved > 0:
            sqft_pct = lots_improved_sqft / lots_improved
        else:
            sqft_pct = 1.0  # sem improved = sqft N/A = nao penaliza
        date_score = 0 if dates_suspeitas else 1
        # Pra LOTES: contar lotes do condado com verdict real
        cond_lots_in_data = sum(decisoes_por_cond[cod].values()) if cod in decisoes_por_cond else 0
        lotes_score = 1 if cond_lots_in_data > 0 else 0
        # Recency
        recency_score = 0
        if row['ultimo_scrape']:
            try:
                ts = datetime.fromisoformat(row['ultimo_scrape'].replace('Z', ''))
                if datetime.now() - ts < timedelta(days=2):
                    recency_score = 1
            except Exception:
                pass

        if lots_v == 0:
            quality = 0
        else:
            quality = int(
                W_ADDR * addr_pct
                + W_ASSESSED * value_pct
                + W_SQFT * sqft_pct
                + W_DATES * date_score
                + W_LOTES * lotes_score
                + W_RECENCY * recency_score
            )

        # Status visual
        sem_atividade = cod in SEM_ATIVIDADE_LEGITIMA
        if sales_fut == 0:
            status = "sem_atividade" if sem_atividade else "vazio"
        elif quality >= 70:
            status = "verde"
        elif quality >= 40:
            status = "amarelo"
        else:
            status = "vermelho"

        # Issues do condado
        issues = []
        if sales_fut == 0 and not sem_atividade:
            issues.append("Sem leiloes futuros (provavel: scraper falhou OU condado sem atividade Q2)")
        if dates_suspeitas:
            issues.append(date_warning)
            total_problems_critical += 1
        if lots_v > 0 and addr_pct < 0.5:
            issues.append(f"address missing em {lots_v - lots_addr}/{lots_v} ({(1-addr_pct)*100:.0f}%) — scraper de lot incompleto")
        if lots_v > 0 and value_pct < 0.5:
            issues.append(f"assessed/just_value missing em {lots_v - lots_value}/{lots_v} — Regrid/PA falhou")
        # 0% sqft eh agregado num issue global — nao polui issue por condado
        if lots_aid > 0:
            issues.append(f"{lots_aid} placeholders AID_* (lot_scraper sem parcel_id real)")

        total_lots_valid += lots_v

        saude["condados"].append({
            "codigo": cod,
            "nome": row['nome'],
            "state": row['state'],
            "url_sales": row['url_sales'],
            "sales_futuras": sales_fut,
            "proxima_sale": row['proxima_sale'],
            "ultima_sale": row['ultima_sale'],
            "lots_validos": lots_v,
            "cobertura": {
                "address_pct": round(addr_pct * 100, 1),
                "value_pct": round(value_pct * 100, 1),
                "sqft_pct": round(sqft_pct * 100, 1),
            },
            "decisoes": dict(decisoes_por_cond[cod]) if cod in decisoes_por_cond else {},
            "quality_score": quality,
            "status": status,
            "issues": issues,
        })

    # 2. Workers (run_logs) — ultimas 24h
    c.execute("PRAGMA table_info(run_logs)")
    log_cols = [r[1] for r in c.fetchall()]

    c.execute("""
        SELECT worker, status, items_processed, errors_count, started_at, finished_at, log_text
        FROM run_logs
        WHERE started_at >= datetime('now', '-1 day')
        ORDER BY id DESC
    """)
    workers_recent = []
    workers_seen = set()
    for r in c.fetchall():
        if r['worker'] in workers_seen:
            continue
        workers_seen.add(r['worker'])
        w_status = r['status']
        # Re-classifica como degraded se items=0 e nao for legitimo
        if w_status == "success" and (r['items_processed'] or 0) == 0 and (r['errors_count'] or 0) > 0:
            w_status = "degraded"
        workers_recent.append({
            "worker": r['worker'],
            "status": w_status,
            "items": r['items_processed'] or 0,
            "errors": r['errors_count'] or 0,
            "ran_at": r['started_at'],
            "log": (r['log_text'] or "")[:300],
        })
    saude["workers"] = workers_recent

    # 3. Problemas globais detectados
    if any(w["status"] in ("degraded", "failed") for w in workers_recent):
        for w in workers_recent:
            if w["status"] in ("degraded", "failed"):
                saude["problemas"].append({
                    "severidade": "ALTA",
                    "categoria": "worker",
                    "msg": f"Worker {w['worker']} com status {w['status']} ({w['items']} items, {w['errors']} erros) — {w['log'][:150] or 'sem log'}",
                })

    # PA SPA Playwright (substitui PA legacy regex). So alarma se nao processou.
    pa_sp = next((w for w in workers_recent if w["worker"] == "pa_playwright_spa"), None)
    if pa_sp and pa_sp["items"] < 5:
        saude["problemas"].append({
            "severidade": "ALTA",
            "categoria": "enrichment",
            "msg": f"PA Playwright SPA processou so {pa_sp['items']} lots/run — patterns nao casam pra maioria dos condados",
        })

    # Regrid quebrado
    rg = next((w for w in workers_recent if w["worker"] == "regrid_enricher"), None)
    if rg and rg["items"] == 0:
        saude["problemas"].append({
            "severidade": "ALTA",
            "categoria": "enrichment",
            "msg": "Regrid enricher items=0 (verificar REGRID_API_KEY no GitHub Secrets)",
        })

    # FEMA quebrado
    fc = next((w for w in workers_recent if w["worker"] == "fema_checker"), None)
    if fc and fc["errors"] > 100:
        saude["problemas"].append({
            "severidade": "MEDIA",
            "categoria": "enrichment",
            "msg": f"FEMA checker {fc['errors']} erros — depende de address (cascata do PA enricher)",
        })

    # Por condado: agrupados (nao gera issue duplicado por condado).
    # SQFT zero so' eh problema em condados COM lots improved (casas/condos).
    # Terras vagas (vacant) sem sqft eh esperado.
    condados_sqft_problema = []
    for c in saude["condados"]:
        if c["lots_validos"] > 0 and c.get("lots_improved", 0) > 0 and c["cobertura"]["sqft_pct"] < 30:
            condados_sqft_problema.append(c["codigo"])
    if condados_sqft_problema and len(condados_sqft_problema) > 2:
        saude["problemas"].append({
            "severidade": "ALTA",
            "categoria": "enrichment",
            "msg": f"PA enricher: {len(condados_sqft_problema)} condados COM lots improved (casas/condos) sem building_sqft "
                   f"({', '.join(condados_sqft_problema[:6])}) — patterns regex Playwright nao casaram",
        })

    for c_info in saude["condados"]:
        for issue in c_info["issues"]:
            sev = "CRITICA" if "duplicacao" in issue else "MEDIA"
            saude["problemas"].append({
                "severidade": sev,
                "categoria": "condado",
                "condado": c_info["codigo"],
                "msg": f"{c_info['codigo']}: {issue}",
            })

    # Ordena problemas: CRITICA -> ALTA -> MEDIA -> BAIXA
    sev_order = {"CRITICA": 0, "ALTA": 1, "MEDIA": 2, "BAIXA": 3}
    saude["problemas"].sort(key=lambda p: sev_order.get(p["severidade"], 9))

    # 4. Resumo
    n_verde = sum(1 for c in saude["condados"] if c["status"] == "verde")
    n_amarelo = sum(1 for c in saude["condados"] if c["status"] == "amarelo")
    n_vermelho = sum(1 for c in saude["condados"] if c["status"] == "vermelho")
    n_vazio = sum(1 for c in saude["condados"] if c["status"] == "vazio")
    n_sem_atividade = sum(1 for c in saude["condados"] if c["status"] == "sem_atividade")

    # Saude geral exclui sem_atividade (legitimo) do denominador
    n_ativos = len(saude["condados"]) - n_vazio - n_sem_atividade
    saude["resumo"] = {
        "total_condados": len(saude["condados"]),
        "condados_verdes": n_verde,
        "condados_amarelos": n_amarelo,
        "condados_vermelhos": n_vermelho,
        "condados_vazios": n_vazio,
        "condados_sem_atividade": n_sem_atividade,
        "total_lots_validos": total_lots_valid,
        "lots_com_verdict_lotes": len(parcels_com_verdict),
        "problemas_criticos": sum(1 for p in saude["problemas"] if p["severidade"] in ("CRITICA", "ALTA")),
        "saude_geral_pct": int(100 * n_verde / max(n_ativos, 1)),
    }

    # 5. Fila status
    if FILA.exists():
        try:
            with open(FILA, encoding='utf-8') as f:
                fila = json.load(f)
            saude["fila_status"] = {
                "candidatos": len(fila.get("lotes", [])),
                "ratio_minimo": fila.get("ratio_minimo"),
                "top_per_auction": fila.get("top_per_auction"),
                "gerado_em": fila.get("gerado_em"),
            }
        except Exception:
            pass

    # 6. TODO epicos — backlog visivel de melhorias estruturais que requerem
    # trabalho substancial (1+ dia cada). Mostra na UI pra Daniel saber o que
    # falta sem precisar perguntar.
    saude["todos_epicos"] = [
        {
            "titulo": "PA enricher → Playwright migration",
            "impacto": "Desbloqueia building_sqft/year_built/property_type pra 14 condados nao-SPA",
            "esforco": "1-2 dias",
            "afeta": "Resolve 10 issues criticos (1 raiz + 9 cascatas) + libera scoring real",
        },
        {
            "titulo": "Regrid token — verificar config",
            "impacto": "Desbloqueia just_value/lot_sqft em Lake/Polk/Putnam",
            "esforco": "5min (Daniel) — rodar scripts/test_regrid.py",
            "afeta": "Resolve 4 issues medios (Regrid items=0 + 3 condados sem assessed)",
        },
        {
            "titulo": "GIS REST publico (ArcGIS) por condado — fallback Regrid",
            "impacto": "Cobertura redundante quando Regrid falha 404",
            "esforco": "1 dia (1 condado piloto Marion ou Polk) + replicar",
            "afeta": "Eleva quality_score em vermelhos sem custo recurring",
        },
        {
            "titulo": "Calendar scraper — investigar root cause das datas duplicadas",
            "impacto": "Hoje dedup_sales remove 33+ sales fakes/run, mas sintoma — fix no scraper",
            "esforco": "0.5 dia",
            "afeta": "Reduz overhead + aumenta confianca",
        },
        {
            "titulo": "Auto-restart server LOTES (PM2 ou Windows Service)",
            "impacto": "24/7 uptime garantido — hoje server pode cair sem ninguem perceber",
            "esforco": "2-3h",
            "afeta": "Mandato Daniel: nunca parar",
        },
    ]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(saude, indent=2, ensure_ascii=False), encoding='utf-8')
    print(f"[saude] Gravado: {OUT}")
    print(f"[saude] {n_verde} verdes / {n_amarelo} amarelos / {n_vermelho} vermelhos / {n_vazio} vazios")
    print(f"[saude] {len(saude['problemas'])} problemas detectados ({saude['resumo']['problemas_criticos']} criticos/altos)")


if __name__ == "__main__":
    main()
