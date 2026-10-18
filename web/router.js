/* 経路探索（osm_to_network.py の project_to_edge / add_snap_nodes / shortest_path の JS 版）
 * ブラウザでは window.Router、Node では module.exports として使える。 */
(function (root) {
  "use strict";
  const R = 6371009;
  const rad = d => d * Math.PI / 180;

  function haversine(lat1, lon1, lat2, lon2) {
    const dp = rad(lat2 - lat1), dl = rad(lon2 - lon1);
    const a = Math.sin(dp / 2) ** 2 + Math.cos(rad(lat1)) * Math.cos(rad(lat2)) * Math.sin(dl / 2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
  }

  class MinHeap {
    constructor() { this.a = []; }
    get size() { return this.a.length; }
    push(item) {
      const a = this.a; a.push(item);
      let i = a.length - 1;
      while (i > 0) {
        const p = (i - 1) >> 1;
        if (a[p][0] <= a[i][0]) break;
        [a[p], a[i]] = [a[i], a[p]]; i = p;
      }
    }
    pop() {
      const a = this.a, top = a[0], last = a.pop();
      if (a.length) {
        a[0] = last;
        let i = 0;
        for (;;) {
          let l = 2 * i + 1, r = l + 1, m = i;
          if (l < a.length && a[l][0] < a[m][0]) m = l;
          if (r < a.length && a[r][0] < a[m][0]) m = r;
          if (m === i) break;
          [a[m], a[i]] = [a[i], a[m]]; i = m;
        }
      }
      return top;
    }
  }

  class Graph {
    /** data: export_graph_js.py の出力 (window.GRAPH) */
    constructor(data) {
      const s = data.scale;
      this.built = data.built;
      this.n = data.nodes.length / 2;
      this.lat = new Float64Array(this.n);
      this.lon = new Float64Array(this.n);
      for (let i = 0; i < this.n; i++) {
        this.lat[i] = data.nodes[2 * i] / s;
        this.lon[i] = data.nodes[2 * i + 1] / s;
      }
      // エッジ: {u, v, len, pts:[[lat,lon],...]}  pts は u→v の全点（端点を含む）
      this.edges = data.edges.map(([u, v, len, mid]) => {
        const pts = [[this.lat[u], this.lon[u]]];
        for (let i = 0; mid && i < mid.length; i += 2) pts.push([mid[i] / s, mid[i + 1] / s]);
        pts.push([this.lat[v], this.lon[v]]);
        return { u, v, len, pts };
      });
      this.out = Array.from({ length: this.n }, () => []);
      this.byPair = new Map();
      this.edges.forEach((e, i) => {
        this.out[e.u].push(i);
        this.byPair.set(e.u * this.n + e.v, i);
      });
      let s0 = 90, w0 = 180, n0 = -90, e0 = -180;
      for (let i = 0; i < this.n; i++) {
        s0 = Math.min(s0, this.lat[i]); n0 = Math.max(n0, this.lat[i]);
        w0 = Math.min(w0, this.lon[i]); e0 = Math.max(e0, this.lon[i]);
      }
      this.bounds = { south: s0, west: w0, north: n0, east: e0 };
    }

    inBounds(lat, lon) {
      const b = this.bounds;
      return lat >= b.south && lat <= b.north && lon >= b.west && lon <= b.east;
    }

    /** 点を最寄りの道路上に射影する。戻り値: {edge, r, lat, lon, dist} (r は u からの割合) */
    project(lat, lon) {
      const cos0 = Math.cos(rad(lat)), k = Math.PI / 180 * R;
      let best = null;
      for (let ei = 0; ei < this.edges.length; ei++) {
        const pts = this.edges[ei].pts;
        // 粗い除外: 端点・中間点のどれもが 0.01 度(約1km)以上離れていれば飛ばす
        let near = false;
        for (const p of pts) {
          if (Math.abs(p[0] - lat) < 0.01 && Math.abs(p[1] - lon) < 0.012) { near = true; break; }
        }
        if (!near) continue;
        const xy = pts.map(p => [(p[1] - lon) * cos0 * k, (p[0] - lat) * k]);
        let total = 0;
        for (let i = 0; i + 1 < xy.length; i++) total += Math.hypot(xy[i + 1][0] - xy[i][0], xy[i + 1][1] - xy[i][1]);
        total = total || 1e-9;
        let along = 0;
        for (let i = 0; i + 1 < xy.length; i++) {
          const a = xy[i], b = xy[i + 1];
          const seg = Math.hypot(b[0] - a[0], b[1] - a[1]);
          if (seg === 0) continue;
          const t = Math.max(0, Math.min(1, -(a[0] * (b[0] - a[0]) + a[1] * (b[1] - a[1])) / (seg * seg)));
          const px = a[0] + t * (b[0] - a[0]), py = a[1] + t * (b[1] - a[1]);
          const dist = Math.hypot(px, py);
          if (!best || dist < best.dist) {
            best = { edge: ei, r: (along + t * seg) / total, dist,
                     lat: lat + py / k, lon: lon + px / (cos0 * k) };
          }
          along += seg;
        }
      }
      return best;
    }
  }

  /** ポリライン pts の割合 r0〜r1 の部分を切り出す（長さは局所平面座標で按分） */
  function subPolyline(pts, r0, r1, lat0) {
    const cos0 = Math.cos(rad(lat0));
    const d = [0];
    for (let i = 0; i + 1 < pts.length; i++) {
      d.push(d[i] + Math.hypot((pts[i + 1][1] - pts[i][1]) * cos0, pts[i + 1][0] - pts[i][0]));
    }
    const total = d[d.length - 1] || 1e-12;
    const at = r => {
      const target = r * total;
      for (let i = 0; i + 1 < pts.length; i++) {
        if (target <= d[i + 1] || i + 2 === pts.length) {
          const t = d[i + 1] === d[i] ? 0 : (target - d[i]) / (d[i + 1] - d[i]);
          return [pts[i][0] + t * (pts[i + 1][0] - pts[i][0]), pts[i][1] + t * (pts[i + 1][1] - pts[i][1])];
        }
      }
      return pts[pts.length - 1];
    };
    const out = [at(r0)];
    for (let i = 1; i + 1 < pts.length; i++) if (d[i] > r0 * total && d[i] < r1 * total) out.push(pts[i]);
    out.push(at(r1));
    return out;
  }

  /**
   * 射影点を仮ノード(番号 n, n+1, …)として追加した探索用の隣接表を作る。元のグラフは変更しない。
   * snaps: [{edge, r, lat, lon}]。同じエッジ上の複数点は位置順に連結する。
   * u→v / v→u の存在する向きにだけ辺を張る（一方通行を保つ）。
   */
  function withSnapNodes(g, snaps) {
    const extraOut = new Map(); // node -> [{to, len, pts}]
    const coord = new Map();
    const add = (a, b, len, pts) => {
      if (!extraOut.has(a)) extraOut.set(a, []);
      extraOut.get(a).push({ to: b, len, pts });
    };
    const ids = snaps.map((_, i) => g.n + i);
    const byEdge = new Map();
    snaps.forEach((s, i) => {
      coord.set(ids[i], [s.lat, s.lon]);
      const e = g.edges[s.edge], key = Math.min(e.u, e.v) * g.n + Math.max(e.u, e.v);
      // 向きをそろえる: 基準を u<v 側の辺として r を換算
      const fwd = e.u < e.v;
      if (!byEdge.has(key)) byEdge.set(key, { a: Math.min(e.u, e.v), b: Math.max(e.u, e.v), pts: [] });
      byEdge.get(key).pts.push({ r: fwd ? s.r : 1 - s.r, id: ids[i] });
    });
    for (const { a, b, pts } of byEdge.values()) {
      pts.sort((x, y) => x.r - y.r);
      const chain = [{ r: 0, id: a }, ...pts, { r: 1, id: b }];
      for (const [fu, fv, sign] of [[a, b, 1], [b, a, -1]]) {
        const ei = g.byPair.get(fu * g.n + fv);
        if (ei === undefined) continue;
        const e = g.edges[ei];
        const seq = sign === 1 ? chain : chain.map(c => ({ r: 1 - c.r, id: c.id })).reverse();
        for (let i = 0; i + 1 < seq.length; i++) {
          const f = seq[i + 1].r - seq[i].r;
          add(seq[i].id, seq[i + 1].id, e.len * f, subPolyline(e.pts, seq[i].r, seq[i + 1].r, g.lat[fu]));
        }
      }
    }
    const pos = n => n < g.n ? [g.lat[n], g.lon[n]] : coord.get(n);
    return {
      ids,
      pos,
      *neighbors(n) {
        if (n < g.n) for (const ei of g.out[n]) { const e = g.edges[ei]; yield { to: e.v, len: e.len, pts: e.pts }; }
        for (const x of extraOut.get(n) || []) yield x;
      },
    };
  }

  /** A* / Dijkstra。戻り値: {path:[node...], length, coords:[[lat,lon]...]} または null */
  function shortestPath(net, source, target, algo = "astar") {
    const [ty, tx] = net.pos(target);
    const h = algo === "astar"
      ? n => { const [y, x] = net.pos(n); return haversine(y, x, ty, tx); }
      : () => 0;
    const dist = new Map([[source, 0]]), prev = new Map(), done = new Set();
    const heap = new MinHeap();
    heap.push([h(source), source]);
    while (heap.size) {
      const [, u] = heap.pop();
      if (done.has(u)) continue;
      if (u === target) {
        const path = [u], coords = [];
        while (path[path.length - 1] !== source) path.push(prev.get(path[path.length - 1]).from);
        path.reverse();
        for (let i = 1; i < path.length; i++) {
          const seg = prev.get(path[i]).pts;
          coords.push(...(coords.length ? seg.slice(1) : seg));
        }
        return { path, length: dist.get(target), coords };
      }
      done.add(u);
      for (const e of net.neighbors(u)) {
        if (done.has(e.to)) continue;
        const nd = dist.get(u) + e.len;
        if (nd < (dist.has(e.to) ? dist.get(e.to) : Infinity)) {
          dist.set(e.to, nd);
          prev.set(e.to, { from: u, pts: e.pts });
          heap.push([nd + h(e.to), e.to]);
        }
      }
    }
    return null;
  }

  const api = { Graph, haversine, withSnapNodes, shortestPath };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Router = api;
})(typeof window !== "undefined" ? window : globalThis);
