"""Property Appraiser Enricher - busca dados do PA de cada condado.

19 condados Tier Everest cobertos com URLs validadas:

Tier original (11):
- HIGHLANDS: hcpao.org
- POLK: polkpa.org
- LEE: leepa.org
- ORANGE: ocpaweb.ocpafl.org
- MARION: pa.marion.fl.us
- LAKE: lakecopropappr.com
- OSCEOLA: ira.property-appraiser.org
- PUTNAM: pa.putnam-fl.com
- ST_LUCIE: paslc.gov
- BREVARD: bcpao.us
- CITRUS: pa.citrus.fl.us

Expansao Centro/Costa Atl. + Norte (8):
- HILLSBOROUGH: hcpafl.org
- PASCO: pascopa.com
- HERNANDO: hernandocountypa.com
- VOLUSIA: vcpa.vcgov.org
- FLAGLER: flaglerpa.com
- ALACHUA: acpafl.org
- DUVAL: paopropertysearch.coj.net
- LEVY: levypa.com

Cada PA tem layout proprio. Tentativa de search + parse_generic com regex.
Se site bloqueia/falha: retorna None silencioso, Regrid eh fallback.
"""
import json
import os
import re
import time
from bs4 import BeautifulSoup
from src.db.connection import cursor
from src.workers.base import BaseWorker
from src.utils.http import fetch


# Counties cujo PA roda em SPA com hash routing (React/Angular).
# requests.get retorna shell vazio -> regex nunca acha nada e queima 30s+ por lote.
# Migrar pra Playwright quando priorizado. Por enquanto: skip silencioso.
_SPA_COUNTIES = {"hillsborough", "brevard"}


