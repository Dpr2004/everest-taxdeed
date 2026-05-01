"""Post-Auction Scraper - Fase 4 (vault integration).

Reusa a infra do lot_scraper_playwright.py (mesmo profile Chrome persistente,
mesmo login autenticado, mesmo stealth JS) para scrape de RESULTADOS pos-leilao
dos 11 condados Tier Everest.

Diferenca chave:
- lot_scraper itera 'sales' futuros da DB e extrai upcoming lots
- post_auction itera COUNTY_DOMAINS (config estatico) x datas passadas (D-1 a D-7)
  e extrai status/winner/amount de cada lot completed.

Os resultados sao enviados via webhook ao LOTES Analyzer:
  POST {LOTES_TUNNEL_URL}/api/post-auction-result
  Headers: X-API-Key: {LOTES_API_KEY}
  Body: { parcel_id, status, winner, final_amount, auction_date }

LOTES Analyzer atualiza o vault Obsidian:
- Frontmatter da nota original recebe resultado_leilao/valor_final/winner
- Se Everest ganhou (match em everest-bidder-config.json), cria portfolio/{parcel}.md

Env vars necessarias:
- REALAUCTION_USER, REALAUCTION_PASS  (mesmas do lot_scraper)
- LOTES_TUNNEL_URL                    (URL do Cloudflare Tunnel)
- LOTES_API_KEY                       (UUID 64 chars do header X-API-Key)

Env vars opcionais:
- PLAYWRIGHT_HEADLESS=true (default false local, true em CI)
- PLAYWRIGHT_CHANNEL=chrome|msedge
- PLAYWRIGHT_PROFILE=<dir>
- DRY_RUN=1 (nao envia ao LOTES, so loga)
- POST_AUCTION_DAYS_BACK=7
- POST_AUCTION_COUNTIES="Highlands Putnam"  (space-separated)
"""
import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from src.workers.base import BaseWorker

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

from src.workers.post_auction_config import (
    COUNTY_DOMAINS,
    SELECTORS,
    DAYLIST_PATH,
    normalize_status,
)

# Mesmo stealth JS do lot_scraper
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
        return 0.0
    s = str(s).replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return 0.0


