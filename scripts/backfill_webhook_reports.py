"""
backfill_webhook_reports.py — preenche web/reports/{parcel}.json com verdict
real do comite LOTES (Tier 2 local) pra reports antigos que so tinham
{parcel_id, completed_at} (94% dos webhooks recebidos pre-feature do
payload completo).

Le os LOTES_*_06-qa.json + bridges (lordvader, frodo) locais e produz
payload no MESMO formato que server.js fireWebhook envia hoje.

USO:
  python scripts/backfill_webhook_reports.py [--dry-run]
"""
import argparse, json, re, sys
from datetime import datetime, timezone
from pathlib import Path

LOTES_OUTPUT = Path(r"C:\Users\dpr20\iCloudDrive\lotes-analyzer\output")
WEB_REPORTS = Path(__file__).resolve().parent.parent / "web" / "reports"


def load_lotes_data(parcel: str):
    """Le os arquivos locais pra um parcel e monta payload."""
    qa_file = LOTES_OUTPUT / f"LOTES_{parcel}_06-qa-investment-committee.json"
    lordvader = LOTES_OUTPUT / f"LOTES_{parcel}_lordvader.json"
    frodo = LOTES_OUTPUT / f"LOTES_{parcel}_frodo.json"
    entrega = LOTES_OUTPUT / f"ENTREGA-FINAL_{parcel}.md"

    payload = {"parcel_id": parcel, "completed_at": datetime.now(timezone.utc).isoformat()}

    if lordvader.exists():
        try:
            lv = json.loads(lordvader.read_text(encoding='utf-8'))
            payload['final_score'] = lv.get('final_score')
            payload['verdict'] = lv.get('verdict') or lv.get('final_verdict')
            payload['recommendation'] = lv.get('recommendation')
            payload['red_flags'] = lv.get('red_flags', [])[:15]
            payload['top_3_reasons'] = lv.get('top_3_reasons', [])
        except Exception as e:
            print(f"  [WARN] {parcel} lordvader: {e}", file=sys.stderr)

    # Fallback: se lordvader vazio, le agente 06 direto
    if not payload.get('verdict') and qa_file.exists():
        try:
            a06 = json.loads(qa_file.read_text(encoding='utf-8'))
            payload['verdict'] = a06.get('verdict')
            if not payload.get('final_score'):
                payload['final_score'] = a06.get('final_score') or a06.get('score')
            if not payload.get('recommendation'):
                payload['recommendation'] = a06.get('recommendation')
            if not payload.get('red_flags') and a06.get('red_flags'):
                payload['red_flags'] = a06.get('red_flags', [])[:15]
        except Exception as e:
            print(f"  [WARN] {parcel} a06: {e}", file=sys.stderr)

    if frodo.exists():
        try:
            fr = json.loads(frodo.read_text(encoding='utf-8'))
            payload['vmf_final'] = fr.get('vmf_final')
            rec = fr.get('recommendation') or {}
            payload['max_bid_recommended'] = rec.get('max_bid')
        except Exception:
            pass

    if entrega.exists():
        try:
            payload['report_markdown'] = entrega.read_text(encoding='utf-8')
        except Exception:
            pass

    return payload if payload.get('verdict') else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args()

    if not LOTES_OUTPUT.exists():
        print(f"[ERRO] LOTES output dir nao existe: {LOTES_OUTPUT}")
        sys.exit(1)

    # Lista todos os parcels que tem agente 06 (verdict real)
    parcels = []
    for f in LOTES_OUTPUT.glob('LOTES_*_06-qa-investment-committee.json'):
        m = re.match(r'LOTES_(.+?)_06-qa-investment-committee\.json', f.name)
        if m:
            parcels.append(m.group(1))

    print(f"[backfill] Encontrados {len(parcels)} parcels com agente 06 local")

    backfilled = 0
    skipped_empty = 0
    skipped_already = 0

    for parcel in parcels:
        payload = load_lotes_data(parcel)
        if not payload:
            skipped_empty += 1
            continue

        target = WEB_REPORTS / f"{parcel}.json"
        # Se ja tem com verdict, nao sobrescreve
        if target.exists():
            try:
                existing = json.loads(target.read_text(encoding='utf-8'))
                if existing.get('verdict') or existing.get('final_verdict'):
                    skipped_already += 1
                    continue
            except Exception:
                pass

        if args.dry_run:
            print(f"  [DRY] {parcel}: verdict={payload['verdict']} score={payload.get('final_score')}")
        else:
            WEB_REPORTS.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
            print(f"  [OK]  {parcel}: verdict={payload['verdict']} score={payload.get('final_score')}")
        backfilled += 1

    print(f"\n[backfill] {backfilled} reports {'simulados' if args.dry_run else 'gravados'}, "
          f"{skipped_already} ja tinham verdict, {skipped_empty} sem dados utilizaveis.")


if __name__ == '__main__':
    main()
