"""post_auction_config.py — Config Tier Everest (11 condados RealAuction).

Selectors validados via DEBUG_DUMP_HTML em Citrus 04/30/2026.

Estrutura HTML do .AUCTION_ITEM (Grant Street Group):
  .AUCTION_ITEM
    .AUCTION_STATS
      .ASTAT_MSGA          -> status text ("Auction Sold", "Auction Cancelled", etc)
      .ASTAT_MSGD          -> winning amount ("$900.00")
      .ASTAT_MSG_SOLDTO_MSG -> "Plaintiff" / "3rd Party" / "Cancelled"
    .AUCTION_DETAILS
      table.ad_tab tr
        td.AD_LBL          -> label da row ("Parcel ID:", "Property Address:", etc)
        td.AD_DTA          -> valor da row
    .AUCTION_ITEM_ACTION_PANEL.{WINNING|LOOSING}
      .ASTAT_MSGG          -> "You won this Auction" / "You did not win"
      .ASTAT_MSGH          -> nickname da conta logada (NAO o winner real)
"""

COUNTY_DOMAINS = {
    # === Tier Everest original (11) ===
    "Polk":         "polk.realtaxdeed.com",
    "Marion":       "marion.realtaxdeed.com",
    "Highlands":    "highlands.realtaxdeed.com",
    "Lake":         "lake.realtaxdeed.com",
    "Orange":       "orange.realtaxdeed.com",
    "Osceola":      "osceola.realtaxdeed.com",
    "Putnam":       "putnam.realtaxdeed.com",
    "St. Lucie":    "stlucie.realtaxdeed.com",
    "Lee":          "lee.realtaxdeed.com",
    "Brevard":      "brevard.realforeclose.com",
    "Citrus":       "citrus.realtaxdeed.com",
    # === Expansao Centro/Costa Atlantica + Norte (8) ===
    "Hillsborough": "hillsborough.realtaxdeed.com",
    "Pasco":        "pasco.realforeclose.com",
    "Hernando":     "hernando.realforeclose.com",
    "Volusia":      "volusia.realforeclose.com",
    "Flagler":      "flagler.realtaxdeed.com",
    "Alachua":      "alachua.realtaxdeed.com",
    "Duval":        "duval.realtaxdeed.com",
    "Levy":         "levy.realtaxdeed.com",
}

# Endpoint pattern para listar leiloes por data
DAYLIST_PATH = "/index.cfm?zaction=AUCTION&Zmethod=DAYLIST&AUCTIONDATE={date}"

# Status normalization — RealAuction tem prefixo "Auction "
STATUS_MAP = {
    "auction sold":         "sold",
    "sold":                 "sold",
    "auction redeemed":     "redeemed",
    "redeemed":             "redeemed",
    "auction cancelled":    "cancelled",
    "auction canceled":     "cancelled",
    "cancelled":            "cancelled",
    "canceled":             "cancelled",
    "withdrawn":            "cancelled",
    "no bidders":           "no-bidders",
    "no winning bidder":    "no-bidders",
    "auction closed":       "closed",
    "closed":               "closed",
}


def normalize_status(raw_status: str) -> str:
    """Converte texto de status do RealAuction para canonical."""
    if not raw_status:
        return "unknown"
    return STATUS_MAP.get(raw_status.strip().lower(), "unknown")
