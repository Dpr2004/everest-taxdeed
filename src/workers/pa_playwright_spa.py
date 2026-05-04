"""Property Appraiser SPA scraper via Playwright.

Resolve o gap dos PAs com SPA + hash routing onde requests/curl falham:
- HILLSBOROUGH: gis.hcpafl.org/PropertySearch (React SPA, hash routing)
- BREVARD: bcpao.us/PropertySearch (Angular SPA)
- ORANGE: ocpaweb.ocpafl.org (parcial SPA)

Usa Playwright headless (ja no requirements) pra renderizar JS, esperar
networkidle, depois extrair dados via DOM selectors especificos por condado.

Rodado APOS regrid_enricher e ANTES do property_appraiser (curl). Preenche
os gaps que urllib nao consegue.
"""
import json
import re
import time
from src.db.connection import cursor
from src.workers.base import BaseWorker

try:
    from playwright.sync_api import sync_playwright
    PLAYWRIGHT_AVAILABLE = True
except ImportError:
    PLAYWRIGHT_AVAILABLE = False


# Condados com PA SPA confirmado — tier 1 worker `property_appraiser.py`
# tem esses no _SPA_COUNTIES blocklist. Aqui nos cuidamos deles.
SPA_COUNTIES = {"HILLSBOROUGH", "BREVARD", "ORANGE"}


def _to_float(s):
    if not s:
        return None
    s = re.sub(r"[^\d.]", "", str(s))
    try:
        return float(s) if "." in s else int(s)
    except Exception:
        return None


