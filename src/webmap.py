"""Interactive standalone web map (Folium / Leaflet) -- the final deliverable ``index.html``.

Purpose : present every layer, statistic and route comparison in one browser page
          that works offline from the file itself (only the Leaflet/Bootstrap CDN
          scripts and the background basemap tiles need internet).
Method  : the exported 100 m rasters are colour-mapped to PNGs and embedded as
          Leaflet image overlays (so the map never depends on expiring Earth
          Engine tile tokens); the same rasters are embedded as quantised byte
          arrays so a click anywhere returns the risk class and index at that
          pixel. A custom control panel provides layer toggles, opacity
          sliders, legend, risk-class statistics, MCDM weights, landslide
          validation and the shortest-vs-least-risk route comparison.
          Running ``python -m src.webmap`` executes the whole pipeline first.
References:
  * Agafonkin, Leaflet (leafletjs.com); Folium (python-visualization.github.io/folium).
  * Wu 2020, geemap -- Earth Engine interactive mapping (JOSS 5(51), 2305); used in
    demo.ipynb for interactive exploration (same Leaflet stack as this map).
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import sys
from pathlib import Path

import folium
import geopandas as gpd
import numpy as np
from branca.element import Element, MacroElement, Template
from PIL import Image

import config
from src.rasters import RasterStack
from src.routing import RoutingResult
from src.webdata import build_webgraph, facilities_payload

log = logging.getLogger(__name__)

RAMPS = {
    "risk": config.RISK_RAMP,
    "flood": ["#f7fbff", "#9ecae1", "#4292c6", "#08519c", "#08306b"],
    "landslide": ["#fff7bc", "#d9a441", "#a6611a", "#6b3a1e", "#3b1f0e"],
    "fire": ["#ffffb2", "#fecc5c", "#fd8d3c", "#f03b20", "#bd0026"],
    "exposure": ["#f2f0f7", "#cbc9e2", "#9e9ac8", "#756bb1", "#54278f"],
}
RASTER_LAYERS = (  # (key, label, visible by default)
    ("risk", "Composite risk index", True),
    ("flood", "Flood hazard (Sentinel-1 SAR)", False),
    ("landslide", "Landslide susceptibility", False),
    ("fire", "Fire hazard (MODIS/VIIRS density)", False),
    ("exposure", "Exposure (built area + roads)", False),
)


# --------------------------------------------------------------------------- #
# Raster rendering
# --------------------------------------------------------------------------- #
def _hex_to_rgb(h: str) -> tuple[int, int, int]:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def ramp_color(value: float, stops: tuple | list) -> str:
    """Hex colour at ``value`` (0-1) along an evenly spaced multi-stop ramp."""
    value = min(max(value, 0.0), 1.0)
    pos = value * (len(stops) - 1)
    i = min(int(pos), len(stops) - 2)
    t = pos - i
    a, b = _hex_to_rgb(stops[i]), _hex_to_rgb(stops[i + 1])
    return "#%02x%02x%02x" % tuple(int(round(a[k] + (b[k] - a[k]) * t)) for k in range(3))


def colorize(arr: np.ndarray, stops: list[str], solid_alpha: float | None = None) -> np.ndarray:
    """Map a 0-1 array to RGBA uint8; NaN pixels are fully transparent."""
    valid = np.isfinite(arr)
    v = np.clip(np.nan_to_num(arr, nan=0.0), 0, 1)
    pos = v * (len(stops) - 1)
    idx = np.minimum(pos.astype(int), len(stops) - 2)
    t = (pos - idx)[..., None]
    rgb = np.array([_hex_to_rgb(s) for s in stops], dtype=float)
    out = rgb[idx] + (rgb[idx + 1] - rgb[idx]) * t
    alpha = np.full(arr.shape, solid_alpha if solid_alpha is not None else 0.0)
    if solid_alpha is None:
        alpha = 0.30 + 0.70 * v               # low values stay see-through so the basemap shows
    alpha = np.where(valid, alpha, 0.0)
    return np.dstack([out, alpha * 255]).astype("uint8")


def png_data_uri(rgba: np.ndarray) -> str:
    """Encode an RGBA array as a base64 PNG data URI."""
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def quantise(arr: np.ndarray) -> str:
    """Base64 of a 0-1 array quantised to bytes 0..254 (255 = no data) for click look-ups."""
    q = np.where(np.isfinite(arr), np.round(np.clip(arr, 0, 1) * 254), 255).astype("uint8")
    return base64.b64encode(q.tobytes()).decode("ascii")


# --------------------------------------------------------------------------- #
# HTML fragments for the side panel
# --------------------------------------------------------------------------- #
def _swatch(color: str) -> str:
    return f'<span class="sw" style="background:{color}"></span>'


def stats_table_html(stats: list[dict]) -> str:
    rows = "".join(
        f"<tr><td>{_swatch(config.RISK_CLASS_COLORS[r['class_id'] - 1])}{r['class_name']}</td>"
        f"<td>{r['index_min']:.3f}&ndash;{r['index_max']:.3f}</td><td class='n'>{r['area_km2']:,.1f}</td>"
        f"<td class='n'>{r['percent_of_study_area']:.1f}%</td></tr>" for r in stats)
    return ("<table><thead><tr><th>Risk class</th><th>Index range</th><th>Area km&sup2;</th><th>% of area</th></tr>"
            f"</thead><tbody>{rows}</tbody></table>")


def weights_table_html(table: dict, ahp: dict) -> str:
    cols = ["AHP", "Entropy", "AHP+Entropy (used)", "CRITIC"]
    head = "".join(f"<th>{c.replace('AHP+Entropy (used)', 'Combined')}</th>" for c in cols)
    rows = "".join(f"<tr><td>{crit.title()}</td>" + "".join(f"<td class='n'>{vals[c]:.3f}</td>" for c in cols) + "</tr>"
                   for crit, vals in table.items())
    return (f"<table><thead><tr><th>Criterion</th>{head}</tr></thead><tbody>{rows}</tbody></table>"
            f"<p class='note'>AHP consistency ratio CR = <b>{ahp['cr']:.3f}</b> (&lt; {config.AHP_MAX_CR}, "
            f"&lambda;<sub>max</sub> = {ahp['lambda_max']:.3f}). Combined = mean(AHP, entropy); CRITIC shown for sensitivity.</p>")


def validation_html(val: dict | None) -> str:
    if not val:
        return "<p class='note'>Validation results not available.</p>"
    out = [f"<p class='note'>{config.EVENT_NAME}: is the {config.EVENT_BUFFER_M/1000:.0f} km zone around the event "
           f"classified High / Very High?</p>"]
    for r in val["results"]:
        if r.get("outside_study_area"):
            out.append(f"<div class='val fail'><b>N/A</b> &mdash; {r['label']} point ({r['lat']:.3f}&deg;N, "
                       f"{r['lon']:.3f}&deg;E)<br>The coordinate lies outside the study area, so no zone could be tested.</div>")
            continue
        cls = "pass" if r["passed"] else "fail"
        out.append(
            f"<div class='val {cls}'><b>{'PASS' if r['passed'] else 'FAIL'}</b> &mdash; {r['label']} point "
            f"({r['lat']:.3f}&deg;N, {r['lon']:.3f}&deg;E)<br>Point class: {r['point_class_name']}; modal zone class: "
            f"{r['zone_modal_class']}; {100 * r['zone_fraction_high_or_very_high']:.0f}% of zone High/Very High.</div>")
    return "".join(out)


def routes_table_html(metrics) -> str:
    if metrics is None or len(metrics) == 0:
        return "<p class='note'>No routes computed.</p>"
    rows = ""
    for r in metrics.itertuples():
        rows += (f"<tr><td>{r.origin}<br><small>&rarr; {r.destination_type}: {r.destination}</small></td>"
                 f"<td class='n'>{r.shortest_km:.1f}</td><td class='n'>{r.least_risk_km:.1f}</td>"
                 f"<td class='n'>{r.distance_increase_pct:+.1f}%</td>"
                 f"<td class='n'>{r.shortest_mean_risk:.2f}&rarr;{r.least_risk_mean_risk:.2f}</td>"
                 f"<td class='n good'>{-r.mean_risk_reduction_pct:+.1f}%</td></tr>")
    return ("<table class='routes'><thead><tr><th>Origin &rarr; destination</th><th>Short km</th><th>Safe km</th>"
            "<th>&Delta; dist</th><th>Mean risk</th><th>&Delta; risk</th></tr></thead>"
            f"<tbody>{rows}</tbody></table><p class='note'>Short = pure-length Dijkstra (dashed grey). "
            f"Safe = least-risk Dijkstra, cost = length &times; (1 + {config.ROUTE_ALPHA:g} &times; mean risk) "
            "(solid green).</p>")


def legend_html(breaks: list[float]) -> str:
    grad = ", ".join(config.RISK_RAMP)
    classes = "".join(f"<div>{_swatch(c)}{n}</div>" for n, c in zip(config.RISK_CLASS_NAMES, config.RISK_CLASS_COLORS))
    return (f"<div class='ramp' style='background:linear-gradient(90deg,{grad})'></div>"
            "<div class='ramp-lab'><span>0 (low)</span><span>Risk index</span><span>1 (high)</span></div>"
            f"<div class='classes'>{classes}</div>"
            "<div class='keys'>"
            f"<div><span class='line' style='border-top:3px dashed {config.ROUTE_SHORTEST_STYLE['color']}'></span>Shortest route</div>"
            f"<div><span class='line' style='border-top:4px solid {config.ROUTE_LEASTRISK_STYLE['color']}'></span>Least-risk route</div>"
            "<div><span class='line' style='border-top:3px solid #7f0000'></span>Hotspot (Very High)</div></div>")


# --------------------------------------------------------------------------- #
# Static page assets
# --------------------------------------------------------------------------- #
CSS = """
html,body{height:100%;margin:0;font-family:'Segoe UI',Roboto,Helvetica,Arial,sans-serif;color:#1f2933}
.folium-map{position:absolute !important;top:54px;bottom:0;left:0;right:0;height:auto !important;width:auto !important}
#hdr{position:fixed;top:0;left:0;right:0;height:54px;background:#0f2a43;color:#fff;z-index:1200;display:flex;
 align-items:center;padding:0 16px;box-shadow:0 2px 6px rgba(0,0,0,.35)}
