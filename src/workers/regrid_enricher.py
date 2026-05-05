"""Regrid Enricher - substitui o Property Appraiser scraping.

Usa a API oficial Regrid (Parcels API v2) para dados canonicos de parcela:
- just_value / assessed_value / land_value / improvement_value
- endereco padronizado (saddno + saddstr + scity + sstate + szip)
- land use (usedesc), zoning, year_built
- acres (gisacre), lat/lon
- owner name
- geometria GeoJSON (salva em raw_data_json)

Docs: https://app.regrid.com/api/v2/parcels

Rate limit Pro: ~10 req/s. Usamos 5 req/s por seguranca.
"""
import os
import json
import time
import requests
from src.db.connection import cursor
from src.workers.base import BaseWorker

REGRID_BASE = "https://app.regrid.com/api/v2/parcels"
TOKEN = os.environ.get("REGRID_API_KEY", "").strip()
REQ_PER_SEC = float(os.environ.get("REGRID_RATE", "5"))
MAX_LOTES = int(os.environ.get("REGRID_MAX_LOTES", "500"))

# Codigo condado Everest -> slug Regrid (verificar em app.regrid.com/us/fl/<slug>)
# Slugs oficiais Regrid seguem formato lowercase com hifen
CONDADO_SLUG = {
    # Tier Everest original (11)
    "CITRUS": "citrus",
    "MARION": "marion",
    "PUTNAM": "putnam",
    "LAKE": "lake",
    "ORANGE": "orange",
    "BREVARD": "brevard",
    "OSCEOLA": "osceola",
    "POLK": "polk",
    "HIGHLANDS": "highlands",
    "ST_LUCIE": "st-lucie",
    "LEE": "lee",
    # Expansao (8) — Centro/Costa Atlantica + Norte
    "HILLSBOROUGH": "hillsborough",
    "PASCO": "pasco",
    "HERNANDO": "hernando",
    "VOLUSIA": "volusia",
    "FLAGLER": "flagler",
    "ALACHUA": "alachua",
    "DUVAL": "duval",
    "LEVY": "levy",
}