class PAPlaywrightSPA(BaseWorker):
    name = "pa_playwright_spa"

    def __init__(self, county_code=None, limit=None):
        super().__init__()
        self.county_code = county_code
        self.limit = limit

    def execute(self):
        if not PLAYWRIGHT_AVAILABLE:
            self.logger.warning("Playwright nao disponivel — pulando PA SPA")
            return

        # Lots sem dados PA enriquecidos, em condados SPA, com sale futuro
        with cursor() as cur:
            q = """
                SELECT l.id, l.parcel_id, l.address, l.city, l.zip,
                       c.codigo AS county_code
                FROM lots l
                JOIN sales s ON s.id = l.sale_id
                JOIN counties c ON c.id = s.county_id
                WHERE s.sale_date >= DATE('now')
                  AND l.parcel_id NOT LIKE 'AID_%'
                  AND (l.assessed_value IS NULL OR l.assessed_value = 0
                       OR l.just_value IS NULL OR l.just_value = 0)
            """
            params = []
            if self.county_code:
                q += " AND c.codigo = ?"
                params.append(self.county_code.upper())
            else:
                placeholders = ",".join("?" * len(SPA_COUNTIES))
                q += f" AND c.codigo IN ({placeholders})"
                params.extend(SPA_COUNTIES)
            q += " ORDER BY s.sale_date ASC"
            if self.limit:
                q += f" LIMIT {int(self.limit)}"
            cur.execute(q, params)
            lots = cur.fetchall()

        self.logger.info(f"PA SPA enrich: {len(lots)} lots elegiveis")
        if not lots:
            return

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
            )
            page = ctx.new_page()

            for lot in lots:
                try:
                    handler = getattr(self, f"_enrich_{lot['county_code'].lower()}", None)
                    if not handler:
                        continue
                    data = handler(page, lot)
                    if data:
                        self._save(lot["id"], data)
                        self.items_processed += 1
                        self.logger.info(
                            f"PA SPA OK {lot['county_code']} {lot['parcel_id']}: "
                            f"{', '.join(f'{k}={v}' for k,v in data.items() if v)[:120]}"
                        )
                except Exception as e:
                    self.errors_count += 1
                    self.logger.warning(f"PA SPA falha {lot['parcel_id']}: {e}")

            browser.close()

    def _save(self, lot_id, data):
        fields = []
        values = []
        for k in ("assessed_value", "just_value", "building_sqft",
                  "year_built", "lot_sqft", "zoning", "property_type",
                  "address", "city", "zip"):
            if k in data and data[k] is not None and data[k] != "":
                fields.append(f"{k} = ?")
                values.append(data[k])
        if not fields:
            return
        with cursor() as cur:
            cur.execute(
                f"UPDATE lots SET {', '.join(fields)} WHERE id = ?",
                values + [lot_id]
            )

    # ============================================================
    # HILLSBOROUGH — gis.hcpafl.org/PropertySearch
    # ============================================================
    def _enrich_hillsborough(self, page, lot):
        folio = lot["parcel_id"].replace("-", "").replace(".", "")
        url = f"https://gis.hcpafl.org/PropertySearch/#/folio/{folio}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            return None

        # SPA precisa de tempo pra renderizar
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        # Tenta extrair via JS direto do app state (Hillsborough usa React)
        try:
            text = page.evaluate("() => document.body.innerText")
        except Exception:
            return None

        data = {}
        # Patterns observados no PropertySearch render
        patterns = {
            "assessed_value": r"Total\s*Assessed\s*Value[:\s]*\$?([\d,]+)",
            "just_value": r"(?:Just|Market)\s*Value[:\s]*\$?([\d,]+)",
            "building_sqft": r"(?:Heated|Living|Total)\s*Area[:\s]*([\d,]+)",
            "year_built": r"Year\s*Built[:\s]*(\d{4})",
            "lot_sqft": r"Lot\s*Size[:\s]*([\d,]+)\s*(?:sf|sqft|sq\s*ft)",
            "zoning": r"Zoning[:\s]*([A-Z0-9\-]+)",
            "property_type": r"(?:Property\s*Use|Land\s*Use)[:\s]*([A-Z0-9\-\s]+)",
        }
        for field, pat in patterns.items():
            m = re.search(pat, text, re.I)
            if m:
                v = m.group(1).replace(",", "").strip()
                if field in ("zoning", "property_type"):
                    data[field] = v[:30]
                else:
                    data[field] = _to_float(v)
        return data if data else None

    # ============================================================
    # BREVARD — bcpao.us/PropertySearch
    # ============================================================
    def _enrich_brevard(self, page, lot):
        # Brevard usa "ParcelID" sem hyphens
        parcel = lot["parcel_id"].replace("-", "").replace(".", "")
        url = f"https://www.bcpao.us/PropertySearch/#/parcel/{parcel}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            return None
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

        try:
            text = page.evaluate("() => document.body.innerText")
        except Exception:
            return None

        data = {}
        patterns = {
            "assessed_value": r"Total\s*Assessed[:\s]*\$?([\d,]+)",
            "just_value": r"(?:Just|Market)[:\s]*\$?([\d,]+)",
            "building_sqft": r"(?:Living|Heated|Total\s*Area)[:\s]*([\d,]+)",
            "year_built": r"Year\s*Built[:\s]*(\d{4})",
            "lot_sqft": r"(?:Land\s*Area|Lot\s*Size)[:\s]*([\d,]+)\s*(?:sf|sqft)",
            "zoning": r"Zoning[:\s]*([A-Z0-9\-]+)",
            "property_type": r"(?:Property\s*Use|Use\s*Code)[:\s]*([A-Z0-9\-\s]+)",
        }
        for field, pat in patterns.items():
            m = re.search(pat, text, re.I)
            if m:
                v = m.group(1).replace(",", "").strip()
                if field in ("zoning", "property_type"):
                    data[field] = v[:30]
                else:
                    data[field] = _to_float(v)
        return data if data else None

    # ============================================================
    # ORANGE — ocpaweb.ocpafl.org/parcelsearch
    # ============================================================
    def _enrich_orange(self, page, lot):
        parcel = lot["parcel_id"]
        url = f"https://ocpaweb.ocpafl.org/parcelsearch/?parcel={parcel}"
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=20000)
        except Exception:
            return None
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(2500)

        try:
            text = page.evaluate("() => document.body.innerText")
        except Exception:
            return None

        data = {}
        patterns = {
            "assessed_value": r"Total\s*Assessed[:\s]*\$?([\d,]+)",
            "just_value": r"(?:Just|Market)[:\s]*\$?([\d,]+)",
            "building_sqft": r"(?:Living|Heated|Total)\s*Area[:\s]*([\d,]+)",
            "year_built": r"Year\s*Built[:\s]*(\d{4})",
            "lot_sqft": r"(?:Land\s*Sq\s*Ft|Lot\s*Size)[:\s]*([\d,]+)",
            "zoning": r"Zoning[:\s]*([A-Z0-9\-]+)",
        }
        for field, pat in patterns.items():
            m = re.search(pat, text, re.I)
            if m:
                v = m.group(1).replace(",", "").strip()
                if field in ("zoning", "property_type"):
                    data[field] = v[:30]
                else:
                    data[field] = _to_float(v)
        return data if data else None


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else None
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    PAPlaywrightSPA(county_code=code, limit=limit).run()
