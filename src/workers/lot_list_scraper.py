"""Lot List Scraper - dado um sale especifico, baixa a lista de lotes do site
RealAuction/RealTaxDeed/RealTDA e popula a tabela `lots`.

Abordagem:
- Para cada condado, implementar um `_parse_<codigo>` se o HTML for distinto.
- Cai em um parser generico que tenta tabelas comuns quando nao tem especifico.

Obs.: RealAuction usa JavaScript-heavy na lista. Se `requests` nao retornar dados,
adicionar Playwright (ver requirements.txt comentado).
"""
import json
from bs4 import BeautifulSoup
from src.db.connection import cursor
from src.workers.base import BaseWorker
from src.utils.http import fetch


class LotListScraper(BaseWorker):
    name = "lot_list_scraper"

    def __init__(self, county_code=None, sale_id=None):
        super().__init__()
        self.county_code = county_code
        self.sale_id = sale_id

    def execute(self):
        with cursor() as cur:
            if self.sale_id:
                cur.execute("""
                    SELECT s.*, c.codigo AS county_codigo, c.url_sales, c.plataforma, c.nome AS county_nome
                    FROM sales s JOIN counties c ON c.id = s.county_id
                    WHERE s.id = ?
                """, (self.sale_id,))
                sales = cur.fetchall()
            elif self.county_code:
                cur.execute("""
                    SELECT s.*, c.codigo AS county_codigo, c.url_sales, c.plataforma, c.nome AS county_nome
                    FROM sales s JOIN counties c ON c.id = s.county_id
                    WHERE c.codigo = ? AND s.status = 'scheduled'
                    ORDER BY s.sale_date ASC
                """, (self.county_code,))
                sales = cur.fetchall()
            else:
                # Todos os sales futuros scheduled
                cur.execute("""
                    SELECT s.*, c.codigo AS county_codigo, c.url_sales, c.plataforma, c.nome AS county_nome
                    FROM sales s JOIN counties c ON c.id = s.county_id
                    WHERE s.status = 'scheduled' AND s.sale_date >= DATE('now')
                    ORDER BY s.sale_date ASC
                """)
                sales = cur.fetchall()

        for sale in sales:
            try:
                lots = self._scrape_sale(sale)
                self._save_lots(sale["id"], lots)
                self.items_processed += len(lots)
                self.logger.info(
                    f"{sale['county_codigo']} {sale['sale_date']}: {len(lots)} lotes salvos"
                )
            except Exception as e:
                self.errors_count += 1
                self.logger.warning(
                    f"Falha scraping {sale['county_codigo']} {sale['sale_date']}: {e}"
                )

    def _scrape_sale(self, sale):
        """Orquestra scraping do sale. Usa parser especifico se existir."""
        code = sale["county_codigo"]
        parser_method = getattr(self, f"_parse_{code.lower()}", None)
        if parser_method:
            return parser_method(sale)
        return self._parse_generic(sale)

    # ---------- PARSER LEE (piloto) ----------
    def _parse_lee(self, sale):
        """Lee County via lee.realtaxdeed.com. Plataforma RealAuction."""
        # A lista publica de proximos sales geralmente esta em:
        # https://lee.realtaxdeed.com/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=MM/DD/YYYY
        from datetime import datetime
        sale_dt = datetime.strptime(sale["sale_date"], "%Y-%m-%d").date()
        url = (f"https://lee.realtaxdeed.com/index.cfm?zaction=AUCTION&"
               f"Zmethod=PREVIEW&AUCTIONDATE={sale_dt.strftime('%m/%d/%Y')}")
        self.logger.info(f"LEE fetching {url}")
        resp = fetch(url)
        return self._parse_realauction_html(resp.text)

    # ---------- PARSER POLK ----------
    def _parse_polk(self, sale):
        # Polk usa realtda.com. Tentar endpoint publico.
        # Este e um stub - melhorar quando testado com site ao vivo.
        url = "https://www.realtda.com/index.cfm?zaction=USER&Zmethod=CALENDAR"
        self.logger.info(f"POLK fetching {url}")
        resp = fetch(url)
        return self._parse_realauction_html(resp.text)

    # ---------- PARSER GENERICO (RealAuction / RealTaxDeed) ----------
    def _parse_generic(self, sale):
        """Usa URL do condado como ponto de partida."""
        url = sale["url_sales"]
        self.logger.info(f"{sale['county_codigo']} generic fetching {url}")
        try:
            resp = fetch(url)
            return self._parse_realauction_html(resp.text)
        except Exception as e:
            self.logger.warning(f"Generic parser falhou em {url}: {e}")
            return []

    def _parse_realauction_html(self, html):
        """Extrai lotes de HTML estilo RealAuction. Heuristica por tabela."""
        soup = BeautifulSoup(html, "lxml")
        lots = []

        # Estrategia 1: procurar tabelas com cabecalhos comuns
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if not headers:
                continue
            if any("parcel" in h or "case" in h or "opening" in h or "bid" in h for h in headers):
                for tr in table.find_all("tr")[1:]:  # skip header
                    cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                    if not cells:
                        continue
                    lot = self._extract_lot_fields(headers, cells)
                    if lot.get("parcel_id"):
                        lots.append(lot)

        # Estrategia 2: procurar JSON embutido (alguns sites usam fetch interno)
        if not lots:
            import re
            for script in soup.find_all("script"):
                txt = script.get_text()
                if "parcelID" in txt or "ParcelID" in txt:
                    try:
                        # Tenta extrair arrays JSON
                        matches = re.findall(r"\[\s*\{.*?\}\s*\]", txt, re.DOTALL)
                        for m in matches:
                            try:
                                arr = json.loads(m)
                                for item in arr:
                                    lot = {
                                        "parcel_id": str(item.get("parcelID") or item.get("ParcelID") or ""),
                                        "case_num": str(item.get("caseNumber") or ""),
                                        "min_bid": float(item.get("openingBid", 0) or 0) or None,
                                        "address": item.get("situsAddress") or "",
                                        "raw_data_json": json.dumps(item),
                                    }
                                    if lot["parcel_id"]:
                                        lots.append(lot)
                            except Exception:
                                continue
                    except Exception:
                        pass

        return lots

    def _extract_lot_fields(self, headers, cells):
        lot = {}
        for i, h in enumerate(headers):
            if i >= len(cells):
                break
            v = cells[i]
            if "parcel" in h:
                lot["parcel_id"] = v
            elif "case" in h:
                lot["case_num"] = v
            elif "cert" in h:
                lot["tax_cert_num"] = v
            elif "opening" in h or "minimum bid" in h or h == "bid":
                try:
                    lot["min_bid"] = float(v.replace("$", "").replace(",", "").strip() or 0)
                except Exception:
                    pass
            elif "address" in h or "situs" in h:
                lot["address"] = v
            elif "city" in h:
                lot["city"] = v
            elif "assessed" in h:
                try:
                    lot["assessed_value"] = float(v.replace("$", "").replace(",", "").strip() or 0)
                except Exception:
                    pass
            elif "just" in h or "market" in h:
                try:
                    lot["just_value"] = float(v.replace("$", "").replace(",", "").strip() or 0)
                except Exception:
                    pass
            elif "legal" in h:
                lot["legal_description"] = v
        lot["raw_data_json"] = json.dumps(dict(zip(headers, cells)))
        return lot

    def _save_lots(self, sale_id, lots):
        if not lots:
            return
        with cursor() as cur:
            for lot in lots:
                cur.execute("""
                    INSERT INTO lots (sale_id, tax_cert_num, case_num, parcel_id,
                        address, city, zip, legal_description, property_type,
                        min_bid, assessed_value, just_value, raw_data_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(sale_id, parcel_id) DO UPDATE SET
                        case_num = excluded.case_num,
                        address = excluded.address,
                        min_bid = excluded.min_bid,
                        assessed_value = excluded.assessed_value,
                        just_value = excluded.just_value,
                        raw_data_json = excluded.raw_data_json,
                        scraped_at = CURRENT_TIMESTAMP
                """, (
                    sale_id, lot.get("tax_cert_num"), lot.get("case_num"),
                    lot.get("parcel_id"), lot.get("address"), lot.get("city"),
                    lot.get("zip"), lot.get("legal_description"),
                    lot.get("property_type"), lot.get("min_bid"),
                    lot.get("assessed_value"), lot.get("just_value"),
                    lot.get("raw_data_json"),
                ))
            # Atualiza total_lots no sale
            cur.execute(
                "UPDATE sales SET total_lots = (SELECT COUNT(*) FROM lots WHERE sale_id = ?) "
                "WHERE id = ?", (sale_id, sale_id)
            )


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else "LEE"
    LotListScraper(county_code=code).run()
