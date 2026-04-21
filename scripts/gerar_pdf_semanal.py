"""Gera o PDF do calendario semanal lendo dados do DB (ou regras calculadas).

Usado pelo GitHub Actions toda segunda-feira.
"""
import os
import sys
from pathlib import Path

# Garante imports do src
sys.path.insert(0, str(Path(__file__).parent.parent))

# Reusa o script existente em Florida/gerar_pdf_calendario.py
# Como no GitHub Actions o arquivo nao existe, replicamos o gerador aqui.

from datetime import date, timedelta
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, Table,
                                 TableStyle, PageBreak)
from reportlab.lib.enums import TA_LEFT

from src.db.connection import cursor

OUTPUT_DIR = os.environ.get("OUTPUT_DIR", "./data/outputs")
Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)

HOJE = date.today()
INICIO = HOJE - timedelta(days=HOJE.weekday())
FIM = INICIO + timedelta(weeks=8) - timedelta(days=1)
OUT_PDF = f"{OUTPUT_DIR}/Calendario_Leiloes_TaxDeed_{HOJE.strftime('%Y-%m-%d')}.pdf"

NAVY = colors.HexColor("#1F3864")
LIGHT = colors.HexColor("#F2F2F2")
GREEN = colors.HexColor("#C6EFCE")
YELLOW = colors.HexColor("#FFEB9C")
PINK = colors.HexColor("#FFC7CE")
GRAY = colors.HexColor("#595959")
WHITE = colors.white
BLACK = colors.black
ACCENT = colors.HexColor("#0563C1")


def _deco(canv, doc):
    canv.saveState()
    w, h = landscape(A4)
    canv.setFont("Helvetica-Bold", 9)
    canv.setFillColor(NAVY)
    canv.drawString(1.2 * cm, h - 0.7 * cm,
                    "EVEREST INVESTMENTS - Calendario Tax Deed Florida (11 condados)")
    canv.setFont("Helvetica", 8)
    canv.setFillColor(GRAY)
    canv.drawRightString(w - 1.2 * cm, h - 0.7 * cm,
                         f"Gerado em {HOJE.strftime('%d/%b/%Y')}  |  Janela: "
                         f"{INICIO.strftime('%d/%b')} a {FIM.strftime('%d/%b/%Y')}")
    canv.setStrokeColor(NAVY); canv.setLineWidth(0.5)
    canv.line(1.2 * cm, h - 0.9 * cm, w - 1.2 * cm, h - 0.9 * cm)
    canv.setFont("Helvetica", 8); canv.setFillColor(GRAY)
    canv.drawString(1.2 * cm, 0.7 * cm,
                    "Daniel Rocha - Everest Investments  |  dpr2004@hotmail.com")
    canv.drawRightString(w - 1.2 * cm, 0.7 * cm, f"Pagina {doc.page}")
    canv.setStrokeColor(colors.lightgrey)
    canv.line(1.2 * cm, 1.0 * cm, w - 1.2 * cm, 1.0 * cm)
    canv.restoreState()


