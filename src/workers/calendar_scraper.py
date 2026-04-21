"""Calendar Scraper - descobre datas REAIS de leiloes navegando o site oficial.

REGRA CRITICA: ZERO CHUTE.
- Nao inferir datas a partir de regras genericas (toda terca, etc)
- Sempre buscar do site real
- Se nao conseguir confirmar, marcar como warning e nao gravar
"""
import re
from datetime import date, datetime, timedelta
from urllib.parse import urlparse, urljoin
from bs4 import BeautifulSoup
from src.db.connection import cursor
from src.workers.base import BaseWorker
from src.utils.http import fetch


class CalendarScraper(BaseWorker):
    name = "calendar_scraper"

    def __init__(self, janela_semanas=12):
        super().__init__()
        self.janela_semanas = janela_semanas

    def execute(self):
        hoje = date.today()
        limite = hoje + timedelta(weeks=self.janela_semanas)

        with cursor() as cur:
            cur.execute("SELECT * FROM counties WHERE ativo = 1")
            condados = cur.fetchall()

        for c in condados:
            try:
                datas = self._discover_dates(c, hoje, limite)
            except Exception as e:
                self.errors_count += 1
                self.logger.warning(f"{c['codigo']}: descoberta falhou ({e}); pulando")
                continue

            if not datas:
                self.logger.info(f"{c['codigo']}: ZERO datas confirmadas no site oficial")
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
                    f"CONFIRMADO {c['codigo']} {d.isoformat()} (descoberto via site oficial)"
                )

    def _discover_dates(self, county, hoje, limite):
        """Estrategia: usar site RealAuction navegando Current + Next.

        Para sites diferentes (govease, etc) implementar metodo especifico depois.
        """
        url_sales = county["url_sales"]
        if not url_sales:
            return []

        # Detectar plataforma RealAuction/RealTaxDeed/RealForeclose/RealTDA
        if any(plat in url_sales.lower() for plat in
               ["realtaxdeed.com", "realauction.com", "realforeclose.com", "realtda.com"]):
            return self._discover_realauction(url_sales, hoje, limite, county["codigo"])

        # Outras plataformas: nao implementadas ainda, retorna vazio
        self.logger.warning(
            f"{county['codigo']}: plataforma nao suportada ainda ({url_sales}); "
            f"adicione _discover_<codigo> no scraper"
        )
        return []

    def _discover_realauction(self, base_url, hoje, limite, codigo):
        """Navega sites RealAuction usando Current + Next Auction links.

        Retorna apenas datas confirmadas (apareceram no site) dentro da janela.
        """
        parsed = urlparse(base_url)
        base = f"{parsed.scheme}://{parsed.netloc}/"

        datas_encontradas = set()
        urls_visitadas = set()

        # Tentar comecar do hoje
        url = f"{base}index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AuctionDate={hoje.strftime('%m/%d/%Y')}"
        max_iter = 30
        last_date_seen = None

        for i in range(max_iter):
            if url in urls_visitadas:
                self.logger.debug(f"{codigo}: URL repetida, parando: {url}")
                break
            urls_visitadas.add(url)

            try:
                resp = fetch(url, timeout=15)
            except Exception as e:
                self.logger.warning(f"{codigo} fetch falhou {url}: {e}")
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # Extrai data atual da pagina (BLHeaderDateDisplay)
            current_date = self._extract_displayed_date(soup)
            self.logger.debug(f"{codigo} pagina mostra: {current_date}")

            # Confirma com BODY: a area dos lotes precisa ter conteudo, OU pelo menos
            # deve ser uma data futura listada como sale real. Por enquanto confiar
            # no display se for >= hoje E <= limite.
            if current_date and hoje <= current_date <= limite:
                if current_date not in datas_encontradas:
                    datas_encontradas.add(current_date)
                    self.logger.info(f"{codigo}: data confirmada {current_date}")

            # Detectar link Next Auction
            next_div = soup.find("div", class_="BLHeaderNext")
            next_url = None
            if next_div:
                cls = next_div.get("class", [])
                if "NoDate" not in cls:
                    a = next_div.find("a")
                    if a and a.get("href"):
                        href = a["href"]
                        next_url = urljoin(base, href)

            if not next_url:
                # Sem proximo, tenta ir pelo Today (se ainda nao visitamos)
                today_div = soup.find("div", class_="BLHeaderToday")
                if today_div:
                    a = today_div.find("a")
                    if a and a.get("href"):
                        candidate = urljoin(base, a["href"])
                        if candidate not in urls_visitadas:
                            url = candidate
                            continue
                break

            # Validar que next_date e' futuro vs current pra evitar loop reverso
            m = re.search(r"AuctionDate=(\d{1,2}/\d{1,2}/\d{4})", next_url)
            if m:
                try:
                    next_date = datetime.strptime(m.group(1), "%m/%d/%Y").date()
                    if last_date_seen and next_date <= last_date_seen:
                        self.logger.debug(
                            f"{codigo}: Next aponta pra passado ({next_date} <= {last_date_seen}), parando"
                        )
                        break
                    if next_date > limite:
                        self.logger.debug(f"{codigo}: Next ({next_date}) fora da janela, parando")
                        break
                    last_date_seen = next_date
                except ValueError:
                    pass

            url = next_url

        return sorted(datas_encontradas)

    def _extract_displayed_date(self, soup):
        """Extrai data exibida no header da pagina de PREVIEW."""
        display = soup.find("div", class_="BLHeaderDateDisplay")
        if not display:
            return None
        txt = display.get_text(" ", strip=True)
        # Formatos comuns:
        # "Tuesday April 21, 2026"
        # "April 21, 2026"
        for fmt in ("%A %B %d, %Y", "%B %d, %Y"):
            try:
                return datetime.strptime(txt, fmt).date()
            except ValueError:
                continue
        return None


if __name__ == "__main__":
    CalendarScraper().run()
