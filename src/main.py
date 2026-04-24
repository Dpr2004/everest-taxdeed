"""Orquestrador principal. Modos:
  python -m src.main --init          # inicializa DB + seeds
  python -m src.main --scrape-calendar
  python -m src.main --scrape-lots [--county LEE]
  python -m src.main --update-spreadsheet [--county LEE]
  python -m src.main --all           # roda tudo em sequencia
  python -m src.main --daemon        # loop infinito com agendamento interno
"""
import argparse
import os
import sys
import time
from datetime import datetime

# Ajusta sys.path para imports funcionarem fora do package
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.connection import init_schema
from src.db.seeds import seed_counties
from src.workers.calendar_scraper import CalendarScraper
from src.workers.lot_list_scraper import LotListScraper
from src.workers.lot_scraper_playwright import LotScraperPlaywright
from src.workers.spreadsheet_writer import SpreadsheetWriter
from src.workers.scoring_engine import ScoringEngine
from src.workers.property_appraiser import PropertyAppraiser
from src.workers.fema_checker import FemaChecker
from src.workers.alert_engine import AlertEngine
from src.workers.regrid_enricher import RegridEnricher
from src.utils.logger import get_logger

logger = get_logger("main")


def cmd_init():
    logger.info("Inicializando schema + seeds")
    init_schema()
    seed_counties()
    logger.info("DB pronto")


def cmd_scrape_calendar():
    CalendarScraper().run()


def cmd_scrape_lots(county=None):
    LotListScraper(county_code=county).run()


def cmd_scrape_lots_playwright(county=None):
    """Scraper Playwright - autenticado no RealAuction. Requer:
    REALAUCTION_USER e REALAUCTION_PASS env vars."""
    LotScraperPlaywright(county_code=county).run()


def cmd_update_spreadsheet(county=None):
    SpreadsheetWriter(county_code=county).run()


def cmd_scoring():
    ScoringEngine().run()


def cmd_property_appraiser(county=None, limit=None):
    PropertyAppraiser(county_code=county, limit=limit).run()


def cmd_fema(limit=None):
    FemaChecker(limit=limit).run()


def cmd_alerts():
    AlertEngine().run()


def cmd_regrid():
    RegridEnricher().run()


def cmd_enrich(county=None):
    """Roda pipeline completo: PA + FEMA + Scoring + Alerts."""
    cmd_property_appraiser(county)
    cmd_fema()
    cmd_scoring()
    cmd_alerts()


def cmd_all(county=None):
    cmd_scrape_calendar()
    cmd_scrape_lots(county)
    cmd_update_spreadsheet(county)


def cmd_daemon():
    """Loop infinito. Agendamento simples baseado em relogio.

    - Calendar scraper: diario 6AM
    - Lot scraper: diario 6:30AM
    - Spreadsheet writer: diario 7AM
    """
    from datetime import datetime, timedelta
    logger.info("Daemon iniciado")
    last_run = {"cal": None, "lot": None, "ss": None}
    while True:
        now = datetime.now()
        hora = now.hour
        dia = now.date()

        def diff_run(key):
            return last_run[key] != dia

        try:
            if hora == 6 and diff_run("cal"):
                cmd_scrape_calendar()
                last_run["cal"] = dia
            if hora == 6 and diff_run("lot") and now.minute >= 30:
                cmd_scrape_lots()
                last_run["lot"] = dia
            if hora == 7 and diff_run("ss"):
                cmd_update_spreadsheet()
                last_run["ss"] = dia
        except Exception:
            logger.exception("Erro no loop daemon (continuando)")

        time.sleep(60)  # verifica a cada minuto


def main():
    p = argparse.ArgumentParser(description="Everest TaxDeed Worker Orchestrator")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--init", action="store_true", help="Inicializa DB e seeds")
    g.add_argument("--scrape-calendar", action="store_true")
    g.add_argument("--scrape-lots", action="store_true")
    g.add_argument("--scrape-lots-playwright", action="store_true",
                   help="Scraper autenticado via Playwright (usa REALAUCTION_USER/PASS)")
    g.add_argument("--update-spreadsheet", action="store_true")
    g.add_argument("--all", action="store_true")
    g.add_argument("--daemon", action="store_true")
    g.add_argument("--scoring", action="store_true", help="Calcula scores para lots")
    g.add_argument("--property-appraiser", action="store_true", help="Enrich via PA")
    g.add_argument("--fema", action="store_true", help="FEMA flood zones")
    g.add_argument("--alerts", action="store_true", help="Dispara alertas")
    g.add_argument("--enrich", action="store_true", help="Pipeline: PA + FEMA + Score + Alerts")
    g.add_argument("--regrid", action="store_true", help="Enriquece via API Regrid (dados canonicos)")
    p.add_argument("--county", default=None, help="Codigo do condado (ex: LEE)")
    p.add_argument("--limit", default=None, help="Limite de lots a processar", type=int)

    args = p.parse_args()

    if args.init:
        cmd_init()
    elif args.scrape_calendar:
        cmd_scrape_calendar()
    elif args.scrape_lots:
        cmd_scrape_lots(args.county)
    elif args.scrape_lots_playwright:
        cmd_scrape_lots_playwright(args.county)
    elif args.update_spreadsheet:
        cmd_update_spreadsheet(args.county)
    elif args.all:
        cmd_all(args.county)
    elif args.daemon:
        # No primeiro start, inicializa DB se vazio
        try:
            cmd_init()
        except Exception:
            logger.exception("Init falhou - continuando")
        cmd_daemon()
    elif args.scoring:
        cmd_scoring()
    elif args.property_appraiser:
        cmd_property_appraiser(args.county, args.limit)
    elif args.fema:
        cmd_fema(args.limit)
    elif args.alerts:
        cmd_alerts()
    elif args.enrich:
        cmd_enrich(args.county)
    elif args.regrid:
        cmd_regrid()


if __name__ == "__main__":
    main()