def main():
    # Le sales do DB
    with cursor() as cur:
        cur.execute("""
            SELECT s.sale_date, s.sale_time, c.codigo, c.nome, c.horario_et,
                   c.plataforma, c.url_sales, c.url_clerk, c.deposito, c.status,
                   c.telefone, c.cadencia_tipo
            FROM sales s JOIN counties c ON c.id = s.county_id
            WHERE s.sale_date BETWEEN ? AND ?
            ORDER BY s.sale_date, c.codigo
        """, (INICIO.isoformat(), FIM.isoformat()))
        sales = cur.fetchall()
        cur.execute("""
            SELECT codigo, nome, cadencia_tipo, horario_et, plataforma,
                   url_sales, url_clerk, deposito, status, telefone
            FROM counties WHERE cadencia_tipo = 'VERIFY' AND ativo = 1
        """)
        verify = cur.fetchall()

    doc = SimpleDocTemplate(OUT_PDF, pagesize=landscape(A4),
                            leftMargin=1.2 * cm, rightMargin=1.2 * cm,
                            topMargin=1.5 * cm, bottomMargin=1.3 * cm,
                            title="Calendario Tax Deed 11 Condados FL",
                            author="Everest Investments")
    story = []
    styles = getSampleStyleSheet()
    st_title = ParagraphStyle("t", parent=styles["Heading1"], fontSize=18,
                              textColor=NAVY, spaceAfter=4, alignment=TA_LEFT)
    st_sub = ParagraphStyle("s", parent=styles["Normal"], fontSize=9,
                            textColor=GRAY, spaceAfter=10)
    st_h2 = ParagraphStyle("h2", parent=styles["Heading2"], fontSize=13,
                           textColor=NAVY, spaceBefore=12, spaceAfter=6)
    st_h3 = ParagraphStyle("h3", parent=styles["Heading3"], fontSize=11,
                           textColor=NAVY, spaceBefore=8, spaceAfter=4)
    st_body = ParagraphStyle("b", parent=styles["Normal"], fontSize=9,
                             textColor=BLACK, leading=12)
    st_small = ParagraphStyle("sm", parent=styles["Normal"], fontSize=8,
                              textColor=GRAY, leading=10)
    st_link = ParagraphStyle("l", parent=styles["Normal"], fontSize=8,
                             textColor=ACCENT, leading=10)

    story.append(Paragraph("CALENDARIO DE LEILOES - TAX DEED SALES", st_title))
    story.append(Paragraph(
        f"Everest Investments  |  11 condados FL  |  Janela: "
        f"<b>{INICIO.strftime('%d/%b/%Y')} a {FIM.strftime('%d/%b/%Y')}</b>", st_sub))

    # Legenda
    leg = [["Status", "Significado"],
           ["CONFIRMADO", "Regra de cadencia publicada oficialmente"],
           ["PROJETADO", "Cadencia historica - validar no site"],
           ["A VERIFICAR", "Sem cadencia fixa publica - consultar clerk"]]
    tleg = Table(leg, colWidths=[3.5 * cm, 22 * cm])
    tleg.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 1), (0, 1), GREEN),
        ("BACKGROUND", (0, 2), (0, 2), YELLOW),
        ("BACKGROUND", (0, 3), (0, 3), PINK),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(tleg); story.append(Spacer(1, 8))

    # Sumario
    story.append(Paragraph("Resumo", st_h2))
    story.append(Paragraph(
        f"&bull; <b>{len(sales)}</b> leiloes com data nesta janela.<br/>"
        f"&bull; <b>{len(verify)}</b> condados sem cadencia fixa.<br/>"
        f"&bull; Plataformas: RealAuction / RealTaxDeed / RealTDA / RealForeclose.", st_body))
    story.append(Spacer(1, 8))

    story.append(Paragraph("Agenda semana a semana (8 semanas)", st_h2))
    grupos = {}
    dias_pt = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]
    from datetime import datetime as dt
    for s in sales:
        d = dt.strptime(s["sale_date"], "%Y-%m-%d").date()
        seg = d - timedelta(days=d.weekday())
        grupos.setdefault(seg, []).append((d, s))

    for i in range(8):
        ini = INICIO + timedelta(weeks=i)
        fim = ini + timedelta(days=6)
        evs = sorted(grupos.get(ini, []), key=lambda x: x[0])
        story.append(Paragraph(
            f"Semana {i+1}  |  {ini.strftime('%d/%b')} a {fim.strftime('%d/%b/%Y')}", st_h3))
        if not evs:
            story.append(Paragraph("Sem leiloes projetados nesta semana.", st_small))
            story.append(Spacer(1, 4)); continue
        rows = [["Data", "Dia", "Condado", "Horario", "Plataforma", "Cadencia",
                 "Status", "Sales", "Clerk"]]
        for d, s in evs:
            link_s = f'<link href="{s["url_sales"]}" color="blue"><u>auction</u></link>'
            link_c = f'<link href="{s["url_clerk"]}" color="blue"><u>clerk</u></link>'
            rows.append([d.strftime("%d/%b"), dias_pt[d.weekday()], s["codigo"],
                         s["horario_et"], s["plataforma"], s["cadencia_tipo"],
                         s["status"], Paragraph(link_s, st_link),
                         Paragraph(link_c, st_link)])
        t = Table(rows, colWidths=[1.6 * cm, 1.1 * cm, 2.3 * cm, 2.3 * cm, 2.5 * cm,
                                    6 * cm, 2.2 * cm, 1.8 * cm, 1.8 * cm])
        ts = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT]),
        ])
        for ri, (_, s) in enumerate(evs, start=1):
            cor = GREEN if s["status"] == "CONFIRMADO" else \
                  YELLOW if s["status"] == "PROJETADO" else PINK
            ts.add("BACKGROUND", (6, ri), (6, ri), cor)
        t.setStyle(ts)
        story.append(t); story.append(Spacer(1, 6))

    if verify:
        story.append(PageBreak())
        story.append(Paragraph("Condados a verificar manualmente", st_h2))
        vrows = [["Condado", "Plataforma", "Sales URL", "Clerk", "Telefone"]]
        for v in verify:
            ls = f'<link href="{v["url_sales"]}" color="blue"><u>auction</u></link>'
            lc = f'<link href="{v["url_clerk"]}" color="blue"><u>clerk</u></link>'
            vrows.append([v["codigo"], v["plataforma"], Paragraph(ls, st_link),
                          Paragraph(lc, st_link), v["telefone"]])
        t2 = Table(vrows, colWidths=[3 * cm, 4 * cm, 3 * cm, 3 * cm, 4 * cm])
        t2.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), WHITE),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, LIGHT]),
        ]))
        story.append(t2)

    doc.build(story, onFirstPage=_deco, onLaterPages=_deco)
    print(f"OK: {OUT_PDF}")


if __name__ == "__main__":
    main()
