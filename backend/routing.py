"""経路検索（ルート直下の osm_to_network.py の関数を呼び出す薄いラッパー）。

osm_to_network.py 側は変更せず、グラフの読み込み・最寄りノード探索などを使う。
グラフは data/map.graphml を起動時に1回だけ読み込む（無ければ経路検索だけ使えない）。

坂道の考慮:
  scripts/add_elevation.py で標高を書き込んだ地図データなら、傾きが SLOPE_GRADE 以上の区間
  （上り・下りとも）を「坂道」として、利用者の坂道での歩く速さで時間を計算する。
  経路は「所要時間が一番短いもの」を選ぶので、坂を避けて遠回りしたほうが早ければ遠回りし、
  遠回りでかえって遅くなるなら坂道をそのまま通る。
  標高の無い地図データでは、すべて平らな道とみなす（距離が一番短い経路と同じになる）。
"""
import json
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
import numpy as np
import osmnx as ox

import osm_to_network as osm
from .db import connect

GRAPH_PATH = Path(__file__).resolve().parent.parent / "data" / "map.graphml"
# 道路の幅 [[始点ノード, 終点ノード, 長さ[m], 幅[m]], ...]。scripts/build_road_widths.py が作る
WIDTHS_PATH = Path(__file__).resolve().parent.parent / "data" / "road_widths.json"
NARROW_ROAD_M = 3.0  # 幅がこれ未満の道は「細道」として、通れないものとして経路を探す

_graph = None
_has_elevation = False
MAX_SNAP_M = 300     # 指定地点から最寄りの道までの許容距離[m]
SLOPE_GRADE = 0.05   # これ以上の傾き（高低差÷距離）の区間を坂道とみなす（上り・下りとも）


@dataclass(frozen=True)
class WalkSpeeds:
    """経路検索に使う歩く速さ [m/分]。"""
    flat: float   # 普通の道
    slope: float  # 坂道

_edge_list = None          # 地図上の道（上り下りを1本にまとめたもの）[(u, v, 座標列)]。初回に作る
_blocked_cache = (None, [], [])   # (通行不可の危険個所の座標一覧, 目印を付ける道, 探索で通れなくする辺)
_narrow_edges: list = []   # 細道の辺 [(u, v, key), ...]。起動時に作る


class RouteError(Exception):
    """経路検索ができないときのエラー（messageをそのまま利用者に返せる）。"""


def load_graph() -> bool:
    """起動時に呼ぶ。グラフを読み込めたら True。"""
    global _graph, _has_elevation
    if not GRAPH_PATH.exists():
        _graph = None
        return False
    _graph = ox.load_graphml(GRAPH_PATH)
    _has_elevation = _add_slope_lengths(_graph)
    global _blocked_cache, _edge_list
    _edge_list = None
    _blocked_cache = (None, [], [])
    load_narrow_edges()
    return True


def load_narrow_edges() -> int:
    """幅が NARROW_ROAD_M 未満の辺を求める。幅のファイルが無ければ細道は除外しない。件数を返す。"""
    global _narrow_edges
    _narrow_edges = []
    if _graph is None or not WIDTHS_PATH.exists():
        return 0
    rows = json.loads(WIDTHS_PATH.read_text(encoding="utf-8"))
    # 辺は「両端のノード + 長さ」で突き合わせる（上りと下りは同じ幅として両方向を対象にする）
    narrow = {(frozenset((u, v)), round(length, 1)) for u, v, length, width in rows if width < NARROW_ROAD_M}
    _narrow_edges = [(a, b, k) for a, b, k, d in _graph.edges(keys=True, data=True)
                     if (frozenset((a, b)), round(d["length"], 1)) in narrow]
    return len(_narrow_edges)


def _blocked_points() -> tuple:
    """通行不可の危険個所の座標 ((緯度, 経度), ...)。"""
    with connect() as conn:
        return tuple((r["lat"], r["lon"]) for r in
                     conn.execute("SELECT lat, lon FROM hazards WHERE status = 'blocked' ORDER BY id"))


