"""経路検索（ルート直下の osm_to_network.py の関数を呼び出す薄いラッパー）。

osm_to_network.py 側は変更せず、グラフの読み込み・最寄りノード探索・最短経路探索だけを使う。
グラフは data/map.graphml を起動時に1回だけ読み込む（無ければ経路検索だけ使えない）。
"""
from pathlib import Path

import osmnx as ox

import osm_to_network as osm

GRAPH_PATH = Path(__file__).resolve().parent.parent / "data" / "map.graphml"

_graph = None
MAX_SNAP_M = 300  # 指定地点から最寄りの道までの許容距離[m]


class RouteError(Exception):
    """経路検索ができないときのエラー（messageをそのまま利用者に返せる）。"""


def load_graph() -> bool:
    """起動時に呼ぶ。グラフを読み込めたら True。"""
    global _graph
    if not GRAPH_PATH.exists():
        _graph = None
        return False
    _graph = ox.load_graphml(GRAPH_PATH)
    return True


def bounds() -> dict | None:
    """グラフが覆う範囲（地図の初期表示と範囲外の案内に使う）。"""
    if _graph is None:
        return None
    ys = [d["y"] for _, d in _graph.nodes(data=True)]
    xs = [d["x"] for _, d in _graph.nodes(data=True)]
    return {"south": min(ys), "north": max(ys), "west": min(xs), "east": max(xs)}


def _snap(point: tuple[float, float], label: str) -> int:
    """最寄りノードを返す。遠すぎる（地図データの範囲外）ときはエラー。"""
    node = osm.nearest_node(_graph, *point)
    d = osm.haversine(*point, _graph.nodes[node]["y"], _graph.nodes[node]["x"])
    if d > MAX_SNAP_M:
        raise RouteError(f"{label}が地図データの範囲外です（青い枠の中を選んでください）")
    return node


def _edge_coords(u: int, v: int) -> list[list[float]]:
    """u→v の辺に沿った [緯度, 経度] の列。曲がった道は geometry を使う。"""
    data = min(_graph[u][v].values(), key=lambda d: d["length"])
    geom = data.get("geometry")
    if geom is None:
        return [[_graph.nodes[u]["y"], _graph.nodes[u]["x"]],
                [_graph.nodes[v]["y"], _graph.nodes[v]["x"]]]
    pts = [[y, x] for x, y in geom.coords]
    uy, ux = _graph.nodes[u]["y"], _graph.nodes[u]["x"]
    if osm.haversine(uy, ux, *pts[0]) > osm.haversine(uy, ux, *pts[-1]):
        pts.reverse()  # 向きが逆なら揃える
    return pts


def _path_result(source: int, path: list[int]) -> dict:
    """ノードの列から、地図に描く座標の列と距離を作る。"""
    coords = [[_graph.nodes[source]["y"], _graph.nodes[source]["x"]]]
    for u, v in zip(path, path[1:]):
        coords.extend(_edge_coords(u, v)[1:])
    return {
        "coords": coords,
        "distance_m": sum(osm.edge_cost(_graph, u, v, "length") for u, v in zip(path, path[1:])),
    }


def find_route_between(
    starts: list[tuple[str, tuple[float, float]]],
    goals: list[tuple[str, tuple[float, float]]],
) -> dict:
    """出発候補と目的候補（施設の入口など）の組み合わせのうち、道のりが一番短い経路を返す。

    starts, goals: [(名前, (緯度, 経度)), ...]   ※地点1つなら名前は "" でよい
    戻り値: {"coords": [...], "distance_m": 距離[m],
             "start": {"name", "lat", "lon"}, "goal": {"name", "lat", "lon"}}
    """
    if _graph is None:
        raise RouteError(f"地図データがありません（{GRAPH_PATH} を置いてください）")

    def snap_all(points, label):
        snapped = []
        for name, point in points:
            try:
                snapped.append((name, point, _snap(point, label)))
            except RouteError:
                continue  # 地図データの範囲外の入口は候補から外す
        if not snapped:
            raise RouteError(f"{label}が地図データの範囲外です（青い枠の中を選んでください）")
        return snapped

    sources = snap_all(starts, "出発地")
    targets = snap_all(goals, "目的地")
    best = None
    too_close = False
    for s_name, s_point, source in sources:
        for g_name, g_point, target in targets:
            if source == target:
                too_close = True
                continue
            path, _ = osm.shortest_path(_graph, source, target, weight="length", algo="astar")
            if path is None:
                continue
            result = _path_result(source, path)
            if best is None or result["distance_m"] < best["distance_m"]:
                result["start"] = {"name": s_name, "lat": s_point[0], "lon": s_point[1]}
                result["goal"] = {"name": g_name, "lat": g_point[0], "lon": g_point[1]}
                best = result
    if best is None:
        if too_close:
            raise RouteError("出発地と目的地が近すぎます。離れた場所を選んでください")
        raise RouteError("目的地までの経路が見つかりません")
    return best


def find_route_to_nearest(orig: tuple[float, float], goals: list[tuple[str, tuple[float, float]]]) -> dict:
    """出発地（1地点）から、複数のゴールのうち道のりが一番短いものへの経路を返す。"""
    return find_route_between([("", orig)], goals)


def find_route(orig: tuple[float, float], dest: tuple[float, float]) -> dict:
    """(緯度, 経度) 同士の最短経路を返す。

    戻り値: {"coords": [[緯度, 経度], ...], "distance_m": 距離[m]}
    """
    if _graph is None:
        raise RouteError(f"地図データがありません（{GRAPH_PATH} を置いてください）")
    source = _snap(orig, "出発地")
    target = _snap(dest, "目的地")
    if source == target:
        raise RouteError("出発地と目的地が近すぎます。離れた場所を選んでください")
    path, _ = osm.shortest_path(_graph, source, target, weight="length", algo="astar")
    if path is None:
        raise RouteError("経路が見つかりません")
    return _path_result(source, path)
