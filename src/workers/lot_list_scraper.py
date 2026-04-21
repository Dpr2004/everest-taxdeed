"""Lot List Scraper - dado um sale do DB, baixa lista de lotes do site oficial.

REGRA: ZERO chute. Sempre buscar do site real e validar se ha sale de verdade.

Para sites RealAuction (RealTaxDeed/RealAuction/RealForeclose/RealTDA):
- Construir URL: BASE/index.cfm?zaction=AUCTION&Zmethod=PREVIEW&AUCTIONDATE=MM/DD/YYYY
- Se <div role="main"> esta vazio = nao ha sale real nessa data (mesmo se header mostrar)
- Se tem lotes, parsear tabela ou JSON embedded
"""
import json
import os
import re
from datetime import datetime
from urllib.parse import urlparse
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
                    SELECT s.*, c.codigo AS county_codigo, c.url_sales,
                           c.plataforma, c.nome AS county_nome
                    FROM sales s JOIN counties c ON c.id = s.county_id
                    WHERE s.id = ?
                """, (self.sale_id,))
                sales = cur.fetchall()
            elif self.county_code:
                cur.execute("""
                    SELECT s.*, c.codigo AS county_codigo, c.url_sales,
                           c.plataforma, c.nome AS county_nome
                    FROM sales s JOIN counties c ON c.id = s.county_id
                    WHERE c.codigo = ? AND s.status = 'scheduled'
                      AND s.sale_date >= DATE('now')
                    ORDER BY s.sale_date ASC
                """, (self.county_code,))
                sales = cur.fetchall()
            else:
                cur.execute("""
                    SELECT s.*, c.codigo AS county_codigo, c.url_sales,
                           c.plataforma, c.nome AS county_nome
                    FROM sales s JOIN counties c ON c.id = s.county_id
                    WHERE s.status = 'scheduled' AND s.sale_date >= DATE('now')
                    ORDER BY s.sale_date ASC
                """)
                sales = cur.fetchall()

        for sale in sales:
            try:
                lots, html = self._scrape_sale(sale)
                if lots:
                    self._save_lots(sale["id"], lots)
                    self.items_processed += len(lots)
                    self.logger.info(
                        f"OK {sale['county_codigo']} {sale['sale_date']}: {len(lots)} lotes salvos"
                    )
                else:
                    # Salvar HTML pra debug se nao achou lotes
                    self._save_debug_html(sale, html)
                    self.logger.warning(
                        f"VAZIO {sale['county_codigo']} {sale['sale_date']}: "
                        f"0 lotes (HTML salvo em data/debug/)"
                    )
            except Exception as e:
                self.errors_count += 1
                self.logger.warning(
                    f"FALHA {sale['county_codigo']} {sale['sale_date']}: {e}"
                )

    def _scrape_sale(self, sale):
        """Retorna (lots, html_response_text)."""
        url_sales = sale["url_sales"] or ""
        # Detectar plataforma
        if any(p in url_sales.lower() for p in
               ["realtaxdeed.com", "realauction.com", "realforeclose.com", "realtda.com"]):
            return self._scrape_realauction(sale)
        # Outras plataformas nao implementadas
        self.logger.warning(
            f"{sale['county_codigo']}: plataforma nao suportada ({url_sales})"
        )
        return [], ""

    def _scrape_realauction(self, sale):
        """Estrategia ALB: extrai IDs do <div id="ALB"> e busca cada lote individual."""
        import time
        parsed = urlparse(sale["url_sales"])
        base = f"{parsed.scheme}://{parsed.netloc}"
        sale_dt = datetime.strptime(sale["sale_date"], "%Y-%m-%d").date()
        preview_url = (f"{base}/index.cfm?zaction=AUCTION&Zmethod=PREVIEW"
                       f"&AUCTIONDATE={sale_dt.strftime('%m/%d/%Y')}")
        self.logger.info(f"{sale['county_codigo']} preview {preview_url}")
        resp = fetch(preview_url, timeout=20)
        preview_html = resp.text

        # Extrai IDs do ALB (Auction Lot Batch - lista escondida no HTML)
        soup = BeautifulSoup(preview_html, "lxml")
        alb_div = soup.find("div", id="ALB")
        if not alb_div:
            self.logger.info(f"{sale['county_codigo']} {sale['sale_date']}: sem div ALB")
            return [], preview_html
        alb_text = alb_div.get_text(strip=True)
        alb_ids = [x.strip() for x in alb_text.split(",")
                   if x.strip() and x.strip().isdigit()]
        if not alb_ids:
            self.logger.info(f"{sale['county_codigo']} {sale['sale_date']}: ALB vazio")
            return [], preview_html

        self.logger.info(
            f"{sale['county_codigo']} {sale['sale_date']}: {len(alb_ids)} IDs no ALB"
        )

        # Tentar varios endpoints conhecidos pra cada ID
        endpoints = [
            "/index.cfm?zaction=AUCTION&Zmethod=DETAILS&AUCTIONID={id}",
            "/index.cfm?zaction=AUCTION&Zmethod=DETAIL&AUCTIONID={id}",
        ]
        # Detectar endpoint correto com 1 ID
        first_id = alb_ids[0]
        working_endpoint = None
        for ep in endpoints:
            try:
                test_url = base + ep.format(id=first_id)
                t_resp = fetch(test_url, timeout=15)
                if t_resp.status_code == 200 and len(t_resp.text) > 500:
                    working_endpoint = ep
                    self.logger.info(f"{sale['county_codigo']}: endpoint OK = {ep}")
                    break
            except Exception:
                continue
        if not working_endpoint:
            self.logger.warning(
                f"{sale['county_codigo']}: nenhum endpoint DETAILS funcionou"
            )
            return [], preview_html

        # Buscar todos os lotes
        lots = []
        for i, aid in enumerate(alb_ids):
            try:
                d_url = base + working_endpoint.format(id=aid)
                d_resp = fetch(d_url, timeout=15)
                lot = self._parse_auction_detail(d_resp.text, aid)
                if lot.get("parcel_id"):
                    lots.append(lot)
            except Exception as e:
                self.logger.warning(f"Falha lot {aid}: {e}")
            if (i + 1) % 10 == 0:
                time.sleep(0.5)  # delay leve a cada 10
        return lots, preview_html

    def _parse_auction_detail(self, html, auction_id):
        """Extrai dados de uma pagina de detalhes individual (Realauction)."""
        soup = BeautifulSoup(html, "lxml")
        lot = {"raw_data_json": json.dumps({"auction_id": auction_id})[:1500]}
        text = soup.get_text(" ", strip=True)

        # Regex para campos comuns (varias variacoes)
        patterns = {
            "parcel_id": r"Parcel(?:\s*ID|\s*Number|\s*#)?\s*[:\-]?\s*([A-Z0-9\-\.]+)",
            "case_num": r"Case(?:\s*Number|\s*#)?\s*[:\-]?\s*([A-Z0-9\-]+)",
            "min_bid": r"(?:Opening|Min(?:imum)?)\s*Bid\s*[:\-]?\s*\$?\s*([0-9,\.]+)",
            "address": r"(?:Property|Situs)\s*Address\s*[:\-]?\s*([^\n\|]+?)(?:\s{2}|$)",
            "assessed_value": r"Assessed\s*Value\s*[:\-]?\s*\$?\s*([0-9,\.]+)",
            "just_value": r"(?:Just|Market)\s*Value\s*[:\-]?\s*\$?\s*([0-9,\.]+)",
            "legal_description": r"Legal\s*Description\s*[:\-]?\s*([^\n\|]{10,200})",
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.I)
            if m:
                v = m.group(1).strip()
                if key in ("min_bid", "assessed_value", "just_value"):
                    lot[key] = _to_float(v)
                else:
                    lot[key] = v

        # Tentar tabelas (estrutura label/value)
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(cells) >= 2:
                    label = cells[0].lower()
                    value = cells[1]
                    if "parcel" in label and not lot.get("parcel_id"):
                        lot["parcel_id"] = value
                    elif "case" in label and not lot.get("case_num"):
                        lot["case_num"] = value
                    elif "address" in label and not lot.get("address"):
                        lot["address"] = value
                    elif ("opening" in label or "min" in label) and not lot.get("min_bid"):
                        lot["min_bid"] = _to_float(value)
                    elif "assessed" in label and not lot.get("assessed_value"):
                        lot["assessed_value"] = _to_float(value)
                    elif ("just" in label or "market" in label) and not lot.get("just_value"):
                        lot["just_value"] = _to_float(value)
                    elif "legal" in label and not lot.get("legal_description"):
                        lot["legal_description"] = value

        if not lot.get("parcel_id"):
            # Fallback: usar auction_id como parcel
            lot["parcel_id"] = f"AID_{auction_id}"

        return lot

    def _parse_realauction_html(self, html):
        """Extrai lotes de HTML RealAuction.

        Estrategias:
        1. Procurar tabelas com cabecalhos de Parcel/Bid
        2. Procurar arrays JSON embedded
        3. Procurar divs/li com classes especificas (auctionItem, etc)
        """
        soup = BeautifulSoup(html, "lxml")
        lots = []

        # Verificar se a area principal esta vazia (sem sale real)
        main_div = soup.find("div", attrs={"role": "main"})
        if main_div and not main_div.get_text(strip=True):
            return []  # Sem conteudo, sem sale

        # Estrategia 1: tabelas
        for table in soup.find_all("table"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            if not headers:
                continue
            if any("parcel" in h or "case" in h or "opening" in h or "bid" in h or "min" in h
                   for h in headers):
                for tr in table.find_all("tr")[1:]:
                    cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                    if not cells:
                        continue
                    lot = self._extract_lot_fields(headers, cells)
                    if lot.get("parcel_id"):
                        lots.append(lot)

        if lots:
            return lots

        # Estrategia 2: blocos com classes "auctionItem", "AuctionDetail"
        for cls in ["AUCTION_DETAILS", "auctionItem", "auctionRow", "AuctionTbl",
                    "AuctionListRow", "AdvancedAuctionItem"]:
            for div in soup.find_all(class_=cls):
                lot = self._extract_lot_from_div(div)
                if lot.get("parcel_id"):
                    lots.append(lot)
            if lots:
                return lots

        # Estrategia 3: arrays JSON embutidos no JS
        for script in soup.find_all("script"):
            txt = script.get_text()
            if "parcelID" in txt or "ParcelID" in txt or "auctionItem" in txt:
                try:
                    matches = re.findall(r"\[\s*\{[^\[\]]*?\}\s*\]", txt, re.DOTALL)
                    for m in matches:
                        try:
                            arr = json.loads(m)
                            for item in arr:
                                if not isinstance(item, dict):
                                    continue
                                lot = {
                                    "parcel_id": str(item.get("parcelID")
                                                     or item.get("ParcelID")
                                                     or item.get("parcel_id") or ""),
                                    "case_num": str(item.get("caseNumber")
                                                    or item.get("CaseNumber") or ""),
                                    "min_bid": _to_float(item.get("openingBid")
                                                         or item.get("OpeningBid")
                                                         or item.get("minBid")),
                                    "address": item.get("situsAddress")
                                              or item.get("SitusAddress")
                                              or item.get("address") or "",
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
            elif "opening" in h or "minimum bid" in h or "min bid" in h or h == "bid":
                lot["min_bid"] = _to_float(v)
            elif "address" in h or "situs" in h or "property" in h:
                lot["address"] = v
            elif "city" in h:
                lot["city"] = v
            elif "assessed" in h:
                lot["assessed_value"] = _to_float(v)
            elif "just" in h or "market" in h:
                lot["just_value"] = _to_float(v)
            elif "legal" in h:
                lot["legal_description"] = v
        lot["raw_data_json"] = json.dumps(dict(zip(headers, cells)))
        return lot

    def _extract_lot_from_div(self, div):
        """Extrai lot de bloco div generico (heuristica)."""
        lot = {"raw_data_json": str(div)[:2000]}
        text = div.get_text(" ", strip=True)
        m = re.search(r"Parcel(?:\s*ID)?[:\s]*([A-Z0-9\-\.]+)", text, re.I)
        if m:
            lot["parcel_id"] = m.group(1)
        m = re.search(r"Case(?:\s*Number)?[:\s]*([A-Z0-9\-]+)", text, re.I)
        if m:
            lot["case_num"] = m.group(1)
        m = re.search(r"(?:Opening|Min(?:imum)?)\s*Bid[:\s]*\$?\s*([0-9,\.]+)", text, re.I)
        if m:
            lot["min_bid"] = _to_float(m.group(1))
        return lot

    def _save_debug_html(self, sale, html):
        if not html:
            return
        try:
            base = "/app/data/debug" if os.path.isdir("/app") else "./data/debug"
            os.makedirs(base, exist_ok=True)
            sale_dt = datetime.strptime(sale["sale_date"], "%Y-%m-%d").date()
            path = f"{base}/{sale['county_codigo'].lower()}_{sale_dt.strftime('%Y%m%d')}.html"
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            # Tambem extrair body pra log facilitar diagnostico
            soup = BeautifulSoup(html, "lxml")
            main = soup.find("div", attrs={"role": "main"})
            body_preview = (main.get_text(" ", strip=True)[:500]
                           if main else "(sem div role=main)")
            self.logger.info(
                f"DEBUG {sale['county_codigo']} {sale['sale_date']}: "
                f"main_text={body_preview!r}"
            )
        except Exception as e:
            self.logger.warning(f"Falha salvando debug HTML: {e}")

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
            cur.execute(
                "UPDATE sales SET total_lots = (SELECT COUNT(*) FROM lots WHERE sale_id = ?) "
                "WHERE id = ?", (sale_id, sale_id)
            )


def _to_float(v):
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace("$", "").replace(",", "").strip() or 0)
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else None
    LotListScraper(county_code=code).run()
