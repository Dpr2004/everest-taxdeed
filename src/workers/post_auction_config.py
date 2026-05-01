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
