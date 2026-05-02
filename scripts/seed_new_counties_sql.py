"""Insere/atualiza condados no DB direto via SQL com schema real."""
import json
import sqlite3
from pathlib import Path

DB = Path('data/taxdeed.db')
CFG = Path('config/condados.json')

with CFG.open(encoding='utf-8') as f:
    cfg = json.load(f)

con = sqlite3.connect(str(DB))
cur = con.cursor()

inserted = 0
updated = 0
for c in cfg['condados']:
    cad = c.get('cadencia', {})
    cad_tipo = cad.get('tipo', 'VERIFY')
    cad_dia = cad.get('dia_semana')
    cad_ord = cad.get('ordem')

    cur.execute('SELECT id FROM counties WHERE codigo = ?', (c['codigo'],))
    row = cur.fetchone()
    if row:
        cur.execute("""
            UPDATE counties SET nome=?, aba_planilha=?, plataforma=?, horario_et=?,
                url_sales=?, url_clerk=?, url_property_appraiser=?,
                telefone=?, deposito=?, status=?, ativo=1,
                cadencia_tipo=?, cadencia_dia_semana=?, cadencia_ordem=?, state=?
            WHERE codigo=?
        """, (c['nome'], c.get('aba_planilha', c['codigo']), c['plataforma'], c['horario_et'],
              c['url_sales'], c['url_clerk'], c.get('url_property_appraiser', ''),
              c['telefone'], c['deposito'], c['status'],
              cad_tipo, cad_dia, cad_ord, c['estado'], c['codigo']))
        updated += 1
    else:
        cur.execute("""
            INSERT INTO counties (codigo, state, nome, aba_planilha, cadencia_tipo,
                cadencia_dia_semana, cadencia_ordem, horario_et, plataforma,
                url_sales, url_clerk, url_property_appraiser,
                telefone, deposito, status, ativo)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        """, (c['codigo'], c['estado'], c['nome'], c.get('aba_planilha', c['codigo']),
              cad_tipo, cad_dia, cad_ord, c['horario_et'], c['plataforma'],
              c['url_sales'], c['url_clerk'], c.get('url_property_appraiser', ''),
              c['telefone'], c['deposito'], c['status']))
        inserted += 1
        print(f'  + INSERT {c["codigo"]}: {c["nome"]}')

con.commit()
print(f'[OK] Inserted {inserted}, Updated {updated}')

cur.execute('SELECT COUNT(*) FROM counties WHERE ativo=1')
total = cur.fetchone()[0]
print(f'[TOTAL] {total} condados ativos no DB')
con.close()
