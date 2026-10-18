"""graphml を、ブラウザ(静的Web版)で読み込める graph.js に変換する。

使い方:
  python scripts/export_graph_js.py [osm_network.graphml] [web/graph.js]

出力は `window.GRAPH = {...}` 形式の JS ファイル（<script> で読み込めるので file:// でも動く）。
座標は 1e-5 度(約1m)単位の整数にして、ファイルサイズを抑える。
  nodes: [lat, lon, lat, lon, ...]                      ノード番号 = 並びの順
  edges: [[u, v, 長さ(m), [lat, lon, ...]|0], ...]      有向エッジ。中間点の列は u→v の向き
"""
import json
import math
import sys
from pathlib import Path

import osmnx as ox

SCALE = 100_000
ROOT = Path(__file__).resolve().parent.parent


def q(v):
    return int(round(v * SCALE))


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "osm_network.graphml"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "web" / "graph.js"
    G = ox.load_graphml(src)
    index = {n: i for i, n in enumerate(G.nodes)}
    nodes = []
    for n in G.nodes:
        nodes += [q(G.nodes[n]["y"]), q(G.nodes[n]["x"])]

    edges = []
    for u, v in {(u, v) for u, v in G.edges()}:
        d = min(G[u][v].values(), key=lambda d: d["length"])  # 並行エッジは最短を採用
        mid = 0
        geom = d.get("geometry")
        if geom is not None:
            pts = [(y, x) for x, y in geom.coords]
            uy, ux = G.nodes[u]["y"], G.nodes[u]["x"]
            if math.dist((uy, ux), pts[0]) > math.dist((uy, ux), pts[-1]):
                pts.reverse()  # 念のため u→v の向きにそろえる
            inner = pts[1:-1]
            if inner:
                mid = [c for y, x in inner for c in (q(y), q(x))]
        edges.append([index[u], index[v], round(d["length"], 1), mid])

    built = str(G.graph.get("created_date", ""))[:10]
    data = {"built": built, "scale": SCALE, "nodes": nodes, "edges": edges}
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text("window.GRAPH=" + json.dumps(data, separators=(",", ":")) + ";\n",
                   encoding="utf-8")
    print(f"{dst}: ノード {len(G)} / エッジ {len(edges)} / {dst.stat().st_size / 1e6:.2f} MB"
          f"（取得日 {built}）")


if __name__ == "__main__":
    main()
