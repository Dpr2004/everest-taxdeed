"""Lot Scraper v2 - Playwright autenticado no RealAuction.

Substitui o lot_list_scraper.py antigo. Usa login real (REALAUCTION_USER/PASS)
e scraper de dados de cada sale via pagina DAYLIST autenticada.

Extrai por cada sale futura:
- parcel_id, case_num, tax_cert_num
- min_bid (opening bid)
- address, city, zip
- assessed_value
- auction_id (ID interno Realauction pra cross-ref)

Env vars necessarias:
- REALAUCTION_USER
- REALAUCTION_PASS

Env vars opcionais:
- PLAYWRIGHT_HEADLESS=true (default false local, true em CI)
- PLAYWRIGHT_CHANNEL=chrome|msedge (default chrome)
- PLAYWRIGHT_PROFILE=<dir> (default data/browser_profile/)
"""
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from src.db.connection import cursor
from src.workers.base import BaseWorker

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


STEALTH_JS = """
Object.defineProperty(navigator,'webdriver',{get:()=>undefined});
Object.defineProperty(navigator,'plugins',{get:()=>[1,2,3,4,5]});
Object.defineProperty(navigator,'languages',{get:()=>['en-US','en']});
window.chrome={runtime:{}};
const origQuery=window.navigator.permissions.query;
window.navigator.permissions.query=(p)=>(
    p.name==='notifications'
        ?Promise.resolve({state:Notification.permission})
        :origQuery(p)
);
"""


def _to_float(s):
    if s is None:
        return None
    s = str(s).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


