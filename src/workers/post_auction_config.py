"""
post_auction_config.py — Configuracao por condado para post-auction monitoring.

Mapeia cada condado Tier Everest ao seu dominio RealAuction (Grant Street Group)
e selectors CSS especificos quando necessario.

NOTA SOBRE SELECTORS: o RealAuction tem template padrao para os 11 condados,
mas alguns variam levemente. Os selectors abaixo sao chute educado baseado
em estrutura padrao Grant Street. Validar via Playwright recorder em pelo
menos 1 condado antes de ativar todos os 11.
"""

# Domain por condado — todos sao subdominios .realforeclose.com (ou variante)
COUNTY_DOMAINS = {
    "Polk":      "polk.realforeclose.com",
    "Marion":    "marion.realforeclose.com",
    "Highlands": "highlands.realforeclose.com",
    "Lake":      "lake.realforeclose.com",
    "Orange":    "orange.realforeclose.com",
    "Osceola":   "osceola.realforeclose.com",
    "Putnam":    "putnam.realforeclose.com",
    "St. Lucie": "stlucie.realforeclose.com",
    "Lee":       "lee.realforeclose.com",
    "Brevard":   "brevard.realforeclose.com",
    "Citrus":    "citrus.realforeclose.com",
}

# Selectors CSS — Grant Street Group standard template
# VALIDAR via inspecao manual antes de production
SELECTORS = {
    "auction_item":     ".AUCTION_ITEM",
    "parcel_id":        ".AUCTION_ITEM .ad_tab .parcel-id, .AUCTION_ITEM [data-parcel]",
    "status":           ".AUCTION_ITEM .ad_status, .AUCTION_ITEM .auction-status",
    "winning_bidder":   ".AUCTION_ITEM .ad_winning_bidder, .AUCTION_ITEM .winning-bidder",
    "winning_amount":   ".AUCTION_ITEM .ad_winning_amount, .AUCTION_ITEM .winning-amount",
    "case_number":      ".AUCTION_ITEM .case-number",
}

# Endpoint pattern para listar leiloes por data (mesmo do lot_scraper)
DAYLIST_PATH = "/index.cfm?zaction=AUCTION&Zmethod=DAYLIST&AUCTIONDATE={date}"

# Status normalization — mapeia variacoes para os 4 valores canonicos
STATUS_MAP = {
    "sold":               "sold",
    "auction sold":       "sold",
    "winning bid":        "sold",
    "redeemed":           "redeemed",
    "redemption":         "redeemed",
    "owner redeemed":     "redeemed",
    "cancelled":          "cancelled",
    "canceled":           "cancelled",
    "withdrawn":          "cancelled",
    "no bidders":         "no-bidders",
    "no bidder":          "no-bidders",
    "no winning bidder":  "no-bidders",
}

def normalize_status(raw_status: str) -> str:
    """Converte texto de status do RealAuction para canonical (sold/redeemed/cancelled/no-bidders)."""
    if not raw_status:
        return "unknown"
    key = raw_status.strip().lower()
    return STATUS_MAP.get(key, "unknown")
