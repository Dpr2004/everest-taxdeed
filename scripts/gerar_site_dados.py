"""Gera web/data.json a partir do DB SQLite para consumo pelo site estatico."""
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.db.connection import cursor

OUT_DIR = Path(__file__).parent.parent / "web"
OUT_FILE = OUT_DIR / "data.json"


def cadencia_texto(tipo, dia_semana, ordem):
    dias = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]
    if tipo == "WEEKLY" and dia_semana is not None:
        return f"Toda {dias[dia_semana].lower()}"
    if tipo == "ORDINAL" and ordem and dia_semana is not None:
        ord_txt = {1: "1a", 2: "2a", 3: "3a", 4: "4a"}.get(ordem, f"{ordem}a")
        return f"{ord_txt} {dias[dia_semana].lower()} do mes"
    return "A verificar no site do clerk"


def main():
    hoje = date.today()
    inicio = hoje - timedelta(days=hoje.weekday())
    fim = inicio + timedelta(weeks=8) - timedelta(days=1)
    dias_pt = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]

    payload = {
        "gerado_em": datetime.now().strftime("%d/%b/%Y %H:%M"),
        "resumo": {},
        "semanas": [],
        "lots": [],
        "condados": [],
    }

    with cursor() as cur:
        # ----- Condados -----
        cur.execute("""
            SELECT c.*,
                (SELECT COUNT(*) FROM sales s WHERE s.county_id = c.id AND s.sale_date >= DATE('now')) AS total_sales,
                (SELECT COUNT(*) FROM lots l JOIN sales s ON s.id = l.sale_id WHERE s.county_id = c.id AND l.parcel_id NOT LIKE 'AID_%') AS total_lots
            FROM counties c
            WHERE c.ativo = 1
            ORDER BY c.state, c.codigo
        """)
        for c in cur.fetchall():
            payload["condados"].append({
                "codigo": c["codigo"],
                "estado": c["state"],
                "nome": c["nome"],
                "plataforma": c["plataforma"],
                "url_sales": c["url_sales"],
                "url_clerk": c["url_clerk"],
                "url_property_appraiser": c["url_property_appraiser"],
                "telefone": c["telefone"],
                "deposito": c["deposito"],
                "status": c["status"],
                "horario_et": c["horario_et"] or "Verificar",
                "cadencia_texto": cadencia_texto(c["cadencia_tipo"],
                                                  c["cadencia_dia_semana"],
                                                  c["cadencia_ordem"]),
                "total_sales": c["total_sales"],
                "total_lots": c["total_lots"],
            })

        # ----- Semanas -----
        for i in range(8):
            ini = inicio + timedelta(weeks=i)
            fim_sem = ini + timedelta(days=6)
            label = f"{ini.strftime('%d/%b')} a {fim_sem.strftime('%d/%b/%Y')}"
            cur.execute("""
                SELECT s.id, s.sale_date, s.sale_time, s.total_lots,
                       c.codigo, c.state, c.plataforma, c.url_sales, c.url_clerk, c.status
                FROM sales s JOIN counties c ON c.id = s.county_id
                WHERE s.sale_date BETWEEN ? AND ?
                  AND s.sale_date >= DATE('now')
                ORDER BY s.sale_date, c.codigo
            """, (ini.isoformat(), fim_sem.isoformat()))
            sales = []
            for s in cur.fetchall():
                d = datetime.strptime(s["sale_date"], "%Y-%m-%d").date()
                sales.append({
                    "id": s["id"],
                    "data_fmt": d.strftime("%d/%b/%Y"),
                    "dia_semana": dias_pt[d.weekday()],
                    "condado": s["codigo"],
                    "estado": s["state"],
                    "horario": s["sale_time"] or "—",
                    "plataforma": s["plataforma"],
                    "url_sales": s["url_sales"],
                    "url_clerk": s["url_clerk"],
                    "status": s["status"] or "A_VERIFICAR",
                    "total_lots": s["total_lots"] or 0,
                })
            payload["semanas"].append({"label": label, "sales": sales})

        # ----- Lotes (com scoring + FEMA se disponivel) -----
        cur.execute("""
            SELECT l.*, c.codigo AS condado, c.state AS estado, s.sale_date,
                   sc.max_bid_recommended, sc.projected_profit, sc.projected_roi,
                   sc.final_score, sc.decision,
                   dd.fema_flood_zone, dd.fema_risk
            FROM lots l
            JOIN sales s ON s.id = l.sale_id
            JOIN counties c ON c.id = s.county_id
            LEFT JOIN scores sc ON sc.lot_id = l.id
            LEFT JOIN dd ON dd.lot_id = l.id
            WHERE s.sale_date >= DATE('now')
              AND l.parcel_id NOT LIKE 'AID_%'
              AND (l.min_bid > 0 OR l.address IS NOT NULL)
            ORDER BY COALESCE(sc.final_score, 0) DESC, l.min_bid ASC
            LIMIT 500
        """)
        for l in cur.fetchall():
            payload["lots"].append({
                "id": l["id"],
                "condado": l["condado"],
                "estado": l["estado"],
                "sale_date": l["sale_date"],
                "parcel_id": l["parcel_id"],
                "address": l["address"],
                "city": l["city"],
                "zip": l["zip"],
                "min_bid": l["min_bid"],
                "assessed_value": l["assessed_value"],
                "just_value": l["just_value"],
                "building_sqft": l["building_sqft"],
                "year_built": l["year_built"],
                "score": l["final_score"],
                "roi": l["projected_roi"],
                "decisao": l["decision"],
                "max_bid": l["max_bid_recommended"],
                "profit": l["projected_profit"],
                "fema_zone": l["fema_flood_zone"],
                "fema_risk": l["fema_risk"],
            })

        # ----- Resumo -----
        estados = sorted({c["estado"] for c in payload["condados"]})
        payload["resumo"] = {
            "total_condados": len(payload["condados"]),
            "total_sales": sum(len(w["sales"]) for w in payload["semanas"]),
            "total_lots": len(payload["lots"]),
            "lances": sum(1 for l in payload["lots"] if l["decisao"] == "LANCE"),
            "estados": estados,
        }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False, default=str)

    print(f"OK: {OUT_FILE}")
    print(f"  - {payload['resumo']['total_condados']} condados")
    print(f"  - {payload['resumo']['total_sales']} sales")
    print(f"  - {payload['resumo']['total_lots']} lotes")
    print(f"  - {payload['resumo']['lances']} LANCE")


if __name__ == "__main__":
    main()
