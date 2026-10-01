/* Facilities & roads layers + two-place route search (runs inside the standalone Folium/Leaflet page).
 *
 * Globals injected by src/webmap.py: MAP_NAME, FACILITY_DATA, WEBGRAPH, SEARCH_CONFIG.
 *  - FACILITY_DATA: {cats:[{key,label,color,count}], pts:[[lon,lat,catIndex,name,sub],...]}
 *  - WEBGRAPH: compact undirected road graph (see src/webdata.py): typed arrays as base64.
 *  - SEARCH_CONFIG: {alpha, corridors, defaultCorridor, snapMaxM, shortest:{...}, safe:{...}, roadColors}
 * The router is Dijkstra on the junction graph with nearest-road snapping; edge cost for the least-risk route is
 * length * (1 + alpha * mean_risk), identical to the Python routing model. One-way streets are ignored.
 */
(function () {
  'use strict';
  var map = window[MAP_NAME];
  var FAC = window.FACILITY_DATA, WG = window.WEBGRAPH, CFG = window.SEARCH_CONFIG;
  if (!map || !CFG) return;
  var $ = function (id) { return document.getElementById(id); };
  var RAD = Math.PI / 180;

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]; }); }
  function norm(s) { return String(s).toLowerCase().normalize('NFD').replace(/[̀-ͯ]/g, ''); }
  function metersBetween(lat1, lon1, lat2, lon2) {
    var dx = (lon2 - lon1) * Math.cos((lat1 + lat2) / 2 * RAD) * 111320, dy = (lat2 - lat1) * 110570;
    return Math.sqrt(dx * dx + dy * dy);
  }
  function fmtKm(m) { return (m / 1000).toFixed(m < 10000 ? 2 : 1) + ' km'; }

  /* ------------------------------------------------------------------ facilities */
  var cats = FAC ? FAC.cats : [], pts = FAC ? FAC.pts : [];
  var canvas = L.canvas({padding: 0.4});
  var catLayers = cats.map(function () { return L.layerGroup(); });
  function popupHtml(p) {
    var c = cats[p[2]], title = p[3] || ('(unnamed ' + (p[4] || c.label).replace(/_/g, ' ') + ')');
    return '<div class="pp"><b>' + esc(title) + '</b><br><span style="color:' + c.color + '">&#9679;</span> ' + esc(c.label) +
      (p[4] ? ' &middot; ' + esc(String(p[4]).replace(/_/g, ' ')) : '') + '<br><small>' + p[1].toFixed(5) + '&deg;N, ' + p[0].toFixed(5) + '&deg;E</small></div>';
  }
  pts.forEach(function (p) {
    var c = cats[p[2]], isPlace = c.key === 'place';
    var m = L.circleMarker([p[1], p[0]], {renderer: canvas, radius: isPlace ? 3 : 5, color: '#ffffff', weight: isPlace ? 0.5 : 1,
      fillColor: c.color, fillOpacity: isPlace ? 0.7 : 0.92, bubblingMouseEvents: false});
    m.bindPopup(popupHtml(p), {maxWidth: 260});
    m.bindTooltip(p[3] || c.label, {direction: 'top', offset: [0, -4]});
    catLayers[p[2]].addLayer(m);
  });

  /* ------------------------------------------------------------------ road graph */
  var G = null;
  function typed(b64, T) {
    var s = atob(b64), a = new Uint8Array(s.length);
    for (var i = 0; i < s.length; i++) a[i] = s.charCodeAt(i);
    return new T(a.buffer, 0, a.length / T.BYTES_PER_ELEMENT);
  }
  if (WG) {
    var S = WG.scale, N = WG.n, E = WG.e;
    G = {N: N, E: E, nxy: typed(WG.nodes, Int32Array), eu: typed(WG.eu, Uint32Array), ev: typed(WG.ev, Uint32Array),
      len: typed(WG.len, Uint32Array), risk: typed(WG.risk, Uint8Array), cls: typed(WG.cls, Uint8Array),
      goff: typed(WG.goff, Uint32Array), gxy: typed(WG.gxy, Int32Array)};
    var start = new Uint32Array(N + 1), e, i;
    for (e = 0; e < E; e++) { start[G.eu[e] + 1]++; start[G.ev[e] + 1]++; }
    for (i = 0; i < N; i++) start[i + 1] += start[i];
    var fill = start.slice(0, N), adjN = new Uint32Array(2 * E), adjE = new Uint32Array(2 * E);
    for (e = 0; e < E; e++) {
      var u = G.eu[e], v = G.ev[e];
      adjN[fill[u]] = v; adjE[fill[u]++] = e; adjN[fill[v]] = u; adjE[fill[v]++] = e;
    }
    G.start = start; G.adjN = adjN; G.adjE = adjE;
    var V = G.gxy.length / 2; G.V = V; G.vEdge = new Uint32Array(V);
    for (e = 0; e < E; e++) for (i = G.goff[e]; i < G.goff[e + 1]; i++) G.vEdge[i] = e;
    G.cell = 0.005; G.grid = new Map();
    for (i = 0; i < V; i++) {
      var key = Math.floor(G.gxy[2 * i] / S / G.cell) * 4096 + Math.floor(G.gxy[2 * i + 1] / S / G.cell);
      var b = G.grid.get(key); if (b) b.push(i); else G.grid.set(key, [i]);
    }
  }
  function vLon(v) { return G.gxy[2 * v] / WG.scale; }
  function vLat(v) { return G.gxy[2 * v + 1] / WG.scale; }

  var roadLayer = null;
  function buildRoads() {
    if (roadLayer || !G) return;
    var groups = [[], [], []];
    for (var e = 0; e < G.E; e++) {
      var line = [];
      for (var i = G.goff[e]; i < G.goff[e + 1]; i++) line.push([vLat(i), vLon(i)]);
      groups[G.cls[e]].push(line);
    }
    var style = [{color: CFG.roadColors.major, weight: 3.2, opacity: 0.9}, {color: CFG.roadColors.minor, weight: 2.2, opacity: 0.85},
      {color: CFG.roadColors.local, weight: 1.2, opacity: 0.8}];
    roadLayer = L.layerGroup(groups.map(function (g, k) {
      // default (shared) canvas, pushed to the back below: roads sit under hotspots and routes
      return L.polyline(g, {color: style[k].color, weight: style[k].weight, opacity: style[k].opacity,
        smoothFactor: 1.3, interactive: false});
    }));
  }

  function nearestVertex(lon, lat, maxM) {
    var cx = Math.floor(lon / G.cell), cy = Math.floor(lat / G.cell), best = -1, bestD = Infinity;
    var cellM = G.cell * 110570, kmax = Math.ceil(maxM / cellM) + 1;
    for (var r = 0; r <= kmax; r++) {
      for (var dx = -r; dx <= r; dx++) for (var dy = -r; dy <= r; dy++) {
        if (Math.max(Math.abs(dx), Math.abs(dy)) !== r) continue;
        var list = G.grid.get((cx + dx) * 4096 + (cy + dy)); if (!list) continue;
        for (var j = 0; j < list.length; j++) {
          var d = metersBetween(lat, lon, vLat(list[j]), vLon(list[j]));
          if (d < bestD) { bestD = d; best = list[j]; }
        }
      }
      if (best >= 0 && bestD <= r * cellM) break;
    }
    return best < 0 ? null : {v: best, edge: G.vEdge[best], dist: bestD, lon: vLon(best), lat: vLat(best)};
  }
  function edgeOffsets(snap) {          // metres from the snapped vertex to each end of its road segment
    var e = snap.edge, a = G.goff[e], b = G.goff[e + 1] - 1, tot = 0, toV = 0;
    for (var i = a; i < b; i++) {
      var d = metersBetween(vLat(i), vLon(i), vLat(i + 1), vLon(i + 1));
      tot += d; if (i < snap.v) toV += d;
    }
    var f = tot > 0 ? G.len[e] / tot : 1;
    return {u: toV * f, v: (tot - toV) * f};
  }

  /* ------------------------------------------------------------------ Dijkstra */
  function Heap() { this.k = []; this.n = []; }
  Heap.prototype.push = function (key, node) {
    var k = this.k, n = this.n, i = k.length; k.push(key); n.push(node);
    while (i > 0) { var p = (i - 1) >> 1; if (k[p] <= k[i]) break; var tk = k[p], tn = n[p]; k[p] = k[i]; n[p] = n[i]; k[i] = tk; n[i] = tn; i = p; }
  };
  Heap.prototype.pop = function () {
    var k = this.k, n = this.n, rk = k[0], rn = n[0], lk = k.pop(), ln = n.pop(), len = k.length;
    if (len > 0) {
      k[0] = lk; n[0] = ln; var i = 0;
      for (;;) {
        var l = 2 * i + 1, r = l + 1, m = i;
        if (l < len && k[l] < k[m]) m = l; if (r < len && k[r] < k[m]) m = r; if (m === i) break;
        var tk = k[m], tn = n[m]; k[m] = k[i]; n[m] = n[i]; k[i] = tk; n[i] = tn; i = m;
      }
    }
    return [rk, rn];
  };

  function route(sa, sb, alpha) {
    var w = function (e, len) { return len * (1 + alpha * G.risk[e] / 254); };
    var oa = edgeOffsets(sa), ob = edgeOffsets(sb), ea = sa.edge, eb = sb.edge;
    var coords = [], edges = [], before;              // edges: [edgeId, lengthUsed]
    var addV = function (from, to) { var step = from <= to ? 1 : -1; for (var i = from; i !== to + step; i += step) coords.push([vLat(i), vLon(i)]); };
    if (ea === eb) {                                  // both points on the same road segment
      addV(sa.v, sb.v);
      return summarise(coords, [[ea, Math.abs(oa.u - ob.u)]]);
    }
    var dist = new Float64Array(G.N).fill(Infinity), prevE = new Int32Array(G.N).fill(-1), prevN = new Int32Array(G.N).fill(-1);
    var done = new Uint8Array(G.N), heap = new Heap();
    var us = G.eu[ea], vs = G.ev[ea];
    dist[us] = w(ea, oa.u); dist[vs] = Math.min(dist[vs], w(ea, oa.v)); heap.push(dist[us], us); heap.push(dist[vs], vs);
    var ut = G.eu[eb], vt = G.ev[eb];
    while (heap.k.length) {
      var top = heap.pop(), d = top[0], x = top[1];
      if (done[x]) continue; done[x] = 1;
      if (done[ut] && done[vt]) break;
      for (var q = G.start[x]; q < G.start[x + 1]; q++) {
        var y = G.adjN[q], ed = G.adjE[q], nd = d + w(ed, G.len[ed]);
        if (nd < dist[y]) { dist[y] = nd; prevE[y] = ed; prevN[y] = x; heap.push(nd, y); }
      }
    }
    var costU = dist[ut] + w(eb, ob.u), costV = dist[vt] + w(eb, ob.v);
    if (!isFinite(costU) && !isFinite(costV)) return null;
    var endNode = costU <= costV ? ut : vt, endOff = costU <= costV ? ob.u : ob.v;
    var chain = [], node = endNode;                   // walk back to a source node
    while (prevE[node] >= 0) { chain.push([prevE[node], node, prevN[node]]); node = prevN[node]; }
    chain.reverse();
    var startNode = node, startOff = startNode === us ? oa.u : oa.v;
    // start partial: snapped vertex -> start node along the first segment
    if (startNode === us) addV(sa.v, G.goff[ea]); else addV(sa.v, G.goff[ea + 1] - 1);
    edges.push([ea, startOff]);
    chain.forEach(function (c) {
      var ed = c[0], from = c[2], a = G.goff[ed], b = G.goff[ed + 1] - 1, before = coords.length;
      if (G.eu[ed] === from) addV(a, b); else addV(b, a);
      coords.splice(before, 1);                       // drop the duplicated joint vertex
      edges.push([ed, G.len[ed]]);
    });
    before = coords.length;
    if (endNode === ut) addV(G.goff[eb], sb.v); else addV(G.goff[eb + 1] - 1, sb.v);
    coords.splice(before, 1);
    edges.push([eb, endOff]);
    return summarise(coords, edges);
  }
  function summarise(coords, edges) {
    var len = 0, rsum = 0, rmax = 0;
    edges.forEach(function (x) { var r = G.risk[x[0]] / 254; len += x[1]; rsum += x[1] * r; if (x[1] > 0 && r > rmax) rmax = r; });
    return {coords: coords, length: len, meanRisk: len > 0 ? rsum / len : 0, maxRisk: rmax};
  }

  /* ------------------------------------------------------------------ search UI */
  var names = [];
  pts.forEach(function (p, i) { if (p[3]) names.push({i: i, n: norm(p[3]), place: cats[p[2]].key === 'place'}); });
  function suggest(q) {
    q = norm(q).trim(); if (q.length < 2) return [];
    var tokens = q.split(/\s+/), out = [];
    names.forEach(function (x) {
      for (var t = 0; t < tokens.length; t++) if (x.n.indexOf(tokens[t]) < 0) return;
      var score = x.n.indexOf(q) === 0 ? 0 : (x.n.indexOf(' ' + q) >= 0 ? 1 : 2);
      out.push({x: x, s: score - (x.place ? 0.6 : 0) + x.n.length / 400});
    });
    out.sort(function (a, b) { return a.s - b.s; });
    return out.slice(0, 8).map(function (o) { return o.x.i; });
  }
  function parseCoords(s) {
    var m = String(s).match(/^\s*(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)\s*$/); if (!m) return null;
    var a = parseFloat(m[1]), b = parseFloat(m[2]);
    return (a >= 5 && a <= 20 && b >= 70 && b <= 82) ? {lat: a, lon: b} : (b >= 5 && b <= 20 && a >= 70 && a <= 82) ? {lat: b, lon: a} : null;
  }
  var chosen = {from: null, to: null};
  function wire(which) {
    var input = $('srch-' + which), box = $('sug-' + which);
    function hide() { box.style.display = 'none'; box.innerHTML = ''; }
    input.addEventListener('input', function () {
      chosen[which] = null;
      var idx = suggest(input.value);
      if (!idx.length) { hide(); return; }
      box.innerHTML = idx.map(function (i) {
        var c = cats[pts[i][2]];
        return '<div data-i="' + i + '"><span style="color:' + c.color + '">&#9679;</span> ' + esc(pts[i][3]) + ' <small>' + esc(c.label) + '</small></div>';
      }).join('');
      box.style.display = 'block';
    });
    box.addEventListener('mousedown', function (ev) {
      var d = ev.target.closest('[data-i]'); if (!d) return; ev.preventDefault();
      var p = pts[+d.dataset.i]; chosen[which] = {lon: p[0], lat: p[1], label: p[3]}; input.value = p[3]; hide();
    });
    input.addEventListener('blur', function () { setTimeout(hide, 120); });
    input.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') { hide(); go(); } if (ev.key === 'Escape') hide(); });
  }
  function resolve(which) {
    if (chosen[which]) return chosen[which];
    var text = $('srch-' + which).value.trim(); if (!text) return null;
    var c = parseCoords(text); if (c) return {lon: c.lon, lat: c.lat, label: c.lat.toFixed(4) + ', ' + c.lon.toFixed(4)};
    var idx = suggest(text); if (idx.length) { var p = pts[idx[0]]; $('srch-' + which).value = p[3]; return {lon: p[0], lat: p[1], label: p[3]}; }
    return null;
  }
  ['from', 'to'].forEach(wire);
  ['from', 'to'].forEach(function (which) {
    $('pick-' + which).addEventListener('click', function () {
      window.__pickMode = which; map.getContainer().style.cursor = 'crosshair';
      $('srch-msg').textContent = 'Click the map to set the ' + (which === 'from' ? 'start (A)' : 'destination (B)') + '.';
      map.once('click', function (e) {
        window.__pickMode = null; map.getContainer().style.cursor = '';
        chosen[which] = {lat: e.latlng.lat, lon: e.latlng.lng, label: e.latlng.lat.toFixed(4) + ', ' + e.latlng.lng.toFixed(4)};
        $('srch-' + which).value = chosen[which].label; $('srch-msg').textContent = '';
      });
    });
  });
  $('srch-swap').addEventListener('click', function () {
    var t = $('srch-from').value; $('srch-from').value = $('srch-to').value; $('srch-to').value = t;
    var c = chosen.from; chosen.from = chosen.to; chosen.to = c;
  });

  var routeGroup = L.layerGroup().addTo(map), resultGroup = L.layerGroup().addTo(map), last = null;
  function pin(loc, letter, color) {
    return L.marker([loc.lat, loc.lon], {icon: L.divIcon({className: '', iconSize: [26, 26], iconAnchor: [13, 13],
      html: '<div class="pin" style="background:' + color + '">' + letter + '</div>'}), zIndexOffset: 1000})
      .bindTooltip(esc(loc.label), {direction: 'top'});
  }
  function enabledCats() { return cats.map(function (c, i) { var cb = document.querySelector('.fac-cat[data-i="' + i + '"]'); return cb ? cb.checked : true; }); }

  function corridor(coords, bufferM) {
    var n = coords.length, cum = [0], i;
    for (i = 1; i < n; i++) cum.push(cum[i - 1] + metersBetween(coords[i - 1][0], coords[i - 1][1], coords[i][0], coords[i][1]));
    var minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
    coords.forEach(function (c) { minLat = Math.min(minLat, c[0]); maxLat = Math.max(maxLat, c[0]); minLon = Math.min(minLon, c[1]); maxLon = Math.max(maxLon, c[1]); });
    var padLat = bufferM / 110570, padLon = bufferM / (111320 * Math.cos((minLat + maxLat) / 2 * RAD)), on = enabledCats(), found = [];
    pts.forEach(function (p, idx) {
      if (!on[p[2]]) return;
      var lon = p[0], lat = p[1];
      if (lat < minLat - padLat || lat > maxLat + padLat || lon < minLon - padLon || lon > maxLon + padLon) return;
      var kx = Math.cos(lat * RAD) * 111320, ky = 110570, best = Infinity, along = 0;
      for (var s = 0; s < n - 1; s++) {
        var ax = (coords[s][1] - lon) * kx, ay = (coords[s][0] - lat) * ky, bx = (coords[s + 1][1] - lon) * kx, by = (coords[s + 1][0] - lat) * ky;
        var dx = bx - ax, dy = by - ay, l2 = dx * dx + dy * dy, t = l2 > 0 ? Math.max(0, Math.min(1, -(ax * dx + ay * dy) / l2)) : 0;
        var px = ax + t * dx, py = ay + t * dy, d = Math.sqrt(px * px + py * py);
        if (d < best) { best = d; along = cum[s] + t * Math.sqrt(l2); }
      }
      if (best <= bufferM) found.push({i: idx, d: best, along: along});
    });
    found.sort(function (a, b) { return a.along - b.along; });
    return {found: found, total: cum[n - 1]};
  }

  function showResults(cor, label) {
    resultGroup.clearLayers();
    var counts = {}, html = '';
    cor.found.forEach(function (f) {
      var p = pts[f.i], c = cats[p[2]]; counts[c.key] = (counts[c.key] || 0) + 1;
      var m = L.circleMarker([p[1], p[0]], {renderer: canvas, radius: 8, color: '#111', weight: 2, fillColor: c.color, fillOpacity: 1, bubblingMouseEvents: false});
      m.bindPopup(popupHtml(p) + '<small>' + fmtKm(f.along) + ' from A &middot; ' + Math.round(f.d) + ' m off the route</small>');
      m.bindTooltip(p[3] || c.label, {direction: 'top'}); resultGroup.addLayer(m); f.marker = m;
    });
    var chips = cats.filter(function (c) { return counts[c.key]; }).map(function (c) {
      return '<span class="chip"><span style="color:' + c.color + '">&#9679;</span> ' + esc(c.label) + ' <b>' + counts[c.key] + '</b></span>';
    }).join('');
    html += '<p class="note"><b>' + cor.found.length + '</b> facilities and places within the corridor of the ' + esc(label) + ' (' + fmtKm(cor.total) + ').</p>';
    html += '<div class="chips">' + (chips || '<span class="note">None of the selected categories lie within the corridor.</span>') + '</div>';
    html += '<div class="rlist">' + cor.found.map(function (f, k) {
      var p = pts[f.i], c = cats[p[2]];
      return '<div class="ritem" data-k="' + k + '"><span class="km">' + (f.along / 1000).toFixed(1) + ' km</span><span style="color:' + c.color + '">&#9679;</span> ' +
        '<span class="nm">' + esc(p[3] || '(unnamed ' + String(p[4] || c.label).replace(/_/g, ' ') + ')') + '</span><small>' + esc(c.label) + ' &middot; ' + Math.round(f.d) + ' m</small></div>';
    }).join('') + '</div>';
    $('route-results').innerHTML = html;
    $('route-results').onclick = function (ev) {
      var d = ev.target.closest('.ritem'); if (!d) return;
      var f = cor.found[+d.dataset.k]; map.setView([pts[f.i][1], pts[f.i][0]], Math.max(map.getZoom(), 14)); f.marker.openPopup();
    };
    var det = $('route-det'); if (det) det.open = true;
  }

  function go() {
    var msg = $('srch-msg'); msg.textContent = '';
    var A = resolve('from'), B = resolve('to');
    if (!A || !B) { msg.textContent = 'Enter two locations: a place or facility name, or "lat, lon" (or use the pin buttons).'; return; }
    routeGroup.clearLayers(); resultGroup.clearLayers();
    var bufferM = parseFloat($('srch-corr').value) * 1000, mode = $('srch-mode').value;
    var safe = null, shortest = null, note = '';
    if (G) {
      var sa = nearestVertex(A.lon, A.lat, CFG.snapMaxM), sb = nearestVertex(B.lon, B.lat, CFG.snapMaxM);
      if (!sa || !sb) {
        msg.textContent = (!sa ? 'A' : 'B') + ' is more than ' + (CFG.snapMaxM / 1000) + ' km from any mapped road; showing the straight corridor instead.';
      } else {
        shortest = route(sa, sb, 0); safe = route(sa, sb, CFG.alpha);
        if (!shortest || !safe) { shortest = safe = null; msg.textContent = 'No connected road route found between A and B; showing the straight corridor instead.'; }
      }
    }
    var used, label;
    if (shortest && safe) {
      var same = Math.abs(shortest.length - safe.length) < 1 && Math.abs(shortest.meanRisk - safe.meanRisk) < 1e-6;
      if (!same) L.polyline(shortest.coords, {color: '#ffffff', weight: CFG.shortest.weight + 3, opacity: 0.8, interactive: false}).addTo(routeGroup);
      if (!same) L.polyline(shortest.coords, {color: CFG.shortest.color, weight: CFG.shortest.weight, opacity: CFG.shortest.opacity, dashArray: CFG.shortest.dashArray})
        .bindTooltip('Shortest route: ' + fmtKm(shortest.length) + ' &middot; mean risk ' + shortest.meanRisk.toFixed(3)).addTo(routeGroup);
      L.polyline(safe.coords, {color: '#ffffff', weight: CFG.safe.weight + 3, opacity: 0.8, interactive: false}).addTo(routeGroup);
      L.polyline(safe.coords, {color: CFG.safe.color, weight: CFG.safe.weight, opacity: CFG.safe.opacity})
        .bindTooltip((same ? 'Shortest = least-risk route: ' : 'Least-risk route: ') + fmtKm(safe.length) + ' &middot; mean risk ' + safe.meanRisk.toFixed(3)).addTo(routeGroup);
      used = (mode === 'short') ? shortest : safe; label = (mode === 'short') ? 'shortest route' : 'least-risk route';
      var inc = (safe.length - shortest.length) / shortest.length * 100, red = shortest.meanRisk > 0 ? (shortest.meanRisk - safe.meanRisk) / shortest.meanRisk * 100 : 0;
      note = '<b>Shortest</b> ' + fmtKm(shortest.length) + ' &middot; mean risk ' + shortest.meanRisk.toFixed(3) + ' &middot; max ' + shortest.maxRisk.toFixed(2) + '<br>' +
        '<b>Least-risk</b> ' + fmtKm(safe.length) + ' &middot; mean risk ' + safe.meanRisk.toFixed(3) + ' &middot; max ' + safe.maxRisk.toFixed(2) + '<br>' +
        (same ? 'Both objectives use the same road.' : '<span class="good">' + (-red).toFixed(1) + '% mean risk for ' + (inc >= 0 ? '+' : '') + inc.toFixed(1) + '% distance</span>');
    } else {
      var line = [[A.lat, A.lon], [B.lat, B.lon]];
      used = {coords: line, length: metersBetween(A.lat, A.lon, B.lat, B.lon)}; label = 'straight line';
      L.polyline(line, {color: CFG.shortest.color, weight: 3, dashArray: '6 6'}).addTo(routeGroup);
      note = 'Straight-line corridor (' + fmtKm(used.length) + ').';
    }
    pin(A, 'A', '#2e7d32').addTo(routeGroup); pin(B, 'B', '#c62828').addTo(routeGroup);
    var cor = corridor(used.coords, bufferM);
    showResults(cor, label);
    $('srch-summary').innerHTML = note + '<br><b>' + cor.found.length + '</b> places within ' + (bufferM / 1000) + ' km';
    $('srch-summary').style.display = 'block';
    map.fitBounds(L.latLngBounds(used.coords).pad(0.15));
    last = {A: A, B: B};
  }
  $('srch-go').addEventListener('click', go);
  $('srch-clear').addEventListener('click', function () {
    routeGroup.clearLayers(); resultGroup.clearLayers(); chosen = {from: null, to: null};
    $('srch-from').value = ''; $('srch-to').value = ''; $('srch-msg').textContent = ''; $('srch-summary').style.display = 'none';
    $('route-results').innerHTML = '<p class="note">Search two places to list what lies between them.</p>';
  });
  $('srch-corr').addEventListener('change', function () { if (last) go(); });
  $('srch-mode').addEventListener('change', function () { if (last) go(); });

  /* ------------------------------------------------------------------ master toggle + categories */
  var master = $('fac-master'), roadsCb = $('fac-roads');
  function setLayer(layer, on) { if (!layer) return; if (on && !map.hasLayer(layer)) map.addLayer(layer); if (!on && map.hasLayer(layer)) map.removeLayer(layer); }
  function refresh() {
    var on = master.checked;
    document.getElementById('fac-body').classList.toggle('off', !on);
    cats.forEach(function (c, i) { setLayer(catLayers[i], on && document.querySelector('.fac-cat[data-i="' + i + '"]').checked); });
    if (on && roadsCb.checked) { buildRoads(); setLayer(roadLayer, true); if (roadLayer) roadLayer.eachLayer(function (l) { l.bringToBack(); }); }
    else setLayer(roadLayer, false);
  }
  master.addEventListener('change', refresh); roadsCb.addEventListener('change', refresh);
  document.querySelectorAll('.fac-cat').forEach(function (cb) { cb.addEventListener('change', function () { refresh(); if (last) go(); }); });
  $('fac-all').addEventListener('click', function () { document.querySelectorAll('.fac-cat').forEach(function (cb) { cb.checked = true; }); refresh(); });
  $('fac-none').addEventListener('click', function () { document.querySelectorAll('.fac-cat').forEach(function (cb) { cb.checked = false; }); refresh(); });
  refresh();
})();
