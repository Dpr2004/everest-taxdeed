"""Envia o PDF semanal mais recente para o ALERT_EMAIL_TO via SMTP."""
import os
import smtplib
import glob
from datetime import date
from email.message import EmailMessage

SMTP_HOST = os.environ["SMTP_HOST"]
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ["SMTP_USER"]
SMTP_PASS = os.environ["SMTP_PASSWORD"]
TO = os.environ.get("ALERT_EMAIL_TO", "dpr2004@hotmail.com")
FROM = os.environ.get("ALERT_EMAIL_FROM", SMTP_USER)
OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./data/outputs")

pdfs = sorted(glob.glob(f"{OUTPUT_DIR}/Calendario_Leiloes_TaxDeed_*.pdf"))
if not pdfs:
    raise SystemExit("Nenhum PDF encontrado para enviar")
pdf = pdfs[-1]

msg = EmailMessage()
msg["From"] = FROM
msg["To"] = TO
msg["Subject"] = f"Calendario Tax Deed FL - Semana de {date.today().strftime('%d/%b/%Y')}"
msg.set_content(
    f"Ola Daniel,\n\n"
    f"Segue em anexo o calendario semanal de leiloes tax deed dos 11 condados "
    f"da Florida para as proximas 8 semanas.\n\n"
    f"PDF gerado automaticamente em {date.today().strftime('%d/%m/%Y')} pelo sistema "
    f"Everest TaxDeed Workers rodando no GitHub Actions.\n\n"
    f"Sistema Cowork - Everest Investments"
)

with open(pdf, "rb") as f:
    msg.add_attachment(
        f.read(),
        maintype="application",
        subtype="pdf",
        filename=os.path.basename(pdf),
    )

with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
    s.starttls()
    s.login(SMTP_USER, SMTP_PASS)
    s.send_message(msg)

print(f"OK: email enviado para {TO} com anexo {os.path.basename(pdf)}")
