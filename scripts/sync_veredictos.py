"""
sync_veredictos.py — sincroniza veredicto real dos 12 agentes LOTES com data.json.

PROBLEMA QUE RESOLVE:
- data.json (Tier 1) calcula `decisao` por heuristica simples (score+ROI)
- LOTES (Tier 2, 12 agentes) calcula `verdict` por comite real com mandato 50% VMF,
  dealbreakers, red flags
- Os dois NUNCA se sincronizavam → listagem mostrava LANCE em deals que o comite
  ja havia reprovado (PASS), violando mandato de proteger capital

INPUTS:
- data.json (web/data.json) — fila atual com decisoes heuristicas
- LOTES_{parcel}_06-qa-investment-committee.json — veredicto real do comite
- ENTREGA-FINAL_{parcel}.md — pra detectar reports PARCIAIS (0/11 agentes)

OUTPUT:
- data.json com `decisao` REAL + `decisao_origem` (heuristica vs comite vs parcial)
- Relatorio stdout com mudancas

MAPEAMENTO verdict → decisao:
  BUY                → LANCE
  CONDITIONAL_BUY    → REVISAR
  INVESTIGATE_MORE   → REVISAR
  PASS               → PASSA
  INCONCLUSIVO/parcial (< 6 agentes ok) → REVISAR  (conservador: nunca LANCE
                                                    em algo nao verificado)

USO:
  python scripts/sync_veredictos.py [--reports-dir PATH] [--data-json PATH] [--dry-run]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Paths default — podem ser overrideados via CLI
# Default: usa web/reports/ (formato webhook, funciona em CI). Local override possivel.
DEFAULT_REPORTS_DIR = Path(__file__).resolve().parent.parent / "web" / "reports"
LOCAL_OUTPUT_DIR = Path(r"C:\Users\dpr20\iCloudDrive\lotes-analyzer\output")
DEFAULT_DATA_JSON = Path(__file__).resolve().parent.parent / "web" / "data.json"

VERDICT_TO_DECISAO = {
    "BUY": "LANCE",
    "COMPRAR": "LANCE",
    "CONDITIONAL_BUY": "REVISAR",
    "CONDITIONAL BUY": "REVISAR",
    "INVESTIGATE_MORE": "REVISAR",
    "INVESTIGATE MORE": "REVISAR",
    "INVESTIGAR": "REVISAR",
    "INVESTIGAR MAIS": "REVISAR",
    "PASS": "PASSA",
    "PASSAR": "PASSA",
    # parciais/inconclusivos: trata em codigo (default REVISAR)
}

# Regex pra detectar reports parciais
RE_PARCIAL = re.compile(r"(\d+)\s*de\s*(\d+)\s*agentes")


def load_verdict_for_parcel(reports_dir: Path, parcel_id: str):
    """Retorna dict com {verdict, score, n_ok, n_total, source} ou None.

    Suporta DOIS formatos:
    1. LOCAL (output/): LOTES_{parcel}_06-qa-investment-committee.json + ENTREGA-FINAL_{parcel}.md
    2. WEBHOOK (web/reports/): {parcel}.json (payload com verdict, report_markdown)
    """
    # Sanitiza igual o pipeline faz: remove tudo que nao for [a-zA-Z0-9-]
    parcel_clean = re.sub(r"[^a-zA-Z0-9\-]", "", parcel_id)

    qa_file = reports_dir / f"LOTES_{parcel_clean}_06-qa-investment-committee.json"
    entrega_file = reports_dir / f"ENTREGA-FINAL_{parcel_clean}.md"
    webhook_file = reports_dir / f"{parcel_clean}.json"
    # Webhook tambem pode ter parcel sem sanitizacao
    webhook_file_raw = reports_dir / f"{parcel_id}.json"

    verdict = None
    score = None
    md_text = None
    has_qa = False
    has_entrega = False

    # 1. Formato LOCAL: agente 06 + ENTREGA
    if qa_file.exists():
        has_qa = True
        try:
            with open(qa_file, encoding="utf-8") as f:
                a06 = json.load(f)
            verdict = a06.get("verdict") or a06.get("final_verdict")
            score = a06.get("final_score") or a06.get("score")
            if a06.get("recommendation") and isinstance(a06["recommendation"], dict):
                verdict = verdict or a06["recommendation"].get("verdict")
        except Exception as e:
            print(f"  [WARN] erro lendo {qa_file.name}: {e}", file=sys.stderr)

    if entrega_file.exists():
        has_entrega = True
        try:
            md_text = entrega_file.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass

    # 2. Formato WEBHOOK: payload completo em {parcel}.json
    wh_file = webhook_file if webhook_file.exists() else (webhook_file_raw if webhook_file_raw.exists() else None)
    if wh_file:
        has_entrega = True  # payload conta como entrega
        try:
            with open(wh_file, encoding="utf-8") as f:
                wh = json.load(f)
            if not verdict:
                verdict = wh.get("verdict") or wh.get("final_verdict")
                if wh.get("recommendation") and isinstance(wh["recommendation"], dict):
                    verdict = verdict or wh["recommendation"].get("verdict")
            if not score:
                score = wh.get("final_score") or wh.get("score")
            if not md_text:
                md_text = wh.get("report_markdown") or ""
        except Exception as e:
            print(f"  [WARN] erro lendo {wh_file.name}: {e}", file=sys.stderr)

    # Detecta report PARCIAL pelo texto md (se houver)
    n_ok, n_total = None, None
    if md_text:
        m = RE_PARCIAL.search(md_text)
        if m:
            n_ok, n_total = int(m.group(1)), int(m.group(2))

    if not verdict and not has_entrega:
        return None  # sem report nenhum, nao mexe

    # Webhook antigo (so parcel_id + completed_at, sem verdict nem markdown)
    # nao tem informacao util pra decidir — nao mexer pra nao rebaixar a esmo.
    if not verdict and (not md_text or len(md_text) < 200) and not has_qa:
        return None

    return {
        "verdict": (verdict or "").upper().strip(),
        "score": score,
        "n_ok": n_ok,
        "n_total": n_total,
        "has_qa": has_qa,
        "has_entrega": has_entrega,
    }


def decide_from_verdict(info: dict) -> tuple[str, str]:
    """Retorna (decisao, motivo) baseado no veredicto real do comite."""
    n_ok = info.get("n_ok")
    n_total = info.get("n_total")
    verdict = info.get("verdict") or ""

    # Report parcial: < 6 agentes ok = NAO confiavel pra LANCE
    if n_ok is not None and n_total and n_ok < 6:
        return ("REVISAR", f"parcial {n_ok}/{n_total} agentes")

    # Sem agente 06 mas tem entrega: provavelmente parcial sem verdict
    if not info.get("has_qa") and info.get("has_entrega"):
        if n_ok is not None and n_ok == 0:
            return ("REVISAR", "report 0/N agentes (timeout sistemico)")

    # Veredicto explicito do comite
    if verdict in VERDICT_TO_DECISAO:
        return (VERDICT_TO_DECISAO[verdict], f"comite verdict={verdict}")

    # Veredicto desconhecido/INCONCLUSIVO
    if verdict in ("INCONCLUSIVO", "INCONCLUSIVE", ""):
        return ("REVISAR", f"verdict inconclusivo ({n_ok}/{n_total} agentes)")

    return ("REVISAR", f"verdict desconhecido: {verdict!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reports-dir", type=Path, default=DEFAULT_REPORTS_DIR)
    ap.add_argument("--data-json", type=Path, default=DEFAULT_DATA_JSON)
    ap.add_argument("--dry-run", action="store_true", help="nao escreve, so mostra")
    args = ap.parse_args()

    # Auto-fallback: se reports-dir default nao existe (CI sem reports ainda),
    # tenta dir LOCAL. Em CI sem nenhum report, exit 0 silencioso.
    if not args.reports_dir.exists():
        if LOCAL_OUTPUT_DIR.exists():
            print(f"[sync] reports-dir {args.reports_dir} nao existe, usando LOCAL: {LOCAL_OUTPUT_DIR}")
            args.reports_dir = LOCAL_OUTPUT_DIR
        else:
            print(f"[sync] sem reports disponiveis ({args.reports_dir}). Skip silencioso.")
            sys.exit(0)
    if not args.data_json.exists():
        print(f"[ERRO] data.json nao existe: {args.data_json}", file=sys.stderr)
        sys.exit(1)

    print(f"[sync] reports-dir: {args.reports_dir}")
    print(f"[sync] data.json:   {args.data_json}")
    print(f"[sync] dry-run:     {args.dry_run}")
    print()

    with open(args.data_json, encoding="utf-8") as f:
        data = json.load(f)

    lots = data.get("lots", [])
    print(f"[sync] {len(lots)} lotes em data.json")

    mudancas = []
    sem_report = 0
    sem_mudanca = 0

    for lot in lots:
        parcel_id = lot.get("parcel_id")
        if not parcel_id:
            continue

        info = load_verdict_for_parcel(args.reports_dir, parcel_id)
        if not info:
            sem_report += 1
            continue

        nova_decisao, motivo = decide_from_verdict(info)
        decisao_atual = lot.get("decisao")

        if nova_decisao != decisao_atual:
            mudancas.append({
                "parcel": parcel_id,
                "condado": lot.get("condado"),
                "address": lot.get("address"),
                "antes": decisao_atual,
                "depois": nova_decisao,
                "motivo": motivo,
                "score_lot": lot.get("score"),
                "roi_lot": lot.get("roi"),
            })
            lot["decisao"] = nova_decisao
            lot["decisao_origem"] = "comite_lotes" if "comite" in motivo else "lotes_parcial"
            lot["decisao_motivo"] = motivo
        else:
            # Mantem decisao mas marca origem
            lot["decisao_origem"] = "comite_lotes" if "comite" in motivo else "lotes_parcial"
            lot["decisao_motivo"] = motivo
            sem_mudanca += 1

    print(f"\n[sync] resumo:")
    print(f"  - lotes com mudanca de decisao: {len(mudancas)}")
    print(f"  - lotes confirmados (mesma decisao): {sem_mudanca}")
    print(f"  - lotes sem report LOTES: {sem_report}")

    if mudancas:
        print(f"\n[sync] MUDANCAS DE DECISAO ({len(mudancas)}):\n")
        # Agrupa por tipo de mudanca
        criticas = [m for m in mudancas if m["antes"] == "LANCE"]  # rebaixamentos sao criticos
        outras = [m for m in mudancas if m["antes"] != "LANCE"]

        if criticas:
            print(f"  *** {len(criticas)} REBAIXAMENTOS DE LANCE (proteger capital!): ***")
            for m in criticas:
                print(f"    {m['parcel']:20s} {m['condado']:14s} {m['antes']} -> {m['depois']:8s} ({m['motivo']})")
                print(f"      addr: {m['address']}  score_lot={m['score_lot']} ROI={m['roi_lot']}")
            print()

        if outras:
            print(f"  Outras mudancas ({len(outras)}):")
            for m in outras[:30]:
                print(f"    {m['parcel']:20s} {m['condado']:14s} {m['antes']} -> {m['depois']:8s} ({m['motivo']})")
            if len(outras) > 30:
                print(f"    ... +{len(outras)-30} omitidas")

    # Atualiza resumo: contagem real de LANCE
    if "resumo" in data:
        data["resumo"]["lances"] = sum(1 for l in lots if l.get("decisao") == "LANCE")

    if args.dry_run:
        print(f"\n[sync] DRY-RUN — nao gravou data.json")
    else:
        with open(args.data_json, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"\n[sync] data.json atualizado.")


if __name__ == "__main__":
    main()
