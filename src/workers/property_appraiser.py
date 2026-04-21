"""Property Appraiser Enricher - busca dados do PA de cada condado.

Cada condado tem site proprio. Implementacao por condado:
- HIGHLANDS: hcpao.org
- POLK: polkpa.org
- LEE: leepa.org
- ORANGE: ocpaweb.ocpafl.org
- MIAMI-DADE: miamidade.gov
... etc

Por enquanto: framework pronto, com implementacoes iniciais.
Resto fica com placeholder (retorna None, worker nao falha).
"""
import json
import re
from bs4 import BeautifulSoup
from src.db.connection import cursor
from src.workers.base import BaseWorker
from src.utils.http import fetch


class PropertyAppraiser(BaseWorker):
    name = "property_appraiser"

    def __init__(self, county_code=None, limit=None):
        super().__init__()
        self.county_code = county_code
        self.limit = limit

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

        self.logger.info(f"PA enrich: {len(lots)} lots para processar")

        for lot in lots:
            try:
                method = getattr(self, f"_enrich_{lot['county_codigo'].lower()}", None)
                if not method:
                    self.logger.debug(f"Sem enricher pra {lot['county_codigo']}")
                    continue
                data = method(lot)
                if data:
                    self._save(lot["id"], data)
                    self.items_processed += 1
            except Exception as e:
                self.errors_count += 1
                self.logger.warning(f"PA falha {lot['parcel_id']}: {e}")

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

    def _parse_generic_pa(self, html):
        """Parser generico por regex pra sites de PA."""
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        data = {}
        patterns = {
            "year_built": r"Year\s*Built\s*[:\-]?\s*(\d{4})",
            "building_sqft": r"(?:Living|Heated|Total)\s*Area\s*[:\-]?\s*([\d,]+)",
            "lot_sqft": r"Lot\s*Size\s*[:\-]?\s*([\d,]+)",
            "bedrooms": r"Bedrooms?\s*[:\-]?\s*(\d+)",
            "bathrooms": r"Bathrooms?\s*[:\-]?\s*([\d.]+)",
            "zoning": r"Zoning\s*[:\-]?\s*([A-Z0-9\-]+)",
            "just_value": r"(?:Just|Market)\s*Value\s*[:\-]?\s*\$?\s*([\d,]+)",
            "assessed_value": r"Assessed\s*Value\s*[:\-]?\s*\$?\s*([\d,]+)",
        }
        for field, pat in patterns.items():
            m = re.search(pat, text, re.I)
            if m:
                v = m.group(1).replace(",", "").strip()
                if field in ("zoning",):
                    data[field] = v[:20]
                else:
                    try:
                        data[field] = float(v) if "." in v else int(v)
                    except ValueError:
                        pass
        return data if data else None


if __name__ == "__main__":
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else None
    PropertyAppraiser(county_code=code).run()
