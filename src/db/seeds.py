"""Carrega condados no DB. Idempotente. Le config/condados.json."""
import json
import os
from pathlib import Path
from src.db.connection import cursor

CONFIG_FILE = Path(__file__).parent.parent.parent / "config" / "condados.json"


def load_config():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def seed_counties():
    cfg = load_config()
    with cursor() as cur:
        for c in cfg["condados"]:
            cad = c.get("cadencia", {})
            estado = c.get("estado", "FL")
            cur.execute("""
                INSERT INTO counties (codigo, state, nome, aba_planilha, cadencia_tipo,
                    cadencia_dia_semana, cadencia_ordem, horario_et, plataforma,
                    url_sales, url_clerk, url_property_appraiser, telefone, deposito, status, ativo)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(codigo) DO UPDATE SET
                    state = excluded.state,
                    nome = excluded.nome,
                    aba_planilha = excluded.aba_planilha,
                    cadencia_tipo = excluded.cadencia_tipo,
                    cadencia_dia_semana = excluded.cadencia_dia_semana,
                    cadencia_ordem = excluded.cadencia_ordem,
                    horario_et = excluded.horario_et,
                    plataforma = excluded.plataforma,
                    url_sales = excluded.url_sales,
                    url_clerk = excluded.url_clerk,
                    url_property_appraiser = excluded.url_property_appraiser,
                    telefone = excluded.telefone,
                    deposito = excluded.deposito,
                    status = excluded.status
            """, (
                c["codigo"], estado, c["nome"], c["aba_planilha"],
                cad.get("tipo", "VERIFY"), cad.get("dia_semana"), cad.get("ordem"),
                c.get("horario_et"), c.get("plataforma"), c.get("url_sales"),
                c.get("url_clerk"), c.get("url_property_appraiser"),
                c.get("telefone"), c.get("deposito"), c.get("status", "A_VERIFICAR"),
            ))


if __name__ == "__main__":
    seed_counties()
    print(f"OK: condados populados em {os.environ.get('DB_PATH', './data/taxdeed.db')}")
