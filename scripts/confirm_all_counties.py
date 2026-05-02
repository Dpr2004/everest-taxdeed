"""Confirma status dos 19 condados (URLs validadas) e marca PROXIMOS_PASSOS."""
import json
from pathlib import Path

cfg_path = Path('config/condados.json')
with cfg_path.open(encoding='utf-8') as f:
    cfg = json.load(f)

# URLs validadas via HTTP HEAD em 2026-05-02
validated = {'POLK','MARION','HIGHLANDS','LAKE','ORANGE','OSCEOLA','PUTNAM',
             'ST_LUCIE','LEE','BREVARD','CITRUS','HILLSBOROUGH','PASCO',
             'HERNANDO','VOLUSIA','FLAGLER','ALACHUA','DUVAL','LEVY'}

confirmed = 0
for c in cfg['condados']:
    if c['codigo'] in validated and c['status'] == 'A_VERIFICAR':
        c['status'] = 'CONFIRMADO'
        confirmed += 1

print(f'[OK] {confirmed} condados marcados como CONFIRMADO')
print(f'[TOTAL] {len(cfg["condados"])} condados ativos')

with cfg_path.open('w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