class LotScraperPlaywright(BaseWorker):
    name = "lot_scraper_playwright"

    def __init__(self, county_code=None):
        super().__init__()
        self.county_code = county_code
        self.user = os.environ.get("REALAUCTION_USER", "")
        self.password = os.environ.get("REALAUCTION_PASS", "")

    def execute(self):
        if sync_playwright is None:
            self.logger.error("playwright nao instalado. Roda: pip install playwright && playwright install chromium")
            self.errors_count += 1
            return

        if not self.user or not self.password:
            self.logger.error("REALAUCTION_USER ou REALAUCTION_PASS nao setados")
            self.errors_count += 1
            return

        with cursor() as cur:
            q = """
                SELECT s.id, s.sale_date, c.codigo, c.url_sales, c.nome
                FROM sales s JOIN counties c ON c.id = s.county_id
                WHERE s.status = 'scheduled'
                  AND s.sale_date >= DATE('now')
            """
            params = []
            if self.county_code:
                q += " AND c.codigo = ?"
                params.append(self.county_code)
            q += " ORDER BY s.sale_date ASC"
            cur.execute(q, params)
            sales = cur.fetchall()

        if not sales:
            self.logger.info("Nenhum sale futuro pra scrape")
            return

        self.logger.info(f"Processando {len(sales)} sales com Playwright")

        profile_dir = Path(os.environ.get(
            "PLAYWRIGHT_PROFILE",
            str(Path(__file__).parent.parent.parent / "data" / "browser_profile")
        ))
        profile_dir.mkdir(parents=True, exist_ok=True)

        headless = os.environ.get("PLAYWRIGHT_HEADLESS", "false").lower() == "true"
        use_chrome_channel = os.environ.get("PLAYWRIGHT_CHANNEL", "chrome")

        with sync_playwright() as p:
            launch_kwargs = dict(
                user_data_dir=str(profile_dir),
                headless=headless,
                slow_mo=200,
                viewport={"width": 1440, "height": 900},
                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                            "Chrome/131.0.0.0 Safari/537.36"),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                ],
                ignore_default_args=["--enable-automation"],
            )
            if use_chrome_channel:
                launch_kwargs["channel"] = use_chrome_channel

            try:
                ctx = p.chromium.launch_persistent_context(**launch_kwargs)
            except Exception as e:
                self.logger.warning(f"Chrome channel falhou ({e}), usando chromium padrao")
                launch_kwargs.pop("channel", None)
                ctx = p.chromium.launch_persistent_context(**launch_kwargs)

            ctx.add_init_script(STEALTH_JS)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            last_domain = None
            for sale in sales:
                try:
                    self._scrape_sale(page, sale, last_domain)
                    last_domain = urlparse(sale["url_sales"]).netloc
                except Exception as e:
                    self.errors_count += 1
                    self.logger.warning(
                        f"FALHA {sale['codigo']} {sale['sale_date']}: {e}"
                    )

            ctx.close()

    def _scrape_sale(self, page, sale, last_domain):
        url_sales = sale["url_sales"] or ""
        if not url_sales:
            self.logger.info(f"{sale['codigo']}: sem url_sales")
            return

        parsed = urlparse(url_sales)
        base = f"{parsed.scheme}://{parsed.netloc}"
        if parsed.netloc != last_domain:
            self._login(page, base)
            # Se login falhou (credencial nao tem acesso a esse subdomain),
            # abortar — nao adianta tentar DAYLIST sem auth.
            try:
                auth_fail = page.evaluate("() => window.__LOTES_AUTH_FAIL || false")
                if auth_fail:
                    self.logger.warning(
                        f"AUTH_FAIL {sale['codigo']} ({base}): registrar conta "
                        f"em {parsed.netloc} usando REALAUCTION_USER. "
                        f"Pulando sale {sale['sale_date']}."
                    )
                    # Conta como erro pra saude refletir como worker degraded
                    self.errors_count += 1
                    return
            except Exception:
                pass

        sale_dt = datetime.strptime(sale["sale_date"], "%Y-%m-%d")
        date_mmddyyyy = sale_dt.strftime("%m/%d/%Y")
        daylist_url = f"{base}/index.cfm?ZACTION=AUCTION&ZMETHOD=DAYLIST&AUCTIONDATE={date_mmddyyyy}"

        self.logger.info(f"{sale['codigo']} {sale['sale_date']}: fetching {daylist_url}")
        last_err = None
        for attempt in range(3):
            try:
                page.goto(daylist_url, wait_until="domcontentloaded", timeout=20000)
                last_err = None
                break
            except Exception as e:
                last_err = e
                self.logger.info(f"  tentativa {attempt+1} falhou: {e}")
                page.wait_for_timeout(2500)
        if last_err:
            raise last_err
        self._dismiss_notice(page)

        # Aguarda AJAX terminar (auctions sao populados async apos login).
        # Sem isso, ~6 condados (Putnam, Citrus, Duval, Alachua, Hillsborough, Flagler)
        # retornavam 0 lotes apesar de TEREM lotes — selector batia em pagina pre-AJAX.
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass  # alguns sites tem polling longo, ignora
        try:
            page.wait_for_selector("#Area_W .AUCTION_ITEM, #Area_C .AUCTION_ITEM, #Area_R .AUCTION_ITEM",
                                   timeout=15000)
        except Exception:
            self.logger.info(f"{sale['codigo']} {sale['sale_date']}: nenhuma AUCTION_ITEM carregou em ~30s")
        page.wait_for_timeout(2000)

        locators = [
            ("W", page.locator("#Area_W .AUCTION_ITEM")),
            ("R", page.locator("#Area_R .AUCTION_ITEM")),
        ]
        total_found = sum(lcr.count() for _, lcr in locators)
        self.logger.info(f"{sale['codigo']} {sale['sale_date']}: {total_found} lotes em Area W+R")

        if total_found == 0:
            return
        _, area_w = max(locators, key=lambda t: t[1].count())
        count = area_w.count()

        lots = []
        for i in range(count):
            item = area_w.nth(i)
            aid = item.get_attribute("aid")
            try:
                rows = item.locator("table.ad_tab tr").all()
            except Exception:
                continue

            lot = {"raw_data_json": json.dumps({"auction_id": aid})}
            address_parts = []
            for row in rows:
                try:
                    lbl = row.locator("td.AD_LBL").inner_text(timeout=1000).strip().lower()
                    val = row.locator("td.AD_DTA").inner_text(timeout=1000).strip()
                except Exception:
                    continue
                # Parser tolerante a variacoes de label entre condados:
                # RealTaxDeed (highlands, polk, marion, etc) usa labels padrao.
                # RealForeclose (brevard, pasco, volusia, hernando) pode usar
                # 'starting bid', 'minimum bid', 'amount due', 'judgment amount'.
                # Pasco/Volusia tinham 0 bid no diagnostico — provavel label diff.
                if "parcel" in lbl or "folio" in lbl:
                    lot["parcel_id"] = val
                elif "case" in lbl:
                    lot["case_num"] = val
                elif "certificate" in lbl:
                    lot["tax_cert_num"] = val
                elif (lbl.startswith("opening") or lbl.startswith("starting") or
                      lbl.startswith("minimum") or lbl.startswith("min ") or lbl == "min" or
                      "opening bid" in lbl or "starting bid" in lbl or "minimum bid" in lbl or
                      "judgment amount" in lbl or "amount due" in lbl or
                      "tax amount" in lbl or lbl == "bid:"):
                    if not lot.get("min_bid"):  # primeiro match ganha
                        lot["min_bid"] = _to_float(val)
                elif "property address" in lbl or lbl == "address:" or lbl.startswith("address"):
                    address_parts.append(val)
                elif lbl == "" and val:
                    address_parts.append(val)
                elif "assessed" in lbl:
                    lot["assessed_value"] = _to_float(val)
                elif ("just" in lbl or "market value" in lbl or lbl == "market" or
                      "estimated value" in lbl or "appraised" in lbl or
                      "total value" in lbl):
                    lot["just_value"] = _to_float(val)

            if address_parts:
                lot["address"] = address_parts[0]
                if len(address_parts) > 1:
                    city_state = address_parts[1]
                    m = re.match(r"([^,]+),\s*FL[-\s]*(\d{5})", city_state)
                    if m:
                        lot["city"] = m.group(1).strip()
                        lot["zip"] = m.group(2)
                    else:
                        lot["city"] = city_state

            if lot.get("parcel_id"):
                lots.append(lot)

        if lots:
            self._save_lots(sale["id"], lots)
            self.items_processed += len(lots)
            self.logger.info(
                f"OK {sale['codigo']} {sale['sale_date']}: {len(lots)} lotes salvos"
            )

    def _login(self, page, base):
        """Faz login se necessario. Profile persistente pode ja estar logado.

        Importante: cada subdomain RealAuction (alachua, osceola, etc) e
        CONTA INDEPENDENTE. Mesma credential pode nao estar registrada em
        todos. Pos-login, verificar se realmente entrou — se cair em
        Splash Page novamente, marcar como FALHA AUTH (nao silencioso).
        """
        page.goto(f"{base}/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)
        login_attempted = False
        try:
            if page.locator("#LogName").is_visible(timeout=3000):
                self.logger.info(f"Logando em {base}")
                page.fill("#LogName", self.user)
                page.fill("#LogPass", self.password)
                page.click("#LogButton")
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(3000)
                login_attempted = True
            else:
                self.logger.info(f"Ja logado em {base} (profile persistente)")
        except Exception as e:
            self.logger.warning(f"Login erro em {base}: {e}")

        # VERIFICACAO: depois do login, confere se REALMENTE entrou.
        # Splash page persistente = credencial nao tem acesso nesse subdomain.
        try:
            still_splash = page.locator("#LogName").is_visible(timeout=2000)
            if still_splash:
                # Login falhou — credencial Everest2026 nao tem acesso
                self.logger.warning(
                    f"AUTH_FAIL em {base}: credencial nao tem acesso a esse subdomain. "
                    f"Provavel: registro RealAuction necessario por condado. "
                    f"Lots desse condado nao serao scrapeados."
                )
                # Marca pra _scrape_sale verificar e abortar o sale
                page.evaluate("() => { window.__LOTES_AUTH_FAIL = true; }")
        except Exception:
            pass

        self._dismiss_notice(page)
        page.wait_for_timeout(1500)

    def _dismiss_notice(self, page, max_tries=3):
        for _ in range(max_tries):
            try:
                btn = page.locator("#BNOTACC")
                if btn.is_visible(timeout=1500):
                    btn.click()
                    page.wait_for_load_state("networkidle", timeout=6000)
                    continue
            except Exception:
                pass
            break

    def _save_lots(self, sale_id, lots):
        with cursor() as cur:
            for lot in lots:
                raw = lot.get("raw_data_json") or "{}"
                cur.execute("""
                    INSERT INTO lots (sale_id, tax_cert_num, case_num, parcel_id,
                                      address, city, zip, min_bid, assessed_value,
                                      just_value, raw_data_json, scraped_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(sale_id, parcel_id) DO UPDATE SET
                        tax_cert_num = excluded.tax_cert_num,
                        case_num = excluded.case_num,
                        address = excluded.address,
                        city = excluded.city,
                        zip = excluded.zip,
                        min_bid = excluded.min_bid,
                        assessed_value = excluded.assessed_value,
                        just_value = excluded.just_value,
                        raw_data_json = excluded.raw_data_json,
                        scraped_at = CURRENT_TIMESTAMP
                """, (
                    sale_id,
                    lot.get("tax_cert_num"),
                    lot.get("case_num"),
                    lot.get("parcel_id"),
                    lot.get("address"),
                    lot.get("city"),
                    lot.get("zip"),
                    lot.get("min_bid"),
                    lot.get("assessed_value"),
                    lot.get("just_value"),
                    raw,
                ))


if __name__ == "__main__":
    code = None
    if len(sys.argv) > 1:
        code = sys.argv[1]
    LotScraperPlaywright(county_code=code).run()
