"""Alert Engine - dispara alertas quando aparecer oportunidade fora do padrao.

Regras default:
- Lote com decisao=LANCE → alerta
- Lote com score > 200 (se scoring_engine rodou)
- Lote com ROI > 50% → alerta prioridade alta
- Lote subavaliado (just_value / min_bid > 10x) → oportunidade rara

Canais:
- Email (SMTP via env vars) → principal
- Slack webhook (opcional)
"""
import os
import smtplib
from email.message import EmailMessage
from datetime import date
from src.db.connection import cursor
from src.workers.base import BaseWorker
from src.utils.http import fetch

def _env(name, default=""):
    """Env var com tratamento de string vazia."""
    v = os.environ.get(name, "") or ""
    v = v.strip()
    return v if v else default

def _envf(name, default):
    try:
        return float(_env(name, str(default)))
    except (ValueError, TypeError):
        return float(default)

def _envi(name, default):
    try:
        return int(_env(name, str(default)))
    except (ValueError, TypeError):
        return int(default)

SCORE_ALERTA = _envf("SCORE_ALERTA", 70)
ROI_ALERTA = _envf("ROI_ALERTA", 0.50)
RATIO_SUBAVALIADO = _envf("RATIO_SUBAVALIADO", 10)

SMTP_HOST = _env("SMTP_HOST")
SMTP_PORT = _envi("SMTP_PORT", 587)
SMTP_USER = _env("SMTP_USER")
SMTP_PASS = _env("SMTP_PASSWORD")
ALERT_TO = _env("ALERT_EMAIL_TO", "dpr2004@hotmail.com")
SLACK_WEBHOOK = _env("SLACK_WEBHOOK_URL")


class AlertEngine(BaseWorker):
    name = "alert_engine"

    def execute(self):
        oportunidades = self._buscar_oportunidades()
        if not oportunidades:
            self.logger.info("Sem oportunidades novas pra alertar")
            return

        # Deduplicar: so alerta se nao alertou ainda
        novas = self._filtrar_nao_alertados(oportunidades)
        if not novas:
            self.logger.info(f"{len(oportunidades)} oportunidades encontradas mas ja alertadas")
            return

        self.logger.info(f"Alertando sobre {len(novas)} oportunidades novas")

        # Email consolidado
        if SMTP_HOST and SMTP_USER and SMTP_PASS:
            try:
                self._enviar_email(novas)
                self.items_processed += len(novas)
                self._marcar_alertados(novas, channel="email")
            except Exception as e:
                self.errors_count += 1
                self.logger.error(f"Falha email: {e}")

        # Slack
        if SLACK_WEBHOOK:
            try:
                self._enviar_slack(novas)
            except Exception as e:
                self.logger.warning(f"Slack falhou: {e}")

    def _buscar_oportunidades(self):
        with cursor() as cur:
            cur.execute("""
                SELECT l.id, l.parcel_id, l.address, l.city, l.min_bid, l.just_value,
                       s.sale_date, c.codigo AS condado, c.state AS estado,
                       sc.final_score, sc.decision, sc.projected_roi,
                       sc.max_bid_recommended, sc.projected_profit
                FROM lots l
                JOIN sales s ON s.id = l.sale_id
                JOIN counties c ON c.id = s.county_id
                LEFT JOIN scores sc ON sc.lot_id = l.id
                WHERE s.sale_date >= DATE('now')
                  AND (
                      sc.decision = 'LANCE'
                      OR sc.final_score >= ?
                      OR sc.projected_roi >= ?
                      OR (l.just_value > 0 AND l.min_bid > 0
                          AND (l.just_value / l.min_bid) >= ?)
                  )
                ORDER BY COALESCE(sc.final_score, 0) DESC, l.min_bid ASC
            """, (SCORE_ALERTA, ROI_ALERTA, RATIO_SUBAVALIADO))
            return cur.fetchall()

    def _filtrar_nao_alertados(self, oportunidades):
        novas = []
        with cursor() as cur:
            for o in oportunidades:
                cur.execute(
                    "SELECT 1 FROM alerts WHERE lot_id = ? AND alert_type = 'oportunidade'",
                    (o["id"],)
                )
                if not cur.fetchone():
                    novas.append(o)
        return novas

    def _marcar_alertados(self, lista, channel):
        with cursor() as cur:
            for l in lista:
                cur.execute("""
                    INSERT INTO alerts (lot_id, alert_type, message, severity, sent_to, sent_at)
                    VALUES (?, 'oportunidade', ?, ?, ?, CURRENT_TIMESTAMP)
                """, (l["id"], f"{l['condado']} {l['parcel_id']}",
                      "HIGH" if (l["decision"] == "LANCE") else "MEDIUM", channel))

    def _formatar_oportunidade(self, o):
        score = f"{o['final_score']:.0f}" if o['final_score'] else "—"
        roi = f"{o['projected_roi']*100:.1f}%" if o['projected_roi'] else "—"
        min_bid = f"${o['min_bid']:,.0f}" if o['min_bid'] else "—"
        max_bid = f"${o['max_bid_recommended']:,.0f}" if o['max_bid_recommended'] else "—"
        profit = f"${o['projected_profit']:,.0f}" if o['projected_profit'] else "—"
        return (
            f"[{o['decision'] or 'REVISAR'}] {o['condado']} · {o['sale_date']}\n"
            f"  Parcel: {o['parcel_id']}\n"
            f"  Endereço: {o['address'] or '(não disponível)'}, {o['city'] or ''}\n"
            f"  Bid Mín.: {min_bid}  |  Bid Máx. Rec.: {max_bid}\n"
            f"  Lucro proj.: {profit}  |  ROI: {roi}  |  Score: {score}"
        )

    def _enviar_email(self, oportunidades):
        body_lines = [
            "OPORTUNIDADES TAX DEED - EVEREST INVESTMENTS",
            "=" * 60,
            f"Data: {date.today().strftime('%d/%b/%Y')}",
            f"Oportunidades novas: {len(oportunidades)}",
            "",
        ]
        for o in oportunidades[:30]:  # max 30 por email
            body_lines.append(self._formatar_oportunidade(o))
            body_lines.append("")
        body_lines.append("---")
        body_lines.append("Dashboard: https://dpr2004.github.io/everest-taxdeed/")
        body_lines.append("Sistema: Everest TaxDeed Workers")

        msg = EmailMessage()
        msg["From"] = SMTP_USER
        msg["To"] = ALERT_TO
        msg["Subject"] = f"[Everest TaxDeed] {len(oportunidades)} oportunidade(s) nova(s)"
        msg.set_content("\n".join(body_lines))

        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
            s.starttls()
            s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        self.logger.info(f"Email enviado para {ALERT_TO} com {len(oportunidades)} oportunidades")

    def _enviar_slack(self, oportunidades):
        top = oportunidades[:10]
        text = f"*:fire: {len(oportunidades)} oportunidade(s) tax deed*\n\n"
        for o in top:
            text += f"• *{o['condado']}* {o['sale_date']} · {o['parcel_id']} · "
            text += f"Bid: ${o['min_bid'] or 0:,.0f}"
            if o['final_score']:
                text += f" · Score: {o['final_score']:.0f}"
            if o['decision']:
                text += f" · *{o['decision']}*"
            text += "\n"
        text += "\n<https://dpr2004.github.io/everest-taxdeed/|Abrir dashboard>"
        fetch(SLACK_WEBHOOK, method="POST", json={"text": text}, timeout=10)
        self.logger.info(f"Slack notificado com {len(top)} oportunidades")


if __name__ == "__main__":
    AlertEngine().run()
