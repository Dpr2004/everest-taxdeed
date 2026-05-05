"""
dedup_sales.py — deduplica sales fictícias do calendar_scraper.

PROBLEMA: calendar_scraper estava criando 1 sale por dia em condados como
Pasco (SEG, TER, QUA, QUI, SEX consecutivos) — impossivel em tax deed real,
que e' SEMANAL (1x/semana) ou MENSAL.

CAUSA: scraper navega "Next Auction" e cada pagina retorna data ligeiramente
diferente, criando entradas falsas.

FIX defensivo: regra de negocio aplicada pos-scrape — em UMA semana
calendario, condado tem NO MAXIMO 1 sale. Se houver mais, mantem so a com
MAIS lots (mais provavelmente a real). Restantes sao deletadas COM seus
lots associados (que tambem sao falsos — copia da mesma sale).

Roda apos calendar_scraper + lot_list_scraper, antes de gerar_fila/data.json.
"""
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "data" / "taxdeed.db"


def main(dry_run=False):
    if not DB.exists():
        print(f"[ERRO] DB nao existe: {DB}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Lista todas as sales futuras + count de lots + count de lots ja
    # analizados pelo LOTES (lots que tem score com decision != PASSA).
    # Sales com analise LOTES sao PROTEGIDAS — nunca deletar.
    c.execute("""
        SELECT s.id, s.county_id, s.sale_date, cs.codigo,
               (SELECT COUNT(*) FROM lots l WHERE l.sale_id = s.id) as lot_count,
               (SELECT COUNT(*) FROM lots l JOIN scores sc ON sc.lot_id = l.id
                WHERE l.sale_id = s.id AND sc.final_score IS NOT NULL) as scored_count
        FROM sales s JOIN counties cs ON cs.id = s.county_id
        WHERE DATE(s.sale_date) >= DATE('now')
        ORDER BY cs.codigo, s.sale_date
    """)
    sales = list(c.fetchall())

    # Agrupa por (codigo, ano-semana). Mantem so a com mais lots.
    grupos = defaultdict(list)
    for s in sales:
        try:
            d = datetime.fromisoformat(s["sale_date"]).date()
        except ValueError:
            continue
        # ISO week: ano-semana
        year, week, _ = d.isocalendar()
        key = (s["codigo"], year, week)
        grupos[key].append(s)

    a_remover = []
    a_manter = []
    for key, lst in grupos.items():
        if len(lst) <= 1:
            continue
        # Ordena: scored_count DESC (analise LOTES e' PROTEGIDA),
        #         lot_count DESC (mais lots),
        #         sale_date ASC (mais cedo). Keeper e' o melhor.
        lst.sort(key=lambda x: (-x["scored_count"], -x["lot_count"], x["sale_date"]))
        keeper = lst[0]
        a_manter.append(keeper)
        for s in lst[1:]:
            # NUNCA remove sale com lots ja analisados pelo LOTES — pode estar
            # em uso na fila ou no historico do dashboard. Vai pra "outros mantidos".
            if s["scored_count"] > 0:
                a_manter.append(s)
            else:
                a_remover.append(s)

    if not a_remover:
        print("[dedup] Nenhuma duplicacao encontrada.")
        return

    print(f"[dedup] Detectadas {len(a_remover)} sales duplicadas em "
          f"{len(set((s['codigo'], datetime.fromisoformat(s['sale_date']).isocalendar()[1]) for s in a_remover))} grupos.")
    print()

    # Mostra detalhes agrupados
    por_cond = defaultdict(list)
    for s in a_remover:
        por_cond[s["codigo"]].append(s)
    for cond, lst in sorted(por_cond.items()):
        print(f"  {cond}: removendo {len(lst)} sales:")
        for s in lst[:5]:
            print(f"    - sale_date={s['sale_date']} ({s['lot_count']} lots) [id={s['id']}]")
        if len(lst) > 5:
            print(f"    ... +{len(lst)-5} mais")

    if dry_run:
        print("\n[dedup] DRY-RUN — nada foi modificado.")
        return

    # Apaga lots associados primeiro (FK), depois sales
    sale_ids = [s["id"] for s in a_remover]
    placeholders = ",".join("?" for _ in sale_ids)

    # Tambem limpa scores e dd associados
    c.execute(f"DELETE FROM scores WHERE lot_id IN (SELECT id FROM lots WHERE sale_id IN ({placeholders}))", sale_ids)
    sc_del = c.rowcount
    c.execute(f"DELETE FROM dd WHERE lot_id IN (SELECT id FROM lots WHERE sale_id IN ({placeholders}))", sale_ids)
    dd_del = c.rowcount
    c.execute(f"DELETE FROM lots WHERE sale_id IN ({placeholders})", sale_ids)
    lot_del = c.rowcount
    c.execute(f"DELETE FROM sales WHERE id IN ({placeholders})", sale_ids)
    sale_del = c.rowcount
    conn.commit()

    print(f"\n[dedup] Removidos: {sale_del} sales + {lot_del} lots + {sc_del} scores + {dd_del} dd")
    print(f"[dedup] Mantidas {len(a_manter)} sales (1 por semana por condado, com mais lots).")


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    main(dry_run=dry)