class PropertyAppraiser(BaseWorker):
    name = "property_appraiser"

    def __init__(self, county_code=None, limit=None):
        super().__init__()
        self.county_code = county_code
        # CLI --limit > env PA_LIMIT_PER_RUN > sem limite
        if limit is None:
            env_limit = os.environ.get("PA_LIMIT_PER_RUN")
            limit = int(env_limit) if env_limit and env_limit.isdigit() else None
        self.limit = limit
        # Kill switch global: tempo total maximo do worker em segundos
        self.time_budget_sec = int(os.environ.get("PA_TIME_BUDGET_SEC", "900"))

    def execute(self):
        # Busca lots que ainda nao foram enriquecidos (sem sqft/year_built)
        with cursor() as cur:
            q = """
                SELECT l.id, l.parcel_id, l.address, l.city, c.codigo AS county_codigo
                FROM lots l
                JOIN sales s ON s.id = l.sale_id
                JOIN counties c ON c.id = s.county_id
                WHERE s.sale_date >= DATE('now')
                  AND (l.building_sqft IS NULL OR l.year_built IS NULL)
            """
            params = []
            if self.county_code:
                q += " AND c.codigo = ?"
                params.append(self.county_code)
            q += " ORDER BY l.scraped_at DESC"
            if self.limit:
                q += f" LIMIT {int(self.limit)}"
            cur.execute(q, params)
            lots = cur.fetchall()

        self.candidates_count = len(lots)
        self.logger.info(
            f"PA enrich: {len(lots)} lots para processar "
            f"(limit={self.limit}, time_budget={self.time_budget_sec}s)"
        )

        started = time.monotonic()
        spa_skipped = 0
        no_method = 0
        none_returned = 0  # parser retornou None (regex nao casou) — silent failure
        from collections import Counter
        none_por_county = Counter()
        for lot in lots:
            elapsed = time.monotonic() - started
            if elapsed >= self.time_budget_sec:
                self.logger.warning(
                    f"PA kill switch: tempo esgotado ({elapsed:.0f}s >= "
                    f"{self.time_budget_sec}s). Processados {self.items_processed}, "
                    f"restavam {len(lots) - self.items_processed - self.errors_count - spa_skipped}."
                )
                break
            county_lc = (lot["county_codigo"] or "").lower()
            if county_lc in _SPA_COUNTIES:
                spa_skipped += 1
                continue
            try:
                method = getattr(self, f"_enrich_{county_lc}", None)
                if not method:
                    no_method += 1
                    self.logger.debug(f"Sem enricher pra {lot['county_codigo']}")
                    continue
                data = method(lot)
                if data:
                    self._save(lot["id"], data)
                    self.items_processed += 1
                else:
                    none_returned += 1
                    none_por_county[county_lc] += 1
            except Exception as e:
                self.errors_count += 1
                self.logger.warning(f"PA falha {lot['parcel_id']}: {e}")

        if spa_skipped:
            self.logger.info(
                f"PA SPA skip: {spa_skipped} lotes em condados SPA "
                f"({sorted(_SPA_COUNTIES)}) - precisam Playwright"
            )
        if no_method:
            self.logger.warning(f"PA sem enricher implementado: {no_method} lotes")
        # Silent failure surfacing — most insidious bug do PA enricher
        if none_returned:
            top = ", ".join(f"{c}={n}" for c,n in none_por_county.most_common(5))
            self.logger.error(
                f"PA SILENT FAIL: {none_returned} lotes parser retornou None "
                f"(site bloqueando, regex nao casou, layout mudou). "
                f"Top 5 condados afetados: {top}"
            )
            # Conta como erros pra base.run() detectar degraded
            self.errors_count += none_returned

    def _save(self, lot_id, data):
        fields = []
        values = []
        for k, v in data.items():
            if v is not None and k in ("building_sqft", "lot_sqft", "year_built",
                                         "bedrooms", "bathrooms", "zoning",
                                         "property_type", "address", "city", "zip",
                                         "just_value", "assessed_value"):
                fields.append(f"{k} = ?")
                values.append(v)
        if not fields:
            return
        with cursor() as cur:
            cur.execute(f"UPDATE lots SET {', '.join(fields)} WHERE id = ?",
                        values + [lot_id])

    # ========== HIGHLANDS — hcpao.org ==========
    def _enrich_highlands(self, lot):
        """Highlands Property Appraiser."""
        if not lot["parcel_id"]:
            return None
        parcel = lot["parcel_id"].replace("-", "").replace(".", "")
        url = f"https://www.hcpao.org/search/parcel/{parcel}"
        try:
            resp = fetch(url, timeout=15)
        except Exception:
            return None
        soup = BeautifulSoup(resp.text, "lxml")
        text = soup.get_text(" ", strip=True)
        data = {}
        # Heuristica de campos
        for label, field, is_num in [
            ("Year Built", "year_built", True),
            ("Living Area", "building_sqft", True),
            ("Total Area", "building_sqft", True),
            ("Heated Area", "building_sqft", True),
            ("Lot Size", "lot_sqft", True),
            ("Acres", "lot_sqft", False),
            ("Zoning", "zoning", False),
            ("Property Use", "property_type", False),
            ("Bedrooms", "bedrooms", True),
            ("Bathrooms", "bathrooms", True),
            ("Just Market Value", "just_value", True),
            ("Assessed Value", "assessed_value", True),
        ]:
            m = re.search(rf"{label}\s*[:\-]?\s*([\w\$\.,\s]+?)(?:\s{{2}}|$)", text, re.I)
            if m:
                v = m.group(1).strip()
                if is_num:
                    v = re.sub(r"[^\d.]", "", v)
                    try:
                        data[field] = float(v) if "." in v else int(v)
                    except ValueError:
                        pass
                else:
                    data[field] = v[:80]
        return data if data else None

    # ========== POLK — polkpa.org ==========
    def _enrich_polk(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://www.polkpa.org/CamaDisplay.aspx?OutputMode=Display&SearchType=RealEstate&Search={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
        except Exception:
            return None
        # Polk tem estrutura similar. Heuristica igual.
        return self._parse_generic_pa(resp.text)

    # ========== MARION — pa.marion.fl.us ==========
    def _enrich_marion(self, lot):
        """Marion PA — tenta search por parcel ID."""
        if not lot["parcel_id"]:
            return None
        url = f"https://www.pa.marion.fl.us/PropertySearch.aspx?Parcel={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== LAKE — lakecopropappr.com ==========
    def _enrich_lake(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://www.lakecopropappr.com/property-details.aspx?AltKey={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== OSCEOLA — property-appraiser.org ==========
    def _enrich_osceola(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://ira.property-appraiser.org/PropertyDetail.aspx?ParcelID={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== PUTNAM — pa.putnam-fl.com ==========
    def _enrich_putnam(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"http://pa.putnam-fl.com/GIS/D_SearchResults.asp?txtFiltro={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== ST_LUCIE — paslc.gov ==========
    def _enrich_st_lucie(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://www.paslc.gov/searchPropertyDetail.cfm?parcel={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== BREVARD — bcpao.us ==========
    def _enrich_brevard(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://www.bcpao.us/PropertySearch/#/parcel/{lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== CITRUS — pa.citrus.fl.us ==========
    def _enrich_citrus(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://www.pa.citrus.fl.us/Search.aspx?Q={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== HILLSBOROUGH — hcpafl.org ==========
    def _enrich_hillsborough(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://gis.hcpafl.org/propertysearch/#/nav/Search?folio={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== PASCO — pascopa.com ==========
    def _enrich_pasco(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://search.pascopa.com/search-property/{lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== HERNANDO — hernandocountypa.com ==========
    def _enrich_hernando(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://www.hernandocountypa.com/Search?txtSearch={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== VOLUSIA — vcpa.vcgov.org ==========
    def _enrich_volusia(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://vcpa.vcgov.org/property-search.html?parcel={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== FLAGLER — flaglerpa.com ==========
    def _enrich_flagler(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://www.flaglerpa.com/RealProperty/Detail/{lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== ALACHUA — acpafl.org ==========
    def _enrich_alachua(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://www.acpafl.org/parcel-information?parcel={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== DUVAL — paopropertysearch.coj.net ==========
    def _enrich_duval(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://paopropertysearch.coj.net/Basic/Detail.aspx?RE={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    # ========== LEVY — levypa.com ==========
    def _enrich_levy(self, lot):
        if not lot["parcel_id"]:
            return None
        url = f"https://www.levypa.com/_Web/PropSearch/PropertyDetail.aspx?parcel={lot['parcel_id']}"
        try:
            resp = fetch(url, timeout=15)
            return self._parse_generic_pa(resp.text)
        except Exception:
            return None

    def _parse_generic_pa(self, html):
        """Parser generico melhorado: regex em texto + parsing de tabelas HTML.

        Cobre layouts:
        - Texto narrativo "Year Built: 1985"
        - Tabela HTML <th>Year Built</th><td>1985</td>
        - Variacoes de label: "Bldg SF", "Total Living", "Lot Acres", etc.
        """
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        data = {}

        # Patterns expandidos pra cobrir mais variacoes de label
        patterns = {
            "year_built": [
                r"Year\s*Built\s*[:\-]?\s*(\d{4})",
                r"Yr\s*Built\s*[:\-]?\s*(\d{4})",
                r"Construction\s*Year\s*[:\-]?\s*(\d{4})",
                r"Built\s*[:\-]?\s*(\d{4})",
            ],
            "building_sqft": [
                r"(?:Living|Heated|Total|Adjusted)\s*Area\s*[:\-]?\s*([\d,]+)",
                r"(?:Total\s*Living|Heated\s*Living)\s*(?:SF|SqFt|Sq\s*Ft)\s*[:\-]?\s*([\d,]+)",
                r"Bldg\s*(?:SF|SqFt|Sq\s*Ft)\s*[:\-]?\s*([\d,]+)",
                r"Building\s*(?:SF|SqFt|Sq\s*Ft|Square\s*Feet)\s*[:\-]?\s*([\d,]+)",
                r"Gross\s*Living\s*Area\s*[:\-]?\s*([\d,]+)",
            ],
            "lot_sqft": [
                r"Lot\s*Size\s*[:\-]?\s*([\d,]+)",
                r"Land\s*(?:SF|SqFt|Sq\s*Ft|Area)\s*[:\-]?\s*([\d,]+)",
                r"Lot\s*(?:SF|SqFt|Sq\s*Ft)\s*[:\-]?\s*([\d,]+)",
            ],
            "bedrooms": [
                r"Bedrooms?\s*[:\-]?\s*(\d+)",
                r"Beds?\s*[:\-]?\s*(\d+)",
                r"BR\s*[:\-]?\s*(\d+)",
            ],
            "bathrooms": [
                r"Bathrooms?\s*[:\-]?\s*([\d.]+)",
                r"Baths?\s*[:\-]?\s*([\d.]+)",
                r"BA\s*[:\-]?\s*([\d.]+)",
            ],
            "zoning": [
                r"Zoning\s*(?:Code)?\s*[:\-]?\s*([A-Z0-9\-]{1,15})",
                r"Use\s*Code\s*[:\-]?\s*([A-Z0-9\-]{1,15})",
            ],
            "just_value": [
                r"(?:Just|Market)\s*Value\s*[:\-]?\s*\$?\s*([\d,]+)",
                r"Total\s*(?:Just|Market)\s*[:\-]?\s*\$?\s*([\d,]+)",
            ],
            "assessed_value": [
                r"Assessed\s*Value\s*[:\-]?\s*\$?\s*([\d,]+)",
                r"Total\s*Assessed\s*[:\-]?\s*\$?\s*([\d,]+)",
                r"Assessment\s*[:\-]?\s*\$?\s*([\d,]+)",
            ],
            "property_type": [
                r"Property\s*(?:Type|Use|Class)\s*[:\-]?\s*([A-Z][A-Za-z\s]{2,40})",
                r"Use\s*Description\s*[:\-]?\s*([A-Z][A-Za-z\s]{2,40})",
                r"DOR\s*Code\s*[:\-]?\s*([A-Z0-9\-]{1,20})",
            ],
        }

        def coerce(field, raw):
            v = (raw or "").strip()
            if field in ("zoning", "property_type"):
                return v[:30]
            v = v.replace(",", "")
            try:
                return float(v) if "." in v else int(v)
            except ValueError:
                return None

        # 1. Pass de regex no texto (multi-pattern)
        for field, pats in patterns.items():
            if field in data:
                continue
            for pat in pats:
                m = re.search(pat, text, re.I)
                if m:
                    val = coerce(field, m.group(1))
                    if val is not None:
                        data[field] = val
                        break

        # 2. Pass de tabela HTML (<th>label</th><td>val</td> ou 2 <td>s)
        label_to_field = {
            "year built": "year_built", "yr built": "year_built",
            "living area": "building_sqft", "heated area": "building_sqft",
            "total area": "building_sqft", "bldg sf": "building_sqft",
            "building sf": "building_sqft", "building sqft": "building_sqft",
            "gross living area": "building_sqft",
            "lot size": "lot_sqft", "land sf": "lot_sqft", "lot sf": "lot_sqft",
            "land area": "lot_sqft",
            "bedrooms": "bedrooms", "beds": "bedrooms",
            "bathrooms": "bathrooms", "baths": "bathrooms",
            "zoning": "zoning", "zoning code": "zoning",
            "just value": "just_value", "market value": "just_value",
            "assessed value": "assessed_value", "total assessed": "assessed_value",
            "property type": "property_type", "property use": "property_type",
            "use description": "property_type", "dor code": "property_type",
        }
        for table in soup.find_all("table"):
            for tr in table.find_all("tr"):
                cells = tr.find_all(["th", "td"])
                if len(cells) < 2:
                    continue
                label = cells[0].get_text(" ", strip=True).lower().strip(":")
                value = cells[1].get_text(" ", strip=True)
                field = label_to_field.get(label)
                if field and field not in data and value:
                    val = coerce(field, value)
                    if val is not None:
                        data[field] = val

        return data if data else None


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else None
    PropertyAppraiser(county_code=code).run()
