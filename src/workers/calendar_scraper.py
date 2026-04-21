"""Calendar Scraper - varre os 11 condados e popula/atualiza tabela `sales`.

Estrategia:
1. Para condados com regra clara (WEEKLY, ORDINAL): calcular datas nas proximas 8 semanas.
2. Para condados VERIFY: tentar baixar a pagina do clerk e extrair eventos publicados.
3. Gravar em `sales` (upsert).
"""
from datetime import date, timedelta
from src.db.connection import cursor
from src.workers.base import BaseWorker
from src.utils.http import fetch
from bs4 import BeautifulSoup


def datas_janela(cadencia_tipo, dia_semana, ordem, inicio, fim):
    datas = []
    if cadencia_tipo == "WEEKLY" and dia_semana is not None:
        d = inicio
        while d <= fim:
            if d.weekday() == dia_semana:
                datas.append(d)
            d += timedelta(days=1)
    elif cadencia_tipo == "ORDINAL" and ordem and dia_semana is not None:
        ano, mes = inicio.year, inicio.month
        while True:
            primeiro = date(ano, mes, 1)
            offset = (dia_semana - primeiro.weekday()) % 7
            cand = primeiro + timedelta(days=offset + (ordem - 1) * 7)
            if cand.month == mes and inicio <= cand <= fim:
                datas.append(cand)
            mes += 1
            if mes > 12:
                mes = 1
                ano += 1
            if date(ano, mes, 1) > fim:
                break
    return datas


class CalendarScraper(BaseWorker):
    name = "calendar_scraper"

    def __init__(self, janela_semanas=8):
        super().__init__()
        self.janela_semanas = janela_semanas

    def execute(self):
        hoje = date.today()
        inicio = hoje - timedelta(days=hoje.weekday())
        fim = inicio + timedelta(weeks=self.janela_semanas) - timedelta(days=1)

        with cursor() as cur:
            cur.execute("SELECT * FROM counties WHERE ativo = 1")
            condados = cur.fetchall()

        for c in condados:
            datas = datas_janela(
                c["cadencia_tipo"], c["cadencia_dia_semana"],
                c["cadencia_ordem"], inicio, fim
            )
            if not datas:
                # VERIFY: tentar fetch da pagina do clerk
                try:
                    datas = self._scrape_verify(c)
                except Exception as e:
                    self.logger.warning(
                        f"{c['codigo']}: fallback scraping falhou ({e}); pulando"
                    )
                    continue

            for d in datas:
                with cursor() as cur2:
                    cur2.execute("""
                        INSERT INTO sales (county_id, sale_date, sale_time, status, scraped_at)
                        VALUES (?, ?, ?, 'scheduled', CURRENT_TIMESTAMP)
                        ON CONFLICT(county_id, sale_date) DO UPDATE SET
                            sale_time = excluded.sale_time,
                            scraped_at = excluded.scraped_at
                    """, (c["id"], d.isoformat(), c["horario_et"]))
                self.items_processed += 1
                self.logger.info(
                    f"Upsert sale: {c['codigo']} {d.isoformat()} {c['horario_et']}"
                )

    def _scrape_verify(self, c):
        """Fallback para condados VERIFY - tenta extrair datas do HTML da pagina do clerk.

        Implementacao basica: procura por datas no formato MM/DD/YYYY ou similar.
        Para cada condado especifico pode ser melhorado (ex.: API JSON).
        """
        if not c["url_clerk"]:
            return []
        resp = fetch(c["url_clerk"])
        soup = BeautifulSoup(resp.text, "lxml")
        import re
        texto = soup.get_text(" ", strip=True)
        padrao = re.compile(r"\b(\d{1,2})/(\d{1,2})/(20\d{2})\b")
        hoje = date.today()
        encontradas = set()
        for m in padrao.finditer(texto):
            try:
                mm, dd, yyyy = int(m.group(1)), int(m.group(2)), int(m.group(3))
                d = date(yyyy, mm, dd)
                if hoje <= d <= hoje + timedelta(weeks=self.janela_semanas):
                    encontradas.add(d)
            except ValueError:
                continue
        return sorted(encontradas)


if __name__ == "__main__":
    CalendarScraper().run()