def _all_edges() -> list:
    """地図上のすべての道 [(u, v, [[緯度, 経度], ...])]。上りと下りは1本にまとめる。"""
    global _edge_list
    if _edge_list is None:
        seen = set()
        _edge_list = []
        for u, v in _graph.edges():
            key = frozenset((u, v))
            if u == v or key in seen:
                continue
            seen.add(key)
            _edge_list.append((u, v, _edge_coords(u, v, min(_graph[u][v].values(), key=lambda d: d["length"]))))
    return _edge_list


def _dist_to_polyline_m(lat: float, lon: float, coords: list) -> float:
    """点から折れ線までの距離[m]（近い範囲なので平面に直して測る）。"""
    kx = 111_320 * np.cos(np.radians(lat))   # 経度1度あたりの長さ[m]
    ky = 110_574                              # 緯度1度あたりの長さ[m]
    pts = [((c[1] - lon) * kx, (c[0] - lat) * ky) for c in coords]
    best = float("inf")
    for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
        dx, dy = x2 - x1, y2 - y1
        length2 = dx * dx + dy * dy
        t = 0.0 if length2 == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / length2))
        best = min(best, float(np.hypot(x1 + t * dx, y1 + t * dy)))
    return best


def _blocked_state() -> tuple:
    """通行不可の危険個所ごとに、いちばん近い道1本を求める。戻り値: (目印を付ける道, 探索で通れなくする辺)。

    画面の目印と探索で、同じ道を使う（見た目と動作を必ず一致させる）。
    投稿・削除のたびに変わるので、検索のたびに DB を見る（座標の一覧が同じ間は前の結果を使い回す）。
    """
    global _blocked_cache
    if _graph is None:
        return [], []
    points = _blocked_points()
    if _blocked_cache[0] == points:
        return _blocked_cache[1], _blocked_cache[2]
    edges = _all_edges()
    chosen: dict = {}
    for lat, lon in points:
        u, v, coords = min(edges, key=lambda e: _dist_to_polyline_m(lat, lon, e[2]))
        chosen[frozenset((u, v))] = (u, v, coords)   # 同じ道に複数あれば1本にまとめる
    marked = [{"coords": c, "mid": c[len(c) // 2]} for _, _, c in chosen.values()]
    removed = [(a, b, k) for u, v, _ in chosen.values() for a, b in ((u, v), (v, u))
               if _graph.has_edge(a, b) for k in _graph[a][b]]   # 上りも下りも、並行する辺も全部
    _blocked_cache = (points, marked, removed)
    return marked, removed


def blocked_edges() -> list[dict]:
    """通行不可の目印を付ける道（緊急時の地図に縞線で示す）。危険個所ごとに、いちばん近い道1本。

    戻り値: [{"coords": [[緯度, 経度], ...], "mid": [緯度, 経度]}, ...]
    """
    return _blocked_state()[0]


def _removed_edges() -> list:
    """探索で通れないものとして扱う辺（通行不可の危険個所の道 + 細道）。"""
    return _blocked_state()[1] + _narrow_edges


def _search_graph():
    """通れない道を取り除いたグラフ（見え方だけ変える。元のグラフは書き換えない）。"""
    removed = _removed_edges()
    return nx.restricted_view(_graph, [], removed) if removed else _graph


_AVOID_MESSAGE = "通行不可の場所や細い道を避けると、目的地までの経路が見つかりません"


def _no_route_error(message: str) -> RouteError:
    if _removed_edges():
        return RouteError(_AVOID_MESSAGE)
    return RouteError(message)


def _add_slope_lengths(G) -> bool:
    """各辺に slope_m（坂道の長さ[m]）を付ける。標高のある辺が1本でもあれば True。

    elev_profile は辺に沿って等間隔に測った標高（scripts/add_elevation.py が書き込む）。
    隣り合う2点の間の傾きが SLOPE_GRADE 以上なら、その区間を坂道として数える。
    """
    found = False
    for _, _, d in G.edges(data=True):
        d["slope_m"] = 0.0
        profile = d.get("elev_profile")
        if not profile:
            continue
        heights = [float(h) for h in str(profile).split(",")]
        if len(heights) < 2:
            continue
        found = True
        step = d["length"] / (len(heights) - 1)
        if step > 0:
            steep = sum(1 for a, b in zip(heights, heights[1:]) if abs(b - a) / step >= SLOPE_GRADE)
            d["slope_m"] = step * steep
    return found


def has_elevation() -> bool:
    """坂道を考慮できる（標高入りの地図データを読み込んでいる）か。"""
    return _has_elevation


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


def _edge_minutes(data: dict, speeds: WalkSpeeds) -> float:
    """1本の辺を歩くのにかかる時間[分]。"""
    slope = min(data["slope_m"], data["length"])
    return (data["length"] - slope) / speeds.flat + slope / speeds.slope


def _edge_cost(data: dict, speeds: WalkSpeeds, minimize: str) -> float:
    """経路探索で比べる辺の重み。minimize="time" なら時間[分]、"distance" なら距離[m]。"""
    return _edge_minutes(data, speeds) if minimize == "time" else data["length"]


def _best_edge(u: int, v: int, speeds: WalkSpeeds, minimize: str) -> dict:
    """u→v の並行する辺のうち、重みが一番小さい辺。"""
    return min(_graph[u][v].values(), key=lambda d: _edge_cost(d, speeds, minimize))


def _shortest_path(source: int, target: int, speeds: WalkSpeeds, minimize: str) -> list[int] | None:
    """重み（時間か距離）が一番小さい経路のノード列（A*法）。たどり着けなければ None。

    目安（ヒューリスティック）は目的地までの直線距離（時間のときは「÷ 速いほうの速さ」）。
    実際の重みを超えないので、最短の経路が必ず見つかる。
    """
    ty, tx = _graph.nodes[target]["y"], _graph.nodes[target]["x"]
    scale = 1 / max(speeds.flat, speeds.slope) if minimize == "time" else 1.0

    def heuristic(n, _target):
        return osm.haversine(_graph.nodes[n]["y"], _graph.nodes[n]["x"], ty, tx) * scale

    def weight(_u, _v, edges):
        return min(_edge_cost(d, speeds, minimize) for d in edges.values())

    try:
        return nx.astar_path(_search_graph(), source, target, heuristic=heuristic, weight=weight)
    except nx.NetworkXNoPath:
        return None


def _edge_coords(u: int, v: int, data: dict) -> list[list[float]]:
    """u→v の辺に沿った [緯度, 経度] の列。曲がった道は geometry を使う。"""
    geom = data.get("geometry")
    if geom is None:
        return [[_graph.nodes[u]["y"], _graph.nodes[u]["x"]],
                [_graph.nodes[v]["y"], _graph.nodes[v]["x"]]]
    pts = [[y, x] for x, y in geom.coords]
    uy, ux = _graph.nodes[u]["y"], _graph.nodes[u]["x"]
    if osm.haversine(uy, ux, *pts[0]) > osm.haversine(uy, ux, *pts[-1]):
        pts.reverse()  # 向きが逆なら揃える
    return pts


def _path_result(source: int, path: list[int], speeds: WalkSpeeds, minimize: str) -> dict:
    """ノードの列から、地図に描く座標の列・距離・坂道の長さ・所要時間を作る。

    所要時間は、距離で選んだ経路でも坂道で遅くなる分を含めて計算する。
    """
    coords = [[_graph.nodes[source]["y"], _graph.nodes[source]["x"]]]
    distance = slope = minutes = 0.0
    for u, v in zip(path, path[1:]):
        data = _best_edge(u, v, speeds, minimize)
        coords.extend(_edge_coords(u, v, data)[1:])
        distance += data["length"]
        slope += min(data["slope_m"], data["length"])
        minutes += _edge_minutes(data, speeds)
    return {
        "coords": coords,
        "distance_m": distance,
        "slope_m": slope,               # 経路のうち坂道の長さ[m]
        "duration_min": minutes,        # 坂道で遅くなる分を含めた所要時間[分]
        "slope_aware": _has_elevation,  # False なら標高データが無く、坂道を考慮できていない
    }


def straight_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """2地点間の直線距離[m]。"""
    return osm.haversine(*a, *b)


MINIMIZE_KEYS = {"time": "duration_min", "distance": "distance_m"}


def find_route_between(starts: list[tuple], goals: list[tuple], speeds: WalkSpeeds,
                       minimize: str = "time") -> dict:
    """出発候補と目的候補（施設の入口など）の組み合わせのうち、一番早い（短い）経路を返す。

    starts, goals: [(名前, (緯度, 経度)), ...] または [(名前, (緯度, 経度), 追加距離[m]), ...]
      ※追加距離は、道の外（敷地内など）を歩く分。平らな道として時間に足し、合計の距離にも足す
    speeds: 利用者の歩く速さ（普通の道・坂道）
    minimize: "time"（坂道考慮：所要時間が一番短い）か "distance"（通常：距離が一番短い）
    戻り値: {"coords": [...], "distance_m": 合計距離[m], "slope_m": 坂道の長さ[m],
             "duration_min": 所要時間[分], "slope_aware": bool,
             "start": {"name", "lat", "lon", "extra_m"}, "goal": {"name", "lat", "lon", "extra_m"}}
    """
    if _graph is None:
        raise RouteError(f"地図データがありません（{GRAPH_PATH} を置いてください）")

    def snap_all(points, label):
        snapped = []
        for item in points:
            name, point = item[0], item[1]
            extra = item[2] if len(item) > 2 else 0.0
            try:
                snapped.append((name, point, extra, _snap(point, label)))
            except RouteError:
                continue  # 地図データの範囲外の入口は候補から外す
        if not snapped:
            raise RouteError(f"{label}が地図データの範囲外です（青い枠の中を選んでください）")
        return snapped

    key = MINIMIZE_KEYS[minimize]
    sources = snap_all(starts, "出発地")
    targets = snap_all(goals, "目的地")
    best = None
    too_close = False
    blocked_hit = False
    for s_name, s_point, s_extra, source in sources:
        for g_name, g_point, g_extra, target in targets:
            if source == target:
                too_close = True
                continue
            path = _shortest_path(source, target, speeds, minimize)
            if path is None:
                blocked_hit = blocked_hit or bool(_removed_edges())
                continue
            result = _path_result(source, path, speeds, minimize)
            result["distance_m"] += s_extra + g_extra
            result["duration_min"] += (s_extra + g_extra) / speeds.flat
            if best is None or result[key] < best[key]:
                result["start"] = {"name": s_name, "lat": s_point[0], "lon": s_point[1], "extra_m": s_extra}
                result["goal"] = {"name": g_name, "lat": g_point[0], "lon": g_point[1], "extra_m": g_extra}
                best = result
    if best is None:
        if too_close:
            raise RouteError("出発地と目的地が近すぎます。離れた場所を選んでください")
        if blocked_hit:
            raise RouteError(_AVOID_MESSAGE)
        raise RouteError("目的地までの経路が見つかりません")
    return best


def find_route_to_nearest(orig: tuple[float, float], goals: list[tuple[str, tuple[float, float]]],
                          speeds: WalkSpeeds) -> dict:
    """出発地（1地点）から、複数のゴールのうち所要時間が一番短いものへの経路を返す。"""
    return find_route_between([("", orig)], goals, speeds)


def find_route(orig: tuple[float, float], dest: tuple[float, float], speeds: WalkSpeeds) -> dict:
    """(緯度, 経度) 同士の、所要時間が一番短い経路を返す。

    戻り値: {"coords": [[緯度, 経度], ...], "distance_m": 距離[m], "slope_m": 坂道の長さ[m],
             "duration_min": 所要時間[分], "slope_aware": bool}
    """
    if _graph is None:
        raise RouteError(f"地図データがありません（{GRAPH_PATH} を置いてください）")
    source = _snap(orig, "出発地")
    target = _snap(dest, "目的地")
    if source == target:
        raise RouteError("出発地と目的地が近すぎます。離れた場所を選んでください")
    path = _shortest_path(source, target, speeds, "time")
    if path is None:
        raise _no_route_error("経路が見つかりません")
    return _path_result(source, path, speeds, "time")