#hdr h1{font-size:17px;font-weight:600;margin:0;letter-spacing:.2px}
#hdr .sub{font-size:11px;opacity:.75;margin-left:14px;white-space:nowrap}
#panel{position:fixed;top:54px;right:0;bottom:0;width:410px;max-width:92vw;background:#fff;z-index:1100;overflow-y:auto;
 box-shadow:-2px 0 8px rgba(0,0,0,.25);transition:transform .25s}
#panel.closed{transform:translateX(100%)}
#ptoggle{position:fixed;top:66px;right:410px;z-index:1150;background:#0f2a43;color:#fff;border:0;padding:8px 10px;
 border-radius:6px 0 0 6px;cursor:pointer;font-size:13px;transition:right .25s}
#ptoggle.closed{right:0}
#panel details{border-bottom:1px solid #e4e7eb;padding:6px 14px}
#panel summary{cursor:pointer;font-weight:600;font-size:14px;padding:6px 0;color:#0f2a43}
#panel table{border-collapse:collapse;width:100%;font-size:12px;margin:6px 0}
#panel th{background:#f0f4f8;text-align:left;padding:4px 5px;font-weight:600}
#panel td{padding:4px 5px;border-top:1px solid #eceff3;vertical-align:top}
#panel td.n{text-align:right;white-space:nowrap}
#panel td.good{color:#067d34;font-weight:600}
.note{font-size:11.5px;color:#52606d;margin:6px 0}
.sw{display:inline-block;width:12px;height:12px;border-radius:2px;margin-right:6px;vertical-align:-1px;border:1px solid rgba(0,0,0,.25)}
.lrow{display:flex;align-items:center;gap:8px;padding:3px 0;font-size:12.5px}
.lrow label{flex:1;cursor:pointer}
.lrow input[type=range]{width:90px}
.lgroup{font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:#7b8794;margin-top:8px}
.ramp{height:14px;border-radius:3px;border:1px solid #ccc}
.ramp-lab{display:flex;justify-content:space-between;font-size:11px;color:#52606d;margin:2px 0 8px}
.classes{display:grid;grid-template-columns:1fr 1fr;gap:3px 10px;font-size:12px}
.keys{margin-top:8px;font-size:12px}.keys .line{display:inline-block;width:30px;margin-right:8px;vertical-align:middle}
.val{font-size:12px;padding:6px 8px;border-radius:4px;margin:6px 0;border-left:4px solid}
.val.pass{background:#e6f6ec;border-color:#1a9850}.val.fail{background:#fdecea;border-color:#c62828}
.pp{font-size:12.5px;line-height:1.45}.pp b{font-size:13px}
.pp table{border-collapse:collapse;margin-top:4px}.pp td{padding:1px 8px 1px 0}
small{color:#52606d}
#hdr .to3d{margin-left:auto;color:#bce6a1;text-decoration:none;font-size:12.5px;border:1px solid #3d5a49;border-radius:6px;padding:6px 12px;white-space:nowrap}
#hdr .to3d:hover{background:#17384f}
.embed #hdr{display:none}.embed .folium-map{top:0 !important}.embed #panel{top:0}.embed #ptoggle{top:12px}.embed .srch{top:12px}
.srch{position:fixed;top:64px;left:64px;width:350px;max-width:calc(100vw - 90px);background:#fff;z-index:1090;border-radius:8px;
 box-shadow:0 2px 12px rgba(0,0,0,.35);padding:9px 10px;font-size:12.5px}
.srch-row{display:flex;gap:5px;align-items:center;margin-bottom:5px;position:relative}
.srch-row input{flex:1;min-width:0;border:1px solid #c5ccd3;border-radius:5px;padding:6px 8px;font-size:12.5px}
.srch-row button,.srch-opts button{border:1px solid #c5ccd3;background:#f4f6f8;border-radius:5px;padding:5px 8px;cursor:pointer;font-size:12px}
.srch-opts button#srch-go{background:#0f2a43;color:#fff;border-color:#0f2a43;font-weight:600}
.srch-opts{display:flex;flex-wrap:wrap;gap:5px;align-items:center;font-size:11.5px;color:#334}
.srch-opts select{font-size:11.5px;padding:2px;border:1px solid #c5ccd3;border-radius:4px}
.dotl{display:inline-block;width:20px;height:20px;border-radius:50%;color:#fff;font-weight:700;font-size:11px;text-align:center;line-height:20px;flex:none}
.sug{display:none;position:absolute;left:25px;right:0;top:100%;background:#fff;border:1px solid #c5ccd3;border-radius:5px;z-index:1200;
 max-height:230px;overflow-y:auto;box-shadow:0 4px 10px rgba(0,0,0,.25)}
.sug div{padding:5px 8px;cursor:pointer;border-bottom:1px solid #eef0f2}.sug div:hover{background:#eaf2fb}.sug small{margin-left:6px}
#srch-msg{font-size:11.5px;color:#b3261e;margin-top:4px}
#srch-summary{display:none;margin-top:6px;padding:6px 8px;background:#f1f6f2;border-left:3px solid #1a9850;font-size:12px;line-height:1.5}
.good{color:#067d34;font-weight:600}
.pin{width:26px;height:26px;border-radius:50%;color:#fff;font-weight:700;text-align:center;line-height:26px;border:2px solid #fff;box-shadow:0 1px 5px rgba(0,0,0,.5)}
.switch2{display:flex;align-items:center;gap:8px;font-weight:600;font-size:13px;margin:4px 0 8px;cursor:pointer}
.switch2 input{width:34px;height:18px;accent-color:#0f2a43}
#fac-body.off{opacity:.45;pointer-events:none}
.fcat{display:flex;align-items:center;gap:6px;padding:2px 0;font-size:12.5px;cursor:pointer}
.fcat small{margin-left:auto}
.fbtns{display:flex;gap:6px;margin:6px 0}.fbtns button{border:1px solid #c5ccd3;background:#f4f6f8;border-radius:5px;padding:3px 8px;font-size:11.5px;cursor:pointer}
.chips{display:flex;flex-wrap:wrap;gap:4px;margin:4px 0 8px}.chip{background:#eef2f6;border-radius:10px;padding:2px 8px;font-size:11px}
.rlist{max-height:340px;overflow-y:auto;border-top:1px solid #e4e7eb}
.ritem{padding:5px 2px;border-bottom:1px solid #eef0f2;cursor:pointer;font-size:12px;display:grid;grid-template-columns:48px 14px 1fr;column-gap:4px}
.ritem:hover{background:#f1f6fb}.ritem .km{color:#52606d;text-align:right}.ritem small{grid-column:3}
"""

JS = r"""
(function(){
  var map = window[MAP_NAME];
  // place the map below the fixed header (inline styles written by folium would otherwise win)
  // ?embed=1 (used by the 3D site's "Detailed map" tab) hides this page's own header
  var EMBED = /[?&]embed=1/.test(location.search); if (EMBED) document.body.classList.add('embed');
  var el = map.getContainer(); el.style.position='absolute'; el.style.top = EMBED ? '0' : '54px'; el.style.left='0'; el.style.right='0';
  el.style.bottom='0'; el.style.width='auto'; el.style.height='auto'; map.invalidateSize();
  var DATA = RASTER_DATA, ROWS = DATA.rows, COLS = DATA.cols, B = DATA.bounds;   // [west,south,east,north]
  function decode(b64){ var s = atob(b64), a = new Uint8Array(s.length); for (var i=0;i<s.length;i++) a[i]=s.charCodeAt(i); return a; }
  var grids = {}; Object.keys(DATA.grids).forEach(function(k){ grids[k] = decode(DATA.grids[k]); });
  var classes = decode(DATA.classes);

  // ---- layer panel -------------------------------------------------------
  var box = document.getElementById('layer-rows'), lastGroup = null;
  LAYERS.forEach(function(l){
    var layer = window[l.var];
    if (l.group !== lastGroup){ var g = document.createElement('div'); g.className='lgroup'; g.textContent=l.group; box.appendChild(g); lastGroup = l.group; }
    if (!l.visible && layer && map.hasLayer(layer)) map.removeLayer(layer);
    var row = document.createElement('div'); row.className='lrow';
    var cb = document.createElement('input'); cb.type='checkbox'; cb.checked = !!l.visible; cb.id='cb_'+l.var;
    var lab = document.createElement('label'); lab.setAttribute('for', cb.id); lab.innerHTML = (l.swatch ? '<span class="sw" style="background:'+l.swatch+'"></span>' : '') + l.label;
    row.appendChild(cb); row.appendChild(lab);
    cb.addEventListener('change', function(){ if (cb.checked) map.addLayer(layer); else map.removeLayer(layer); });
    if (l.opacity !== null && l.opacity !== undefined){
      var sl = document.createElement('input'); sl.type='range'; sl.min=0; sl.max=1; sl.step=0.05; sl.value=l.opacity; sl.title='Opacity';
      sl.addEventListener('input', function(){
        var v = parseFloat(sl.value);
        if (l.type === 'raster') layer.setOpacity(v);
        else layer.eachLayer(function(x){ if (x.setStyle) x.setStyle({opacity:v, fillOpacity:v*(l.fill||0.3)}); });
      });
      row.appendChild(sl);
    }
    box.appendChild(row);
  });

  // ---- panel toggle ------------------------------------------------------
  var panel = document.getElementById('panel'), tg = document.getElementById('ptoggle');
  tg.addEventListener('click', function(){ var c = panel.classList.toggle('closed'); tg.classList.toggle('closed', c); tg.textContent = c ? '◀ Panel' : '▶'; });

  // ---- click anywhere: risk class + index --------------------------------
  var NAMES = DATA.class_names, COLORS = DATA.class_colors;
  function val(name, idx){ var v = grids[name][idx]; return v === 255 ? null : v/254; }
  map.on('click', function(e){
    if (window.__pickMode) return;
    var w=B[0], s=B[1], ea=B[2], n=B[3];
    if (e.latlng.lng < w || e.latlng.lng > ea || e.latlng.lat < s || e.latlng.lat > n) return;
    var c = Math.floor((e.latlng.lng - w)/(ea - w)*COLS), r = Math.floor((n - e.latlng.lat)/(n - s)*ROWS);
    if (c<0||c>=COLS||r<0||r>=ROWS) return;
    var idx = r*COLS + c, cls = classes[idx];
    if (!cls){ return; }
    var risk = val('risk', idx);
    var h = '<div class="pp"><b>Risk class: <span style="color:'+COLORS[cls-1]+'">'+NAMES[cls-1]+'</span></b><br>'+
            'Composite risk index: <b>'+(risk===null?'n/a':risk.toFixed(3))+'</b>'+
            '<table>';
    DATA.layer_labels.forEach(function(p){ var v = val(p[0], idx); h += '<tr><td>'+p[1]+'</td><td>'+(v===null?'n/a':v.toFixed(3))+'</td></tr>'; });
    h += '</table><small>'+e.latlng.lat.toFixed(4)+'&deg;N, '+e.latlng.lng.toFixed(4)+'&deg;E</small></div>';
    L.popup({maxWidth:300}).setLatLng(e.latlng).setContent(h).openOn(map);
  });
})();
"""


class _PanelScript(MacroElement):
    """Injects the control-panel JavaScript after the map and all layers are defined."""

    _template = Template("{% macro script(this, kwargs) %}{{ this.js }}{% endmacro %}")

    def __init__(self, js: str):
        super().__init__()
        self._name = "PanelScript"
        self.js = js


def _json(obj) -> str:
    """Compact JSON safe to embed inside a <script> block."""
    return json.dumps(obj, separators=(",", ":")).replace("</", "<\\/")


def search_box_html() -> str:
    corridor = "".join(f"<option value='{k}'{' selected' if k == config.SEARCH_CORRIDOR_DEFAULT_KM else ''}>{k} km</option>"
                       for k in config.SEARCH_CORRIDOR_KM)
    return f"""<div class="srch" id="srch">
 <div class="srch-row"><span class="dotl" style="background:#2e7d32">A</span><input id="srch-from" autocomplete="off"
   placeholder="From: place, facility or lat, lon"><button id="pick-from" title="Pick on the map">&#8982;</button><div class="sug" id="sug-from"></div></div>
 <div class="srch-row"><span class="dotl" style="background:#c62828">B</span><input id="srch-to" autocomplete="off"
   placeholder="To: place, facility or lat, lon"><button id="pick-to" title="Pick on the map">&#8982;</button><button id="srch-swap" title="Swap A and B">&#8645;</button><div class="sug" id="sug-to"></div></div>
 <div class="srch-opts">Find places within <select id="srch-corr">{corridor}</select> of the
   <select id="srch-mode"><option value="safe">least-risk route</option><option value="short">shortest route</option></select>
   <button id="srch-go">Search</button><button id="srch-clear">Clear</button></div>
 <div id="srch-msg"></div><div id="srch-summary"></div>
</div>"""


def facilities_panel_html(payload: dict | None, has_roads: bool) -> str:
    """Right-panel section with the master toggle for all facilities and roads, plus per-category switches."""
    if not payload and not has_roads:
        return ""
    rows = "".join(
        f"<label class='fcat'><input type='checkbox' class='fac-cat' data-i='{i}' checked>"
        f"<span class='sw' style='background:{c['color']}'></span>{c['label']}<small>{c['count']}</small></label>"
        for i, c in enumerate((payload or {}).get("cats", [])))
    roads = ("<label class='fcat'><input type='checkbox' id='fac-roads' checked>"
             "<span class='sw' style='background:#7a2e0e'></span>All roads (major / minor / local)</label>") if has_roads else             "<input type='checkbox' id='fac-roads' hidden>"
    return f"""<details open><summary>Facilities &amp; roads (details)</summary>
  <label class="switch2"><input type="checkbox" id="fac-master" checked> Show all facilities, important places &amp; roads</label>
  <div id="fac-body">{roads}<div class="fbtns"><button id="fac-all">All categories</button><button id="fac-none">None</button></div>{rows}</div>
  <p class="note">Toggle the whole detail layer on or off. Click a marker for its name and category. Data: OpenStreetMap.</p></details>"""



# --------------------------------------------------------------------------- #
# Map assembly
# --------------------------------------------------------------------------- #
def _geojson_layer(gdf: gpd.GeoDataFrame, name: str, style: dict, tooltip_fields: list[str] | None = None,
                   aliases: list[str] | None = None, style_fn=None) -> folium.GeoJson:
    tip = folium.GeoJsonTooltip(fields=tooltip_fields, aliases=aliases, sticky=True) if tooltip_fields else None
    return folium.GeoJson(json.loads(gdf.to_json()), name=name, style_function=style_fn or (lambda f: style),
                          tooltip=tip, control=False, smooth_factor=1.0)


def _marker(lat: float, lon: float, name: str, popup: str, color: str, icon: str) -> folium.Marker:
    return folium.Marker([lat, lon], tooltip=name or None, popup=folium.Popup(popup, max_width=260),
                         icon=folium.Icon(color=color, icon=icon))


def build_map(stack: RasterStack, summary: dict, routing: RoutingResult, hotspots: gpd.GeoDataFrame,
              out_path=config.INDEX_HTML, live_layers: dict | None = None,
              facilities: list | None = None) -> folium.Map:
    """Assemble and write the standalone interactive map; returns the folium.Map.

    ``live_layers`` ({label: ee.Image}) optionally adds live Earth Engine tile layers
    (0-1 images, risk ramp). Their tile tokens expire after roughly a day, so they are
    off by default and only meant for exploring a freshly built map.
    """
    west, south, east, north = stack.bounds
    bounds = [[south, west], [north, east]]
    m = folium.Map(location=list(config.MAP_CENTER), zoom_start=config.MAP_ZOOM, tiles=None, control_scale=True,
                   prefer_canvas=True, zoom_control=True)
    folium.TileLayer("OpenStreetMap", name="OpenStreetMap", control=True).add_to(m)
    folium.TileLayer("CartoDB positron", name="Light basemap", control=True, show=False).add_to(m)
    folium.TileLayer(
        "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
        attr="Esri World Imagery", name="Satellite (Esri)", control=True, show=False).add_to(m)
    folium.LayerControl(position="topleft", collapsed=True).add_to(m)
    m.fit_bounds(bounds)

    layers_meta: list[dict] = []

    # ---- raster layers ----------------------------------------------------
    for key, label, visible in RASTER_LAYERS:
        if key not in stack.arrays:
            log.warning("Raster '%s' missing -- layer skipped", key)
            continue
        rgba = colorize(stack.arrays[key], RAMPS[key], solid_alpha=0.88 if key == "risk" else None)
        ov = folium.raster_layers.ImageOverlay(image=png_data_uri(rgba), bounds=bounds, opacity=config.MAP_LAYER_OPACITY,
                                               name=label, control=False, interactive=False, zindex=3)
        ov.add_to(m)
        layers_meta.append({"var": ov.get_name(), "label": label, "type": "raster", "visible": visible,
                            "opacity": config.MAP_LAYER_OPACITY, "group": "Risk & hazard rasters (0–1)",
                            "swatch": RAMPS[key][3]})

    for label, image in (live_layers or {}).items():
        url = image.getMapId({"min": 0, "max": 1, "palette": list(config.RISK_RAMP)})["tile_fetcher"].url_format
        tl = folium.TileLayer(tiles=url, attr="Google Earth Engine", name=label, overlay=True, control=False,
                              opacity=config.MAP_LAYER_OPACITY)
        tl.add_to(m)
        layers_meta.append({"var": tl.get_name(), "label": label, "type": "raster", "visible": False,
                            "opacity": config.MAP_LAYER_OPACITY, "group": "Live Earth Engine tiles (token expires)",
                            "swatch": "#4285f4"})

    # ---- vector layers ----------------------------------------------------
    if not hotspots.empty:
        hs = _geojson_layer(hotspots[["hotspot_id", "area_km2", "mean_risk", "built_area_km2", "geometry"]],
                            "Hotspots", {"color": "#7f0000", "weight": 2, "fillColor": "#e31a1c", "fillOpacity": 0.25},
                            ["hotspot_id", "area_km2", "mean_risk", "built_area_km2"],
                            ["Hotspot #", "Area (km²)", "Mean risk", "Built-up (km²)"])
        hs.add_to(m)
        layers_meta.append({"var": hs.get_name(), "label": "High-risk hotspots (Very High class)", "type": "vector",
                            "visible": True, "opacity": 1.0, "fill": 0.25, "group": "Zones & network", "swatch": "#7f0000"})

    if not routing.roads.empty:
        rd = _geojson_layer(routing.roads, "Roads", {}, ["highway", "risk"], ["Road class", "Mean risk"],
                            style_fn=lambda f: {"color": ramp_color(f["properties"]["risk"], config.RISK_RAMP),
                                                "weight": 2, "opacity": 0.8})
        rd.add_to(m)
        layers_meta.append({"var": rd.get_name(), "label": "Major roads (coloured by risk)", "type": "vector",
                            "visible": False, "opacity": 0.8, "fill": 0.0, "group": "Zones & network", "swatch": "#888888"})

    for rtype, label, style in (("shortest", "Shortest route (dashed grey)", config.ROUTE_SHORTEST_STYLE),
                                ("least_risk", "Least-risk route (solid green)", config.ROUTE_LEASTRISK_STYLE)):
        sub = routing.routes[routing.routes["route_type"] == rtype]
        if sub.empty:
            continue
        rt = _geojson_layer(sub.drop(columns=["route_type"]), label, style,
                            ["origin", "destination_type", "destination", "distance_km", "mean_risk", "max_risk"],
                            ["From", "To type", "To", "Distance (km)", "Mean risk", "Max risk"])
        rt.add_to(m)
        layers_meta.append({"var": rt.get_name(), "label": label, "type": "vector", "visible": True,
                            "opacity": style["opacity"], "fill": 0.0, "group": "Emergency routing", "swatch": style["color"]})

    # ---- markers ----------------------------------------------------------
    def add_group(label: str, group_name: str, color: str, build, visible: bool = True) -> None:
        fg = folium.FeatureGroup(name=label, control=False)
        build(fg)
        fg.add_to(m)
        layers_meta.append({"var": fg.get_name(), "label": label, "type": "markers", "visible": visible,
                            "opacity": None, "group": group_name, "swatch": color})

    def hospitals(fg):
        for r in routing.hospitals.itertuples():
            _marker(r.geometry.y, r.geometry.x, r.name, f"<b>Hospital</b><br>{r.name or 'unnamed'}", "red", "plus-sign").add_to(fg)

    def shelters(fg):
        for r in routing.shelters.itertuples():
            _marker(r.geometry.y, r.geometry.x, r.name, f"<b>Shelter / assembly point</b><br>{r.name or 'unnamed'}",
                    "green", "home").add_to(fg)

    def origins(fg):
        for r in routing.origins.itertuples():
            _marker(r.lat, r.lon, r.name, f"<b>Origin #{r.origin_id}: {r.name}</b><br>Settlement cluster, mean risk "
                    f"{r.mean_risk:.3f}", "orange", "star").add_to(fg)

    def events(fg):
        for lbl, (lat, lon) in (("published event location", config.EVENT_POINT_VERIFIED),
                                ("coordinate given in the project brief", config.EVENT_POINT_SPEC)):
            _marker(lat, lon, f"{config.EVENT_NAME} ({lbl})",
                    f"<b>{config.EVENT_NAME}</b><br>{lbl}<br>{lat:.3f}&deg;N, {lon:.3f}&deg;E", "black",
                    "warning-sign").add_to(fg)
            folium.Circle([lat, lon], radius=config.EVENT_BUFFER_M, color="#000000", weight=2, dash_array="4 6",
                          fill=False).add_to(fg)

    add_group("Hospitals", "Points of interest", "#d43f3a", hospitals)
    # the detailed facilities layer already shows shelters, schools and community centres: hide the legacy pins by default
    add_group("Shelters / assembly points", "Points of interest", "#5cb85c", shelters, visible=not facilities)
    add_group("Origins: highest-risk settlements", "Points of interest", "#f0ad4e", origins)
    add_group("July 2024 landslide event + 2 km zone", "Validation", "#000000", events)

    # ---- embedded data for click look-ups ---------------------------------
    rows, cols = stack.shape
    raster_data = {
        "rows": rows, "cols": cols, "bounds": [west, south, east, north],
        "grids": {k: quantise(stack.arrays[k]) for k, _l, _v in RASTER_LAYERS if k in stack.arrays},
        "classes": base64.b64encode((stack.classes if stack.classes is not None
                                     else np.zeros(stack.shape, "uint8")).astype("uint8").tobytes()).decode("ascii"),
        "class_names": list(config.RISK_CLASS_NAMES), "class_colors": list(config.RISK_CLASS_COLORS),
        "layer_labels": [[k, l.split(" (")[0]] for k, l, _v in RASTER_LAYERS if k in stack.arrays and k != "risk"],
    }

    hs_sum = summary.get("hotspots", {})
    hs_html = (f"<p class='note'><b>{hs_sum.get('n_hotspots', 0)}</b> hotspot polygons cover "
               f"<b>{hs_sum.get('total_area_km2', 0)}</b> km&sup2; ({hs_sum.get('percent_of_study_area', 0)}% of the study area); "
               f"<b>{hs_sum.get('built_area_km2', 0)}</b> km&sup2; of built-up land lies inside them.</p>")

    fac_payload = facilities_payload(facilities) if facilities else None
    webgraph = None
    if getattr(routing, "graph", None) is not None:
        try:
            webgraph = build_webgraph(routing.graph)
        except Exception as err:  # noqa: BLE001 - the map still works without in-browser routing
            log.warning("Could not build the in-browser road graph (%s); search falls back to a straight corridor", err)
    search_box = search_box_html()
    facilities_panel = facilities_panel_html(fac_payload, webgraph is not None)

    panel = f"""
<div id="hdr"><h1>{config.MAP_TITLE}</h1>
 <span class="sub">Satellite data &rarr; indicator / model &rarr; recommendation</span>
 <a class="to3d" href="frontend/index.html" title="Open the 3D terrain viewer">&#9672; 3D terrain view &nearr;</a></div>
<button id="ptoggle" title="Show / hide panel">&#9654;</button>
{search_box}
<div id="panel">
 {facilities_panel}
 <details open id="route-det"><summary>Route &amp; nearby facilities</summary><div id="route-results"><p class="note">Search two places (top-left box) to list the facilities and important places between them, along the shortest and least-risk road routes.</p></div></details>
 <details open><summary>Layers &amp; opacity</summary><div id="layer-rows"></div>
   <p class="note">Click anywhere on the map for the risk class and index at that pixel. Basemaps: top-left control.</p></details>
 <details open><summary>Legend</summary>{legend_html(summary.get('breaks', []))}</details>
 <details open><summary>Risk-class statistics</summary>{stats_table_html(summary['stats'])}{hs_html}
   <p class="note">Study area: {summary.get('study_area_km2', 0):,.0f} km&sup2;. Classes by {config.RISK_CLASSIFICATION.replace('_', ' ')}.</p></details>
 <details open><summary>Route comparison: distance vs risk</summary>{routes_table_html(routing.metrics)}</details>
 <details><summary>Landslide model validation</summary>{validation_html(summary.get('validation'))}</details>
 <details><summary>MCDM weights</summary>{weights_table_html(summary['weight_table'], summary['ahp'])}</details>
 <details><summary>Method &amp; data</summary><p class="note">Flood: Sentinel-1 VV, Otsu threshold, JRC permanent-water and slope&gt;5&deg; masks.
 Landslide: SRTM slope/elevation + Sentinel-2 NDVI + CHIRPS monsoon rainfall, Frequency-Ratio ratings.
 Fire: MODIS/VIIRS 10-yr active-fire density &times; Dynamic World fuel cover. Exposure: Dynamic World built area + road proximity.
 Layers normalised 0&ndash;1, combined by AHP + entropy weights into the composite index. Routes: OSMnx drive network, edge cost
 = length &times; (1 + &alpha; &times; mean risk), Dijkstra. See README for full references.</p></details>
</div>"""
    m.get_root().header.add_child(Element(f"<style>{CSS}</style>"))
    m.get_root().html.add_child(Element(panel))
    search_cfg = {"alpha": config.ROUTE_ALPHA, "snapMaxM": config.WEBGRAPH_SNAP_MAX_M,
                  "shortest": config.ROUTE_SHORTEST_STYLE, "safe": config.ROUTE_LEASTRISK_STYLE,
                  "roadColors": config.ROAD_CLASS_COLORS}
    search_js = (Path(__file__).parent / "static" / "search.js").read_text(encoding="utf-8")
    js = (f"var MAP_NAME = {json.dumps(m.get_name())};\nvar RASTER_DATA = {json.dumps(raster_data)};\n"
          f"var LAYERS = {json.dumps(layers_meta)};\n{JS}\n"
          f"var FACILITY_DATA = {_json(fac_payload)};\nvar WEBGRAPH = {_json(webgraph)};\n"
          f"var SEARCH_CONFIG = {_json(search_cfg)};\n{search_js}")
    m.add_child(_PanelScript(js))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))
    log.info("Interactive web map written to %s (%.1f MB)", out_path, out_path.stat().st_size / 1e6)
    return m


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    """Run the full pipeline (or rebuild from cached outputs) and write index.html."""
    parser = argparse.ArgumentParser(description="Wayanad multi-hazard risk & emergency routing web map")
    parser.add_argument("--from-cache", action="store_true",
                        help="rebuild the map from outputs/ without calling Earth Engine")
    parser.add_argument("--resume", action="store_true",
                        help="reuse the MCDM checkpoint, validation result and downloaded raster bands from a previous run")
    parser.add_argument("--live-tiles", action="store_true",
                        help="also add live Earth Engine tile layers (tokens expire after ~1 day)")
    args = parser.parse_args(argv)
    from src import pipeline
    from src.gee_init import GEEInitError
    try:
        pipeline.run(from_cache=args.from_cache, live_tiles=args.live_tiles, resume=args.resume)
    except GEEInitError as err:
        print(err, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