class RegridEnricher(BaseWorker):
    name = "regrid_enricher"

    def execute(self):
        # FAIL LOUD diagnostico: revela exatamente o que esta acontecendo
        # com o token (sem expor o valor — so length + prefix).
        if not TOKEN:
            self.logger.error("REGRID_API_KEY VAZIO no env. Setar em GitHub Settings > Secrets and variables > Actions > New repository secret > REGRID_API_KEY")
            self.candidates_count = 1
            self.errors_count = 1
            return

        self.logger.info(f"REGRID_API_KEY presente (len={len(TOKEN)}, prefix={TOKEN[:6]}...)")

        # SMOKE TEST: faz 1 request conhecido pra verificar token + endpoint
        # ANTES de iterar. Se token invalido/endpoint mudou, falha rapido com
        # log claro em vez de "items=0 errs=0" enganoso.
        try:
            test_resp = requests.get(
                f"{REGRID_BASE}/parcelnumb",
                params={"parcelnumb": "test", "path": "/us/fl/polk", "token": TOKEN},
                timeout=15,
            )
            self.logger.info(f"Smoke test Regrid: HTTP {test_resp.status_code}")
            if test_resp.status_code == 401:
                self.logger.error("Regrid retornou 401 UNAUTHORIZED — token INVALIDO ou EXPIRADO. Verificar/renovar em app.regrid.com")
                self.candidates_count = 1
                self.errors_count = 1
                return
            if test_resp.status_code in (403, 402):
                self.logger.error(f"Regrid {test_resp.status_code} — provavel quota excedida ou plano expirado: {test_resp.text[:300]}")
                self.candidates_count = 1
                self.errors_count = 1
                return
            if test_resp.status_code >= 500:
                self.logger.error(f"Regrid 5xx — API com problema. Tentando mesmo assim: {test_resp.text[:200]}")
        except Exception as e:
            self.logger.error(f"Smoke test Regrid falhou: {type(e).__name__}: {e}. Pode ser firewall/DNS/SSL.")
            self.candidates_count = 1
            self.errors_count = 1
            return

        lotes = self._buscar_lotes_incompletos()
        if not lotes:
            self.logger.warning("Query retornou 0 lotes pra enriquecer — todos ja tem just_value+address? (suspeito)")
            return

        self.candidates_count = len(lotes)
        self.logger.info(f"Enriquecendo {len(lotes)} lotes via Regrid API (slug map cobre {len(CONDADO_SLUG)} condados)")

        delay = 1.0 / max(REQ_PER_SEC, 1)
        enriquecidos = 0
        from collections import Counter
        sucesso_por_cond = Counter()
        falha_por_cond = Counter()
        sem_slug = Counter()
        for lot in lotes:
            cod = lot["codigo"]
            try:
                if cod not in CONDADO_SLUG:
                    sem_slug[cod] += 1
                    continue
                props = self._buscar_parcel(lot["parcel_id"], cod)
                if props:
                    self._atualizar_lote(lot["id"], props)
                    enriquecidos += 1
                    self.items_processed += 1
                    sucesso_por_cond[cod] += 1
                else:
                    falha_por_cond[cod] += 1
                time.sleep(delay)
            except Exception as e:
                self.errors_count += 1
                falha_por_cond[cod] += 1
                self.logger.warning(f"Lote {lot['id']} ({lot['parcel_id']}): {e}")

        self.logger.info(f"Enriquecidos: {enriquecidos}/{len(lotes)}")
        if sucesso_por_cond:
            self.logger.info(f"Regrid SUCCESS por condado: {dict(sucesso_por_cond.most_common())}")
        if falha_por_cond:
            self.logger.warning(f"Regrid FAIL por condado (404 ou sem dados): {dict(falha_por_cond.most_common())}")
        if sem_slug:
            self.logger.warning(f"Regrid SEM SLUG mapeado: {dict(sem_slug.most_common())}")

    def _buscar_lotes_incompletos(self):
        """Busca lotes de sales futuros que precisam enriquecimento.

        Criterio: sale no futuro + (just_value vazio OU address vazio).
        """
        with cursor() as cur:
            cur.execute("""
                SELECT l.id, l.parcel_id, c.codigo
                FROM lots l
                JOIN sales s ON s.id = l.sale_id
                JOIN counties c ON c.id = s.county_id
                WHERE s.sale_date >= DATE('now')
                  AND l.parcel_id IS NOT NULL
                  AND l.parcel_id != ''
                  AND (l.just_value IS NULL OR l.just_value = 0
                       OR l.address IS NULL OR l.address = '')
                LIMIT ?
            """, (MAX_LOTES,))
            return cur.fetchall()

    def _buscar_parcel(self, parcel_id, codigo_condado):
        slug = CONDADO_SLUG.get(codigo_condado)
        if not slug or not parcel_id:
            return None

        # Regrid aceita parcelnumb com/sem formatacao, tenta os dois
        variations = [
            parcel_id.strip(),
            parcel_id.replace("-", "").replace(" ", "").strip(),
        ]
        variations = list(dict.fromkeys(variations))  # dedup preservando ordem

        for pn in variations:
            url = f"{REGRID_BASE}/parcelnumb"
            params = {
                "parcelnumb": pn,
                "path": f"/us/fl/{slug}",
                "token": TOKEN,
            }
            try:
                resp = requests.get(url, params=params, timeout=20)
                if resp.status_code == 404:
                    continue
                if resp.status_code == 401:
                    self.logger.error("Regrid token invalido/expirado (401)")
                    return None
                if resp.status_code == 429:
                    self.logger.warning("Rate limit Regrid, esperando 3s")
                    time.sleep(3)
                    continue
                resp.raise_for_status()
                data = resp.json()
                parcels = (data.get("parcels") or {}).get("features") or []
                if parcels:
                    return parcels[0].get("properties", {}).get("fields", {}) \
                           or parcels[0].get("properties", {})
            except requests.RequestException as e:
                self.logger.debug(f"Regrid tentativa {pn}: {e}")
                continue
        return None

    def _atualizar_lote(self, lot_id, regrid):
        """Mapeia campos Regrid -> schema nosso. Preenche apenas o que vier."""
        def f(v):
            """Float safe."""
            try:
                return float(v) if v not in (None, "", "NULL") else None
            except (ValueError, TypeError):
                return None

        def s(v):
            """String safe."""
            return str(v).strip() if v not in (None, "") else None

        # Monta endereco completo do Regrid
        saddno = s(regrid.get("saddno"))
        saddstr = s(regrid.get("saddstr"))
        saddsttyp = s(regrid.get("saddsttyp"))
        address_parts = [p for p in [saddno, saddstr, saddsttyp] if p]
        address_full = " ".join(address_parts) if address_parts else s(regrid.get("address"))

        fields = {
            "address": address_full,
            "city": s(regrid.get("scity")) or s(regrid.get("city")),
            "zip": s(regrid.get("szip")) or s(regrid.get("zip")),
            "legal_description": s(regrid.get("legaldesc")),
            "year_built": f(regrid.get("struct_yr")),
            "zoning": s(regrid.get("zoning")),
            "assessed_value": f(regrid.get("assessval")),
            "just_value": f(regrid.get("parval")) or f(regrid.get("just_value")),
            "lot_sqft": self._acres_to_sqft(f(regrid.get("gisacre")) or f(regrid.get("ll_gisacre"))),
            "property_type": self._usecode_to_type(regrid.get("usedesc"), regrid.get("usecode")),
        }
        fields = {k: v for k, v in fields.items() if v is not None}

        if not fields:
            self.logger.debug(f"Lote {lot_id}: Regrid sem dados relevantes")
            return

        # Raw JSON vai pro raw_data_json (append, nao sobrescreve)
        raw_key = "regrid"
        with cursor() as cur:
            cur.execute("SELECT raw_data_json FROM lots WHERE id = ?", (lot_id,))
            row = cur.fetchone()
            existing = {}
            if row and row["raw_data_json"]:
                try:
                    existing = json.loads(row["raw_data_json"])
                except json.JSONDecodeError:
                    existing = {}
            if not isinstance(existing, dict):
                existing = {"previous": existing}
            existing[raw_key] = {
                "owner": s(regrid.get("owner")),
                "usedesc": s(regrid.get("usedesc")),
                "usecode": s(regrid.get("usecode")),
                "lat": f(regrid.get("lat")),
                "lon": f(regrid.get("lon")),
                "gisacre": f(regrid.get("gisacre")),
                "landval": f(regrid.get("landval")),
                "improvval": f(regrid.get("improvval")),
                "taxyr": regrid.get("taxyr"),
                "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            fields["raw_data_json"] = json.dumps(existing, default=str)

            sets = ", ".join(f"{k} = ?" for k in fields)
            cur.execute(
                f"UPDATE lots SET {sets} WHERE id = ?",
                list(fields.values()) + [lot_id]
            )

    @staticmethod
    def _acres_to_sqft(acres):
        if acres is None:
            return None
        return round(acres * 43560, 0)

    @staticmethod
    def _usecode_to_type(usedesc, usecode):
        """Mapeia descricao Regrid -> property_type nosso."""
        if not usedesc and not usecode:
            return None
        text = (str(usedesc or "") + " " + str(usecode or "")).lower()
        if "vacant" in text or "lot" in text:
            return "Lot"
        if "single" in text or "sfr" in text or "residential" in text:
            return "SFR"
        if "multi" in text or "duplex" in text or "apartment" in text:
            return "Multi"
        if "commercial" in text or "comm" in text or "retail" in text:
            return "Comm"
        if "mobile" in text or "manufactured" in text:
            return "Mobile"
        return "Outros"


if __name__ == "__main__":
    RegridEnricher().run()
