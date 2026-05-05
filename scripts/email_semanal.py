"""
email_semanal.py — Email todo domingo com leiloes proximos 15 dias.

Enviado pra Daniel selecionar manualmente quais condados quer rodar
pesquisa LOTES (em vez de auto-fila). Reduz custo + sobrecarga.

USO:
  python scripts/email_semanal.py            # envia (se SMTP configurado)
  python scripts/email_semanal.py --dry-run  # so imprime preview

AGENDAR (cron domingo 7h ET = 11h UTC):
  No GitHub Actions: criar workflow .github/workflows/email-semanal.yml
  com cron "0 11 * * 0" (todo domingo 11h UTC).

REQUISITOS env:
  SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, ALERT_EMAIL_TO

NOTA: este script NAO esta ativo no workflow ainda — primeiro precisa
sistema 100% confiavel. Daniel ativa manualmente quando saude.html mostrar
0 problemas criticos.
"""
import argparse
import json
import os
import smtplib
import sqlite3
import sys
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "taxdeed.db"
DASHBOARD_BASE = "https://dpr2004.github.io/everest-taxdeed"


def buscar_leiloes_proximos(dias=15):
    """Lista leiloes nos proximos N dias com lots por condado."""
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    hoje = date.today()
    limite = hoje + timedelta(days=dias)

    c.execute("""
        SELECT cs.codigo, cs.nome, s.sale_date,
               (SELECT COUNT(*) FROM lots l WHERE l.sale_id = s.id AND l.parcel_id NOT LIKE 'AID_%') as total_lots,
               (SELECT COUNT(*) FROM lots l JOIN scores sc ON sc.lot_id = l.id
                WHERE l.sale_id = s.id AND l.parcel_id NOT LIKE 'AID_%' AND sc.decision = 'LANCE') as lance_lots,
               (SELECT COUNT(*) FROM lots l JOIN scores sc ON sc.lot_id = l.id
                WHERE l.sale_id = s.id AND l.parcel_id NOT LIKE 'AID_%' AND sc.decision = 'REVISAR') as revisar_lots
        FROM sales s JOIN counties cs ON cs.id = s.county_id
        WHERE DATE(s.sale_date) BETWEEN ? AND ?
        ORDER BY s.sale_date, cs.codigo
    """, (hoje.isoformat(), limite.isoformat()))

    return list(c.fetchall())


def render_html(leiloes):
    rows = []
    for l in leiloes:
        rows.append(f"""
        <tr>
          <td><strong>{l['sale_date']}</strong></td>
          <td>{l['codigo']}</td>
          <td>{l['nome']}</td>
          <td style="text-align:center">{l['total_lots']}</td>
          <td style="text-align:center;color:#34d399"><strong>{l['lance_lots']}</strong></td>
          <td style="text-align:center;color:#fbbf24">{l['revisar_lots']}</td>
          <td><a href="{DASHBOARD_BASE}/index.html?c={l['codigo']}" style="color:#E8C86A">Ver →</a></td>
        </tr>
        """)

    return f"""
    <html><body style="font-family:sans-serif;color:#222;background:#f9f9f9;padding:20px;">
    <h2 style="color:#13123E">Leilões TaxDeed — Próximos 15 dias</h2>
    <p>Domingo {date.today().isoformat()} — {len(leiloes)} leilões agendados.</p>
    <p>Selecione os condados que quer rodar pesquisa LOTES esta semana:</p>
    <table style="width:100%;border-collapse:collapse;background:#fff;">
      <thead><tr style="background:#13123E;color:#fff;">
        <th style="padding:8px">Data</th><th>Condado</th><th>Cidade</th>
        <th>Lots</th><th>LANCE</th><th>REVISAR</th><th>Link</th>
      </tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    <p style="margin-top:20px;font-size:12px;color:#666;">
      Sistema: <a href="{DASHBOARD_BASE}/saude.html">saúde</a> •
      <a href="{DASHBOARD_BASE}/">dashboard</a>
    </p>
    </body></html>
    """


def enviar(html):
    host = os.environ.get("SMTP_HOST")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    pwd = os.environ.get("SMTP_PASSWORD")
    to_addr = os.environ.get("ALERT_EMAIL_TO", "dpr2004@gmail.com")

    if not all([host, user, pwd]):
        print("[email] SMTP nao configurado — pulando envio", file=sys.stderr)
        return False

    msg = MIMEText(html, 'html', 'utf-8')
    msg['Subject'] = f"[Everest TaxDeed] Leilões próximos 15 dias — {date.today()}"
    msg['From'] = user
    msg['To'] = to_addr

    with smtplib.SMTP(host, port, timeout=30) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)
    print(f"[email] enviado pra {to_addr}")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--dias', type=int, default=15)
    args = ap.parse_args()

    leiloes = buscar_leiloes_proximos(args.dias)
    print(f"[email] {len(leiloes)} leiloes nos proximos {args.dias} dias")

    if not leiloes:
        print("[email] sem leiloes — nao envia")
        return

    html = render_html(leiloes)
    if args.dry_run:
        preview = ROOT / "web" / "email-preview.html"
        preview.write_text(html, encoding='utf-8')
        print(f"[email] preview salvo em {preview}")
    else:
        enviar(html)


if __name__ == "__main__":
    main()
