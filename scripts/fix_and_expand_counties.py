"""Fix os 11 atuais (Opção A) + expande Tier Everest com 8 novos condados.

Roda 1x: python scripts/fix_and_expand_counties.py
"""
import json
from pathlib import Path

cfg_path = Path('config/condados.json')
with cfg_path.open(encoding='utf-8') as f:
    cfg = json.load(f)

# FIX 1: plataforma label inconsistente (RealAuction -> RealTaxDeed onde URL eh realtaxdeed.com)
fixed_platform = 0
for c in cfg['condados']:
    url = c.get('url_sales', '')
    plat = c.get('plataforma', '')
    if 'realtaxdeed.com' in url and plat == 'RealAuction':
        c['plataforma'] = 'RealTaxDeed'
        fixed_platform += 1
print(f'[FIX 1] Plataforma corrigida: {fixed_platform} condados')

# FIX 2: URLs PA atualizadas onde necessario
pa_urls = {
    'MARION':   'https://www.pa.marion.fl.us/',
    'OSCEOLA':  'https://ira.property-appraiser.org/',
    'PUTNAM':   'http://pa.putnam-fl.com/',
    'ST_LUCIE': 'https://www.paslc.gov/',
    'BREVARD':  'https://www.bcpao.us/',
    'CITRUS':   'https://pa.citrus.fl.us/',
}
fixed_pa = 0
for c in cfg['condados']:
    if c['codigo'] in pa_urls:
        if c.get('url_property_appraiser') != pa_urls[c['codigo']]:
            c['url_property_appraiser'] = pa_urls[c['codigo']]
            fixed_pa += 1
print(f'[FIX 2] URLs PA atualizadas: {fixed_pa} condados')

# ADD: Novos condados Tier Everest (Centro/Costa Atl + Norte)
existing = {c['codigo'] for c in cfg['condados']}
novos = [
    # Centro/Costa Atlântica
    {'codigo': 'HILLSBOROUGH', 'nome': 'Hillsborough County', 'aba_planilha': 'HILLSBOROUGH',
     'cadencia': {'tipo': 'VERIFY'}, 'horario_et': 'Verificar', 'plataforma': 'RealTaxDeed',
     'url_sales': 'https://hillsborough.realtaxdeed.com/',
     'url_clerk': 'https://www.hillsclerk.com/Additional-Services/Foreclosures-and-Tax-Deeds',
     'url_property_appraiser': 'https://www.hcpafl.org/',
     'telefone': 'Hillsborough Clerk', 'deposito': 'Verificar',
     'status': 'A_VERIFICAR', 'estado': 'FL'},
    {'codigo': 'PASCO', 'nome': 'Pasco County', 'aba_planilha': 'PASCO',
     'cadencia': {'tipo': 'VERIFY'}, 'horario_et': 'Verificar', 'plataforma': 'RealForeclose',
     'url_sales': 'https://pasco.realforeclose.com/',
     'url_clerk': 'https://www.pascoclerk.com/279/Tax-Deed-Sales',
     'url_property_appraiser': 'https://search.pascopa.com/',
     'telefone': 'Pasco Clerk', 'deposito': 'Verificar',
     'status': 'A_VERIFICAR', 'estado': 'FL'},
    {'codigo': 'HERNANDO', 'nome': 'Hernando County', 'aba_planilha': 'HERNANDO',
     'cadencia': {'tipo': 'VERIFY'}, 'horario_et': 'Verificar', 'plataforma': 'RealForeclose',
     'url_sales': 'https://hernando.realforeclose.com/',
     'url_clerk': 'https://www.hernandoclerk.com/recording/tax-deed-sales',
     'url_property_appraiser': 'https://www.hernandocountypa.com/',
     'telefone': 'Hernando Clerk', 'deposito': 'Verificar',
     'status': 'A_VERIFICAR', 'estado': 'FL'},
    {'codigo': 'VOLUSIA', 'nome': 'Volusia County', 'aba_planilha': 'VOLUSIA',
     'cadencia': {'tipo': 'VERIFY'}, 'horario_et': 'Verificar', 'plataforma': 'RealForeclose',
     'url_sales': 'https://volusia.realforeclose.com/',
     'url_clerk': 'https://www.clerk.org/courts/tax-deed-sales',
     'url_property_appraiser': 'https://vcpa.vcgov.org/',
     'telefone': 'Volusia Clerk', 'deposito': 'Verificar',
     'status': 'A_VERIFICAR', 'estado': 'FL'},
    {'codigo': 'FLAGLER', 'nome': 'Flagler County', 'aba_planilha': 'FLAGLER',
     'cadencia': {'tipo': 'VERIFY'}, 'horario_et': 'Verificar', 'plataforma': 'RealTaxDeed',
     'url_sales': 'https://flagler.realtaxdeed.com/',
     'url_clerk': 'https://www.flaglerclerk.com/recording/tax-deed-sales',
     'url_property_appraiser': 'https://www.flaglerpa.com/',
     'telefone': 'Flagler Clerk', 'deposito': 'Verificar',
     'status': 'A_VERIFICAR', 'estado': 'FL'},
    # Norte
    {'codigo': 'ALACHUA', 'nome': 'Alachua County', 'aba_planilha': 'ALACHUA',
     'cadencia': {'tipo': 'VERIFY'}, 'horario_et': 'Verificar', 'plataforma': 'RealTaxDeed',
     'url_sales': 'https://alachua.realtaxdeed.com/',
     'url_clerk': 'https://www.alachuaclerk.org/court_services/tax_deeds.cfm',
     'url_property_appraiser': 'https://www.acpafl.org/',
     'telefone': 'Alachua Clerk', 'deposito': 'Verificar',
     'status': 'A_VERIFICAR', 'estado': 'FL'},
    {'codigo': 'DUVAL', 'nome': 'Duval County (Jacksonville)', 'aba_planilha': 'DUVAL',
     'cadencia': {'tipo': 'VERIFY'}, 'horario_et': 'Verificar', 'plataforma': 'RealTaxDeed',
     'url_sales': 'https://duval.realtaxdeed.com/',
     'url_clerk': 'https://www.duvalclerk.com/Court-Records/Tax-Deeds',
     'url_property_appraiser': 'https://paopropertysearch.coj.net/',
     'telefone': 'Duval Clerk', 'deposito': 'Verificar',
     'status': 'A_VERIFICAR', 'estado': 'FL'},
    {'codigo': 'LEVY', 'nome': 'Levy County', 'aba_planilha': 'LEVY',
     'cadencia': {'tipo': 'VERIFY'}, 'horario_et': 'Verificar', 'plataforma': 'RealTaxDeed',
     'url_sales': 'https://levy.realtaxdeed.com/',
     'url_clerk': 'https://www.levyclerk.com/tax-deed-sales/',
     'url_property_appraiser': 'https://www.levypa.com/',
     'telefone': 'Levy Clerk', 'deposito': 'Verificar',
     'status': 'A_VERIFICAR', 'estado': 'FL'},
]

added = 0
for nc in novos:
    if nc['codigo'] not in existing:
        cfg['condados'].append(nc)
        added += 1
        print(f'  + {nc["codigo"]}: {nc["nome"]}')

print(f'[ADD] {added} condados novos adicionados')
print(f'[TOTAL] Tier Everest agora: {len(cfg["condados"])} condados')

with cfg_path.open('w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
print('[OK] condados.json atualizado')
