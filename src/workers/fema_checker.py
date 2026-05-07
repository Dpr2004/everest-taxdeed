"""FEMA Flood Zone Checker - usa API publica do FEMA.

API: https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer
Grátis, sem autenticacao. Consulta por lat/lng.

Fluxo:
1. Geocodifica endereco do lote (via Nominatim OpenStreetMap gratis)
2. Consulta FEMA NFHL com as coordenadas
3. Salva flood_zone + risk_level na tabela dd
"""
import re
import time
import requests
from urllib.parse import quote
from src.db.connection import cursor
from src.workers.base import BaseWorker
from src.utils.http import fetch

# Nominatim Usage Policy exige User-Agent identificavel (nao browser fake).
# https://operations.osmfoundation.org/policies/nominatim/
NOMINATIM_UA = (
    "EverestTaxDeed/1.0 (taxdeed pipeline; "
    "contact: dpr2004@gmail.com)"
)

# Classificacao de risco FEMA
RISCO_POR_ZONA = {
    "A": "ALTO",        # 1% chance anual enchente
    "AE": "ALTO",
    "AH": "ALTO",
    "AO": "ALTO",
    "AR": "ALTO",
    "A99": "ALTO",
    "V": "MUITO_ALTO",  # coastal alto
    "VE": "MUITO_ALTO",
    "B": "MODERADO",
    "X": "BAIXO",       # fora das zonas especiais
    "D": "INDETERMINADO",
}


class FemaChecker(BaseWorker):
    name = "fema_checker"

    def __init__(self, limit=None):
        super().__init__()
        self.limit = limit

    def execute(self):
        # FL e mandatorio (furacao/inundacao). Outros estados = best effort.
        # Aceita lots sem address completo: geocode tenta city+state como fallback.
        with cursor() as cur:
            q = """
                SELECT l.id, l.parcel_id, l.address, l.city, l.zip,
                       c.state AS estado, c.codigo AS county_code
                FROM lots l
                JOIN sales s ON s.id = l.sale_id
                JOIN counties c ON c.id = s.county_id
                LEFT JOIN dd ON dd.lot_id = l.id
                WHERE s.sale_date >= DATE('now')
                  AND l.parcel_id NOT LIKE 'AID_%'
                  AND (dd.fema_flood_zone IS NULL OR dd.fema_flood_zone = '')
                ORDER BY
                    CASE c.state WHEN 'FL' THEN 0 ELSE 1 END,
                    s.sale_date ASC
            """
            if self.limit:
                q += f" LIMIT {int(self.limit)}"
            cur.execute(q)
            lots = cur.fetchall()

        self.candidates_count = len(lots)
        self.logger.info(f"FEMA: {len(lots)} lots para verificar")

        for lot in lots:
            try:
                coords = self._geocode(lot)
                if not coords:
                    self.logger.debug(f"FEMA skip {lot['parcel_id']}: sem coords")
                    continue
                lat, lng = coords
                zone, risk = self._query_fema(lat, lng)
                if zone:
                    self._save(lot["id"], zone, risk, lat, lng)
                    self.items_processed += 1
                    if risk in ("ALTO", "MUITO_ALTO"):
                        self.logger.warning(
                            f"FEMA risco {risk} em {lot['parcel_id']} ({lot['address']}): zona {zone}"
                        )
                time.sleep(1.1)  # respeitar rate limit Nominatim (1 req/s)
            except Exception as e:
                self.errors_count += 1
                self.logger.warning(f"FEMA {lot['parcel_id']}: {e}")

    def _geocode(self, lot):
        """Geocodifica endereco via Nominatim (OpenStreetMap).

        Estrategia em cascata pra nunca skipar FL:
        1. address + city + state
        2. city + state (vacant lots sem address detalhado)
        3. county_code + state (ultima tentativa — coords aproximadas do condado)
        """
        candidates = []
        if lot.get("address") and lot["address"].strip():
            candidates.append(", ".join(p for p in [lot["address"], lot.get("city"), lot.get("estado")] if p))
        if lot.get("city"):
            candidates.append(", ".join(p for p in [lot["city"], lot.get("estado")] if p))
        # fallback condado-level apenas pra FL (manter precisao alta nos demais)
        if lot.get("estado") == "FL" and lot.get("county_code"):
            candidates.append(f"{lot['county_code'].title()} County, FL")

        for q in candidates:
            url = f"https://nominatim.openstreetmap.org/search?q={quote(q)}&format=json&limit=1"
            try:
                # Nominatim exige UA descritivo — usar requests direto (nao fetch que tem UA browser)
                resp = requests.get(
                    url,
                    headers={"User-Agent": NOMINATIM_UA, "Accept": "application/json"},
                    timeout=15,
                )
                resp.raise_for_status()
                data = resp.json()
                if data:
                    return float(data[0]["lat"]), float(data[0]["lon"])
            except Exception as e:
                self.logger.debug(f"geocode '{q}' falhou: {e}")
            time.sleep(1.1)  # rate limit Nominatim (1 req/s)
        return None

    def _query_fema(self, lat, lng):
        """Consulta FEMA NFHL MapServer via REST API."""
        # MapServer NFHL, layer 28 = Flood Hazard Zones
        url = (
            "https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query"
            f"?geometry={lng},{lat}&geometryType=esriGeometryPoint&inSR=4326"
            "&spatialRel=esriSpatialRelIntersects&outFields=FLD_ZONE,ZONE_SUBTY"
            "&returnGeometry=false&f=json"
        )
        try:
            resp = fetch(url, timeout=15)
            data = resp.json()
            feats = data.get("features", [])
            if not feats:
                return "X", "BAIXO"  # fora de zona especial = baixo risco
            attr = feats[0].get("attributes", {})
            zone = attr.get("FLD_ZONE") or "X"
            risk = RISCO_POR_ZONA.get(zone, "INDETERMINADO")
            return zone, risk
        except Exception as e:
            self.logger.debug(f"FEMA query falhou: {e}")
            return None, None

    def _save(self, lot_id, zone, risk, lat, lng):
        with cursor() as cur:
            cur.execute("""
                INSERT INTO dd (lot_id, fema_flood_zone, fema_risk, last_updated)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(lot_id) DO UPDATE SET
                    fema_flood_zone = excluded.fema_flood_zone,
                    fema_risk = excluded.fema_risk,
                    last_updated = CURRENT_TIMESTAMP
            """, (lot_id, zone, risk))


if __name__ == "__main__":
    FemaChecker().run()
