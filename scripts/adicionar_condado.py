"""Helper para adicionar um condado novo ao sistema (qualquer estado).

Uso interativo:
  python scripts/adicionar_condado.py

Uso batch via JSON:
  python scripts/adicionar_condado.py --json '{"codigo": "MIAMI_DADE", "estado": "FL", ...}'
  python scripts/adicionar_condado.py --file novos_condados.json

Formato esperado de cada condado (mesma estrutura de config/condados.json):
{
  "codigo": "MIAMI_DADE",
  "estado": "FL",
  "nome": "Miami-Dade County",
  "aba_planilha": "MIAMI_DADE",
  "cadencia": { "tipo": "WEEKLY", "dia_semana": 2 },
  "horario_et": "09:00",
  "plataforma": "RealTDA",
  "url_sales": "https://www.miamidade.realtaxdeed.com/",
  "url_clerk": "https://www2.miamidadeclerk.gov/taxdeed/",
  "url_property_appraiser": "https://www.miamidade.gov/Apps/PA/propertysearch/",
  "telefone": "(305) 275-1155",
  "deposito": "5% ou $200",
  "status": "CONFIRMADO"
}
"""
import json
import sys
from pathlib import Path

CONFIG_FILE = Path(__file__).parent.parent / "config" / "condados.json"


def load_cfg():
    with open(CONFIG_FILE) as f:
        return json.load(f)


def save_cfg(cfg):
    cfg["metadata"]["total_condados"] = len(cfg["condados"])
    estados = sorted({c.get("estado", "FL") for c in cfg["condados"]})
    cfg["metadata"]["estados_cobertos"] = estados
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def add_condado(novo):
    obrigatorios = ["codigo", "estado", "nome", "aba_planilha", "plataforma",
                    "url_sales", "url_clerk"]
    faltando = [k for k in obrigatorios if not novo.get(k)]
    if faltando:
        raise ValueError(f"Campos obrigatorios faltando: {faltando}")
    novo.setdefault("cadencia", {"tipo": "VERIFY"})
    novo.setdefault("status", "A_VERIFICAR")
    novo.setdefault("horario_et", "Verificar")
    novo.setdefault("deposito", "Verificar site")
    novo.setdefault("telefone", "")
    novo.setdefault("url_property_appraiser", "")

    cfg = load_cfg()
    # Check duplicado
    existentes = [c["codigo"] for c in cfg["condados"]]
    if novo["codigo"] in existentes:
        # Update
        for i, c in enumerate(cfg["condados"]):
            if c["codigo"] == novo["codigo"]:
                cfg["condados"][i] = {**c, **novo}
        action = "atualizado"
    else:
        cfg["condados"].append(novo)
        action = "adicionado"
    save_cfg(cfg)
    print(f"OK: {novo['codigo']} ({novo['estado']}) {action}")


def interativo():
    print("=== Adicionar novo condado ===")
    novo = {}
    novo["codigo"] = input("Codigo (ex.: MIAMI_DADE): ").strip().upper()
    novo["estado"] = input("Estado (ex.: FL, TX, AZ): ").strip().upper()
    novo["nome"] = input("Nome completo (ex.: Miami-Dade County): ").strip()
    novo["aba_planilha"] = input(f"Aba planilha (default: {novo['codigo']}): ").strip() or novo["codigo"]
    novo["plataforma"] = input("Plataforma (RealAuction, RealTaxDeed, RealTDA, govease, Outro): ").strip()
    novo["url_sales"] = input("URL da pagina de sales: ").strip()
    novo["url_clerk"] = input("URL do Clerk: ").strip()
    novo["url_property_appraiser"] = input("URL Property Appraiser (opcional): ").strip()
    novo["horario_et"] = input("Horario (ex.: 10:00 AM ET): ").strip() or "Verificar"
    novo["telefone"] = input("Telefone do clerk: ").strip()
    novo["deposito"] = input("Regra de deposito: ").strip() or "Verificar site"

    print("\nCadencia (como saber quando tem sale)?")
    print("  1 = WEEKLY (toda semana em dia especifico)")
    print("  2 = ORDINAL (ex.: 3a quinta do mes)")
    print("  3 = VERIFY (sem regra, precisa checar site)")
    tipo = input("Escolha (1/2/3): ").strip()

    if tipo == "1":
        dia = int(input("Dia da semana (0=seg, 1=ter, ..., 6=dom): "))
        novo["cadencia"] = {"tipo": "WEEKLY", "dia_semana": dia}
        novo["status"] = "CONFIRMADO"
    elif tipo == "2":
        ordem = int(input("Ordem (1=primeira, 2=segunda, ..., 4=quarta): "))
        dia = int(input("Dia da semana (0=seg, 3=qui): "))
        novo["cadencia"] = {"tipo": "ORDINAL", "ordem": ordem, "dia_semana": dia}
        novo["status"] = "CONFIRMADO"
    else:
        novo["cadencia"] = {"tipo": "VERIFY"}
        novo["status"] = "A_VERIFICAR"

    add_condado(novo)


def main():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--json", help="JSON inline de 1 condado")
    p.add_argument("--file", help="JSON file com array de condados")
    args = p.parse_args()

    if args.json:
        add_condado(json.loads(args.json))
    elif args.file:
        with open(args.file) as f:
            payload = json.load(f)
        arr = payload["condados"] if isinstance(payload, dict) and "condados" in payload else payload
        for c in arr:
            add_condado(c)
    else:
        interativo()


if __name__ == "__main__":
    main()