class PostAuctionScraperPlaywright(BaseWorker):
    name = "post_auction_scraper_playwright"

    def __init__(self, days_back=None, counties=None, dry_run=None):
        super().__init__()
        self.user = os.environ.get("REALAUCTION_USER", "")
        self.password = os.environ.get("REALAUCTION_PASS", "")
        self.lotes_url = os.environ.get("LOTES_TUNNEL_URL", "").rstrip("/")
        self.lotes_key = os.environ.get("LOTES_API_KEY", "")

        self.days_back = days_back or int(os.environ.get("POST_AUCTION_DAYS_BACK", "7"))
        env_counties = os.environ.get("POST_AUCTION_COUNTIES", "").strip()
        if counties:
            self.counties = counties
        elif env_counties:
            self.counties = env_counties.split()
        else:
            self.counties = list(COUNTY_DOMAINS.keys())

        if dry_run is not None:
            self.dry_run = dry_run
        else:
            self.dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")

        self.scraped = 0
        self.sent_ok = 0
        self.sent_failed = 0

    def execute(self):
        if sync_playwright is None:
            self.logger.error("playwright nao instalado. Roda: pip install playwright && playwright install chromium")
            self.errors_count += 1
            return

        if not self.user or not self.password:
            self.logger.error("REALAUCTION_USER ou REALAUCTION_PASS nao setados")
            self.errors_count += 1
            return

        if not self.dry_run and (not self.lotes_url or not self.lotes_key):
            self.logger.error("LOTES_TUNNEL_URL ou LOTES_API_KEY nao setados (use DRY_RUN=1 pra testar)")
            self.errors_count += 1
            return

        today = date.today()
        dates_to_check = [today - timedelta(days=d) for d in range(1, self.days_back + 1)]

        self.logger.info(
            f"Post-auction monitor: {len(self.counties)} condados x {len(dates_to_check)} dias "
            f"(dry_run={self.dry_run})"
        )

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
                self.logger.warning(f"Falha launching com channel={use_chrome_channel}: {e}; tentando sem channel")
                launch_kwargs.pop("channel", None)
                ctx = p.chromium.launch_persistent_context(**launch_kwargs)

            ctx.add_init_script(STEALTH_JS)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            try:
                for county in self.counties:
                    self._process_county(page, county, dates_to_check)
            finally:
                ctx.close()

        self.items_processed = self.scraped
        self.logger.info(
            f"Summary: scraped={self.scraped} sent_ok={self.sent_ok} "
            f"sent_failed={self.sent_failed} errors={self.errors_count}"
        )

    def _process_county(self, page, county, dates_to_check):
        domain = COUNTY_DOMAINS.get(county)
        if not domain:
            self.logger.warning(f"Condado desconhecido: {county}")
            return

        base = f"https://{domain}"
        self.logger.info(f"=== {county} ({base}) ===")

        try:
            self._login(page, base)
        except Exception as e:
            self.logger.error(f"Login falhou em {county}: {e}")
            self.errors_count += 1
            return

        for d in dates_to_check:
            try:
                results = self._scrape_date(page, county, base, d)
                self.scraped += len(results)
                for r in results:
                    if self._send_to_lotes(r):
                        self.sent_ok += 1
                    else:
                        self.sent_failed += 1
                time.sleep(2)  # rate limit gentil
            except Exception as e:
                self.logger.warning(f"  erro em {county} {d}: {e}")
                self.errors_count += 1

    def _scrape_date(self, page, county, base, target_date):
        date_str = target_date.strftime("%m/%d/%Y")
        url = f"{base}{DAYLIST_PATH.format(date=date_str)}"
        self.logger.info(f"  GET {url}")

        try:
            page.goto(url, wait_until="networkidle", timeout=30000)
        except Exception as e:
            self.logger.warning(f"  falha goto {url}: {e}")
            return []

        try:
            page.wait_for_selector(SELECTORS["auction_item"], timeout=8000)
        except Exception:
            self.logger.info(f"  sem .AUCTION_ITEM em {county} {date_str} (provavel sem leilao nesse dia)")
            return []

        items = page.locator(SELECTORS["auction_item"]).all()
        self.logger.info(f"  {len(items)} lotes em {county} {date_str}")

        # DEBUG: dumpa HTML do primeiro item se DEBUG_DUMP_HTML setado
        if items and os.environ.get("DEBUG_DUMP_HTML"):
            try:
                html = items[0].inner_html(timeout=3000)
                self.logger.info(
                    f"  [DEBUG_DUMP_HTML] {county} {date_str} primeiro .AUCTION_ITEM:\n"
                    f"=== BEGIN HTML ===\n{html[:5000]}\n=== END HTML ==="
                )
            except Exception as e:
                self.logger.warning(f"  [DEBUG_DUMP_HTML] falhou: {e}")

        results = []
        for item in items:
            try:
                parcel_id = item.locator(SELECTORS["parcel_id"]).first.inner_text(timeout=2000).strip()
            except Exception:
                continue
            if not parcel_id:
                continue

            try:
                raw_status = item.locator(SELECTORS["status"]).first.inner_text(timeout=2000).strip()
            except Exception:
                raw_status = ""

            try:
                winner = item.locator(SELECTORS["winning_bidder"]).first.inner_text(timeout=2000).strip()
            except Exception:
                winner = ""

            try:
                amount_str = item.locator(SELECTORS["winning_amount"]).first.inner_text(timeout=2000).strip()
            except Exception:
                amount_str = ""

            results.append({
                "parcel_id":    parcel_id,
                "status":       normalize_status(raw_status),
                "winner":       winner or None,
                "final_amount": _to_float(amount_str),
                "auction_date": target_date.isoformat(),
                "_county":      county,
                "_raw_status":  raw_status,
            })

        return results

    def _send_to_lotes(self, result):
        if self.dry_run:
            self.logger.info(
                f"  [DRY-RUN] {result['parcel_id']} -> {result['status']} "
                f"(winner={result['winner']}, amount=${result['final_amount']:.2f})"
            )
            return True

        payload = {
            "parcel_id":    result["parcel_id"],
            "status":       result["status"],
            "winner":       result["winner"],
            "final_amount": result["final_amount"],
            "auction_date": result["auction_date"],
        }

        try:
            r = requests.post(
                f"{self.lotes_url}/api/post-auction-result",
                json=payload,
                headers={
                    "X-API-Key": self.lotes_key,
                    "Content-Type": "application/json",
                },
                timeout=15,
            )
            if r.status_code == 200:
                data = r.json()
                self.logger.info(
                    f"  ok {result['parcel_id']}: {result['status']} "
                    f"(vault_updated={data.get('vault_updated')})"
                )
                return True
            else:
                self.logger.warning(
                    f"  fail {result['parcel_id']}: HTTP {r.status_code} - {r.text[:200]}"
                )
                return False
        except Exception as e:
            self.logger.warning(f"  fail {result['parcel_id']}: {e}")
            return False

    # ============================================================
    # Auth helpers - MESMA logica do lot_scraper_playwright.py
    # ============================================================

    def _login(self, page, base):
        """Faz login se necessario. Profile persistente pode ja estar logado."""
        page.goto(f"{base}/", wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(1500)
        try:
            if page.locator("#LogName").is_visible(timeout=3000):
                self.logger.info(f"Logando em {base}")
                page.fill("#LogName", self.user)
                page.fill("#LogPass", self.password)
                page.click("#LogButton")
                page.wait_for_load_state("networkidle", timeout=15000)
                page.wait_for_timeout(3000)
            else:
                self.logger.info(f"Ja logado em {base} (profile persistente)")
        except Exception as e:
            self.logger.warning(f"Login erro em {base}: {e}")
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


# ============================================================
# CLI entrypoint
# ============================================================

def _parse_args():
    parser = argparse.ArgumentParser(description="Post-auction scraper (Fase 4)")
    parser.add_argument("--days", type=int, help="Dias atras pra checar (default: 7)")
    parser.add_argument("--counties", nargs="+", help="Condados (default: todos Tier Everest)")
    parser.add_argument("--dry-run", action="store_true", help="Nao envia ao LOTES")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    worker = PostAuctionScraperPlaywright(
        days_back=args.days,
        counties=args.counties,
        dry_run=args.dry_run,
    )
    worker.run()
    if worker.errors_count > 0 and worker.scraped == 0:
        sys.exit(1)
