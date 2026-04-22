"""Gera web/fl-counties.svg com TODOS os 67 condados da Florida
baseado em dados oficiais do US Census Bureau (TIGER/Line).

Highlighta os 11 condados-alvo em dourado.
Roda no GitHub Actions como parte do full-pipeline.
"""
import json
import os
import sys
from pathlib import Path
from urllib.request import urlopen, Request

# GeoJSON oficial dos condados da Florida (fonte: plotly/datasets, deriva de US Census)
# Backup: OpenDataDE mirror do Census TIGER
SOURCES = [
    "https://raw.githubusercontent.com/plotly/datasets/master/geojson-counties-fips.json",
]

# Condados-alvo Everest (FIPS code Florida = 12, county codes oficiais)
# Fonte: https://www.census.gov/library/reference/code-lists/ansi.html#county
FIPS_ALVO = {
    "12017": "CITRUS",
    "12083": "MARION",
    "12107": "PUTNAM",
    "12069": "LAKE",
    "12095": "ORANGE",
    "12009": "BREVARD",
    "12097": "OSCEOLA",
    "12105": "POLK",
    "12055": "HIGHLANDS",
    "12111": "ST_LUCIE",
    "12071": "LEE",
}


def baixar_geojson():
    last_err = None
    for url in SOURCES:
        try:
            print(f"[map] Baixando {url}")
            req = Request(url, headers={"User-Agent": "Everest-TaxDeed/1.0"})
            with urlopen(req, timeout=45) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            print(f"[map] OK — {len(data.get('features', []))} features")
            return data
        except Exception as e:
            print(f"[map] Falha {url}: {e}")
            last_err = e
    raise RuntimeError(f"Nenhuma fonte disponivel: {last_err}")


def filtrar_fl(gj):
    """Extrai apenas features com STATE FIPS = 12 (Florida)."""
    feats = []
    for f in gj["features"]:
        fips = f.get("id") or f.get("properties", {}).get("GEO_ID", "")
        # id pode ser "12095" ou "0500000US12095"
        if isinstance(fips, str) and (fips.startswith("12") and len(fips) == 5):
            feats.append(f)
        elif isinstance(fips, str) and "US12" in fips:
            code = fips.split("US")[-1]
            if len(code) == 5 and code.startswith("12"):
                f["id"] = code
                feats.append(f)
    print(f"[map] Florida features: {len(feats)}")
    return feats


def bbox(features):
    xs, ys = [], []
    for f in features:
        for ring in iter_rings(f["geometry"]):
            for pt in ring:
                xs.append(pt[0])
                ys.append(pt[1])
    return min(xs), min(ys), max(xs), max(ys)


def iter_rings(geom):
    if geom["type"] == "Polygon":
        for ring in geom["coordinates"]:
            yield ring
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            for ring in poly:
                yield ring


def feature_to_path(geom, proj, simplify=0.0005):
    """Converte geometry em path SVG com simplificacao leve."""
    def ring_d(ring):
        # Simplificacao: remover pontos muito proximos
        filtered = [ring[0]]
        for p in ring[1:]:
            last = filtered[-1]
            if abs(p[0] - last[0]) > simplify or abs(p[1] - last[1]) > simplify:
                filtered.append(p)
        pts = [proj(x, y) for x, y in filtered]
        if len(pts) < 3:
            return ""
        return "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in pts) + "Z"

    parts = []
    if geom["type"] == "Polygon":
        for ring in geom["coordinates"]:
            d = ring_d(ring)
            if d:
                parts.append(d)
    elif geom["type"] == "MultiPolygon":
        for poly in geom["coordinates"]:
            for ring in poly:
                d = ring_d(ring)
                if d:
                    parts.append(d)
    return " ".join(parts)


def gerar_svg(features, outpath):
    minx, miny, maxx, maxy = bbox(features)
    # Aspect ratio geografico preservado (mercator simplificado)
    import math
    # Latitude central da Florida ~ 28
    lat_c = (miny + maxy) / 2
    aspect = math.cos(math.radians(lat_c))
    # viewBox
    VB_W = 1000
    geo_w = (maxx - minx) * aspect
    geo_h = (maxy - miny)
    VB_H = int(VB_W * geo_h / geo_w)

    def proj(lon, lat):
        x = (lon - minx) / (maxx - minx) * VB_W
        # Y invertido (SVG cresce pra baixo, lat cresce pra cima)
        y = (maxy - lat) / (maxy - miny) * VB_H
        return x, y

    paths_alvo = []
    paths_outros = []
    for f in features:
        fips = f.get("id", "")
        name_orig = f.get("properties", {}).get("NAME", "")
        is_target = fips in FIPS_ALVO
        code = FIPS_ALVO.get(fips, "")
        d = feature_to_path(f["geometry"], proj)
        if not d:
            continue
        attrs = f'data-fips="{fips}" data-name="{name_orig}"'
        if is_target:
            paths_alvo.append(
                f'<a class="fl-link" xlink:href="county.html?c={code}">'
                f'<path class="fl-target" {attrs} data-code="{code}" d="{d}"/>'
                f'</a>'
            )
        else:
            paths_outros.append(f'<path class="fl-other" {attrs} d="{d}"/>')

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {VB_W} {VB_H}" preserveAspectRatio="xMidYMid meet">
<defs>
  <linearGradient id="flGold" x1="0" y1="0" x2="0" y2="1">
    <stop offset="0%" stop-color="#F5E6A8"/>
    <stop offset="25%" stop-color="#E8C86A"/>
    <stop offset="60%" stop-color="#C9972B"/>
    <stop offset="100%" stop-color="#8C6815"/>
  </linearGradient>
  <filter id="flGlow">
    <feGaussianBlur stdDeviation="2.5" result="b"/>
    <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
  </filter>
</defs>
<style>
  .fl-other {{ fill: rgba(19,18,62,0.55); stroke: rgba(255,255,255,0.10); stroke-width: 0.6; transition: fill .18s; }}
  .fl-other:hover {{ fill: rgba(29,65,141,0.55); stroke: rgba(255,255,255,0.22); }}
  .fl-target {{ fill: rgba(29,65,141,0.75); stroke: rgba(201,151,43,0.85); stroke-width: 1.4; cursor: pointer; transition: all .2s; }}
  .fl-target:hover {{ fill: url(#flGold); stroke: #F5E6A8; stroke-width: 2; filter: url(#flGlow); }}
  .fl-link {{ cursor: pointer; }}
</style>
{chr(10).join(paths_outros)}
{chr(10).join(paths_alvo)}
</svg>'''

    Path(outpath).parent.mkdir(parents=True, exist_ok=True)
    Path(outpath).write_text(svg, encoding="utf-8")
    print(f"[map] SVG gerado: {outpath} ({len(svg)//1024}KB)")
    print(f"[map] Alvo: {len(paths_alvo)} | Outros: {len(paths_outros)}")


def main():
    out = os.environ.get("MAP_OUT", "web/fl-counties.svg")
    gj = baixar_geojson()
    feats = filtrar_fl(gj)
    if len(feats) < 60:
        print(f"[map] AVISO: apenas {len(feats)} condados FL encontrados (esperado ~67)")
    gerar_svg(feats, out)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[map] ERRO: {e}", file=sys.stderr)
        sys.exit(1)
