"""経路検索 Web アプリのバックエンド (FastAPI)

起動:  uvicorn backend.app:app --reload     → http://127.0.0.1:8000/
グラフは起動時に1度だけ読み込む（環境変数 GRAPHML で変更可。既定: osm_network.graphml）。
"""
import os
import sys
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import osm_to_network as net  # noqa: E402  経路探索ロジックを再利用

import osmnx as ox  # noqa: E402

WALK_M_PER_MIN = 80  # 徒歩 約4.8km/h

GRAPHML = Path(os.environ.get("GRAPHML", ROOT / "osm_network.graphml"))
G = ox.load_graphml(GRAPHML)
_nodes = ox.graph_to_gdfs(G, edges=False)
_w, _s, _e, _n = _nodes.total_bounds
BOUNDS = {"south": _s, "west": _w, "north": _n, "east": _e}
net.prefer_ipv4()
_geocode_lock = threading.Lock()  # Nominatim の利用規約(1件/秒)に配慮して直列化

app = FastAPI(title="ルート検索")


def resolve(text: str, use_entrance: bool):
    """座標または施設名を解決する。net.resolve_place は失敗時 SystemExit を出すので変換する。"""
    try:
        with _geocode_lock:
            return net.resolve_place(G, text, use_entrance=use_entrance)
    except SystemExit as e:
        raise HTTPException(400, str(e))


def edge_coords(G, u, v):
    """u→v の描画用座標列 [[lat, lon], ...]（edge の geometry があれば曲線を使う）。"""
    d = min(G[u][v].values(), key=lambda d: d["length"])
    geom = d.get("geometry")
    if geom is None:
        return [[G.nodes[u]["y"], G.nodes[u]["x"]], [G.nodes[v]["y"], G.nodes[v]["x"]]]
    pts = [[y, x] for x, y in geom.coords]
    uy, ux = G.nodes[u]["y"], G.nodes[u]["x"]
    if net.haversine(uy, ux, *pts[0]) > net.haversine(uy, ux, *pts[-1]):
        pts.reverse()
    return pts


@app.get("/api/info")
def info():
    return {"bounds": BOUNDS, "walk_m_per_min": WALK_M_PER_MIN}


@app.get("/api/route")
def route(orig: str = Query(..., description="'緯度,経度' または施設名"),
          dest: str = Query(..., description="'緯度,経度' または施設名"),
          algo: str = "astar", entrance: bool = False):
    if algo not in ("astar", "dijkstra"):
        raise HTTPException(400, "algo は astar / dijkstra")
    snaps, ends = [], []
    for sid, text in ((-1, orig), (-2, dest)):
        name, cands = resolve(text, entrance)
        proj = [(net.project_to_edge(G, la, lo), kind, la, lo) for la, lo, kind in cands]
        (u, v, key, r, pt, dist), kind, la, lo = min(proj, key=lambda p: p[0][5])
        # 座標入力でも範囲外なら拒否（地図外のクリック対策）
        if not (BOUNDS["south"] <= la <= BOUNDS["north"]
                and BOUNDS["west"] <= lo <= BOUNDS["east"]):
            raise HTTPException(400, f"'{text}' は探索範囲外です。")
        snaps.append((sid, u, v, key, r, pt))
        ends.append({"name": name, "kind": kind, "lat": la, "lon": lo,
                     "snap": {"lat": pt[0], "lon": pt[1]}, "snap_m": round(dist, 1)})
    H = net.add_snap_nodes(G, snaps)
    path, _ = net.shortest_path(H, -1, -2, weight="length", algo=algo)
    if path is None:
        raise HTTPException(404, "経路が見つかりません（一方通行・非連結の可能性）")
    length = sum(net.edge_cost(H, u, v, "length") for u, v in zip(path, path[1:]))
    coords = []
    for u, v in zip(path, path[1:]):
        seg = edge_coords(H, u, v)
        coords.extend(seg if not coords else seg[1:])
    return {"orig": ends[0], "dest": ends[1], "length_m": round(length, 1),
            "minutes": round(length / WALK_M_PER_MIN, 1), "coords": coords}


@app.get("/")
def index():
    return FileResponse(ROOT / "frontend" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "frontend"), name="static")
