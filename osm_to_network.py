"""OpenStreetMap から道路ネットワークを取得するプログラム (osmnx 使用)


取得範囲の指定方法（いずれか1つ）:
  --place   地名         例: --place "Katsuta, Hitachinaka, Ibaraki, Japan"
  --point   緯度,経度    例: --point 36.397,140.531 --dist 1500   (半径 dist メートル)
  --bbox    西,南,東,北  例: --bbox 140.50,36.37,140.56,36.42

使い方:
  python osm_to_network.py --place "Hitachinaka, Ibaraki, Japan" --type drive
  python osm_to_network.py --point 36.397,140.531 --dist 1500 --type walk
  python osm_to_network.py --bbox 140.50,36.37,140.56,36.42 --out katsuta

最短経路探索（--orig と --dest を指定すると実行）:
  python osm_to_network.py --point 36.397,140.531 --dist 1500       --orig 36.392,140.525 --dest 36.402,140.538
  # 保存済みの graphml を再利用（再ダウンロードしない）
  python osm_to_network.py --graphml osm_network.graphml       --orig 36.392,140.525 --dest 36.402,140.538 --weight travel_time --algo dijkstra

出力（--out で指定した接頭辞。既定は osm_network）:
  <out>.graphml       グラフ（QGIS/Gephi/networkx で読み込み可）
  <out>_nodes.csv     ノード一覧（id, 緯度経度, 次数）
  <out>_edges.csv     エッジ一覧（始点, 終点, 長さ[m], 道路種別, 名称 など）
  <out>.gpkg          GIS用 GeoPackage（--gpkg 指定時）
  <out>.png           地図の可視化
  <out>_route.csv     最短経路のノード列（--orig/--dest 指定時）
  <out>_route.png     地図画像(OSMタイル)上に経路を重ねた図（--orig/--dest 指定時）

必要ライブラリ:
  pip install osmnx matplotlib contextily
  ※ 実行時に OpenStreetMap の Overpass API / Nominatim へインターネット接続が必要です。
"""
import argparse
import csv
import heapq
import itertools
import math
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import osmnx as ox


def fetch_graph(args):
    """指定方法に応じて道路ネットワーク(MultiDiGraph)を取得する。"""
    kw = dict(network_type=args.type, simplify=not args.no_simplify,
              retain_all=args.retain_all)
    if args.place:
        return ox.graph_from_place(args.place, **kw)
    if args.point:
        lat, lon = (float(v) for v in args.point.split(","))
        return ox.graph_from_point((lat, lon), dist=args.dist, **kw)
    west, south, east, north = (float(v) for v in args.bbox.split(","))
    # osmnx 2.x は bbox=(left, bottom, right, top)
    return ox.graph_from_bbox((west, south, east, north), **kw)


EARTH_RADIUS_M = 6_371_009


def haversine(lat1, lon1, lat2, lon2):
    """2点間の大円距離[m]。"""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def parse_latlon(text):
    lat, lon = (float(v) for v in text.split(","))
    return lat, lon


def nearest_node(G, lat, lon):
    """指定座標に最も近いノードを返す（全探索）。"""
    return min(G.nodes,
               key=lambda n: haversine(lat, lon, G.nodes[n]["y"], G.nodes[n]["x"]))


def edge_cost(G, u, v, weight):
    """u→v の並行エッジのうち最小の重みを返す。"""
    return min(d[weight] for d in G[u][v].values())


def shortest_path(G, source, target, weight="length", algo="astar"):
    """Dijkstra 法 / A* 法による最短経路探索。

    algo="astar" のとき、ヒューリスティックとして目的地までの直線距離
    （travel_time の場合はそれを最高速度で割った時間）を用いる。
    直線距離は道路距離以下なので許容的であり、最適解が保証される。
    戻り値: (ノード列, 総コスト)。到達不能なら (None, inf)。
    """
    ty, tx = G.nodes[target]["y"], G.nodes[target]["x"]
    if algo == "astar":
        scale = 1.0
        if weight == "travel_time":  # 最高速度[m/s]で割り、時間の下界にする
            scale = 1 / max(d["speed_kph"] for _, _, d in G.edges(data=True)) * 3.6

        def h(n):
            return haversine(G.nodes[n]["y"], G.nodes[n]["x"], ty, tx) * scale
    else:
        def h(n):
            return 0.0

    dist = {source: 0.0}
    prev = {}
    done = set()
    tie = itertools.count()  # 同コスト時の比較用（ノードIDを比較させない）
    heap = [(h(source), next(tie), source)]
    while heap:
        _, _, u = heapq.heappop(heap)
        if u in done:
            continue
        if u == target:
            path = [u]
            while path[-1] != source:
                path.append(prev[path[-1]])
            return path[::-1], dist[target]
        done.add(u)
        for v in G.successors(u):
            if v in done:
                continue
            nd = dist[u] + edge_cost(G, u, v, weight)
            if nd < dist.get(v, math.inf):
                dist[v] = nd
                prev[v] = u
                heapq.heappush(heap, (nd + h(v), next(tie), v))
    return None, math.inf


def plot_route_on_map(G, path, orig, dest, out_path):
    """OSM のタイル地図の上に経路を描画して保存する。

    タイルの取得にインターネット接続が必要。取得できない場合は
    地図なし（線のみ）の図にフォールバックする。
    """
    nodes = ox.graph_to_gdfs(G, edges=False).to_crs(epsg=3857)
    xs = [nodes.geometry[n].x for n in path]
    ys = [nodes.geometry[n].y for n in path]

    fig, ax = plt.subplots(figsize=(10, 10))
    ax.plot(xs, ys, color="tab:red", linewidth=4, solid_capstyle="round", zorder=3)
    ax.scatter(xs[0], ys[0], c="tab:green", s=120, edgecolors="white", zorder=4, label="Start")
    ax.scatter(xs[-1], ys[-1], c="tab:blue", s=120, edgecolors="white", zorder=4, label="Goal")
    pad = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.15 + 100
    ax.set_xlim(min(xs) - pad, max(xs) + pad)
    ax.set_ylim(min(ys) - pad, max(ys) + pad)
    ax.set_aspect("equal")
    ax.set_axis_off()
    try:
        import contextily as cx
        # OSM のタイル利用ポリシーにより、アプリを識別できる User-Agent が必須
        cx.add_basemap(ax, source=cx.providers.OpenStreetMap.Mapnik, crs="EPSG:3857",
                       headers={"User-Agent": "ICT-Solution-route-viewer/0.1 (student project)"})
    except Exception as e:
        print(f"  地図タイルを取得できませんでした（線のみで出力）: {type(e).__name__}: {e}")
    ax.legend(loc="upper right")
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def find_route(G, args):
    """--orig/--dest 間の最短経路を求めて表示・保存する。"""
    if args.weight == "travel_time":
        G = ox.add_edge_speeds(G)
        G = ox.add_edge_travel_times(G)
    orig = nearest_node(G, *parse_latlon(args.orig))
    dest = nearest_node(G, *parse_latlon(args.dest))
    path, cost = shortest_path(G, orig, dest, weight=args.weight, algo=args.algo)
    if path is None:
        print(f"経路が見つかりません: {orig} → {dest}（一方通行・非連結の可能性）")
        return

    length = sum(edge_cost(G, u, v, "length") for u, v in zip(path, path[1:]))
    with open(args.out + "_route.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["seq", "node", "lat", "lon"])
        for i, n in enumerate(path):
            w.writerow([i, n, G.nodes[n]["y"], G.nodes[n]["x"]])
    if len(path) > 1:
        plot_route_on_map(G, path, orig, dest, args.out + "_route.png")

    print(f"最短経路 ({args.algo}, weight={args.weight}): {orig} → {dest}")
    print(f"  経由ノード数: {len(path)}  距離: {length:.0f} m"
          + (f"  所要時間: {cost / 60:.1f} 分" if args.weight == "travel_time" else ""))
    print(f"  出力: {args.out}_route.csv" + (" / _route.png" if len(path) > 1 else ""))


def save_csv(G, prefix):
    nodes, edges = ox.graph_to_gdfs(G)
    with open(prefix + "_nodes.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["node", "lat", "lon", "degree"])
        for n, d in G.nodes(data=True):
            w.writerow([n, d["y"], d["x"], G.degree(n)])
    cols = [c for c in ("highway", "name", "length", "oneway", "maxspeed", "lanes")
            if c in edges.columns]
    edges.reset_index()[["u", "v"] + cols].to_csv(
        prefix + "_edges.csv", index=False, encoding="utf-8")
    return nodes, edges


def plot_graph(G, path):
    fig, ax = ox.plot_graph(G, node_size=6, node_color="tab:red",
                            edge_color="tab:blue", edge_linewidth=0.8,
                            bgcolor="white", show=False, close=False)
    fig.savefig(path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description="OpenStreetMap から道路ネットワークを取得")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--place", help="地名（Nominatimで検索）")
    g.add_argument("--point", help="中心点 '緯度,経度'（--dist と併用）")
    g.add_argument("--bbox", help="範囲 '西,南,東,北'（経度,緯度,経度,緯度）")
    g.add_argument("--graphml", help="保存済みの graphml を読み込む（取得・保存を省略）")
    ap.add_argument("--dist", type=float, default=1000, help="--point の半径[m]")
    ap.add_argument("--type", default="drive",
                    choices=["drive", "drive_service", "walk", "bike", "all", "all_public"],
                    help="道路種別（既定: drive）")
    ap.add_argument("--no-simplify", action="store_true",
                    help="グラフの単純化をしない（曲がり角もノードになる）")
    ap.add_argument("--retain-all", action="store_true",
                    help="最大連結成分以外の孤立した道路も残す")
    ap.add_argument("--gpkg", action="store_true", help="GeoPackage も出力する")
    ap.add_argument("--out", default="osm_network", help="出力ファイル名の接頭辞")
    ap.add_argument("--cache-off", action="store_true", help="osmnx のキャッシュを使わない")
    ap.add_argument("--orig", help="経路探索の出発地 '緯度,経度'（最寄りノードに吸着）")
    ap.add_argument("--dest", help="経路探索の目的地 '緯度,経度'（最寄りノードに吸着）")
    ap.add_argument("--weight", default="length", choices=["length", "travel_time"],
                    help="最短化する量: 距離[m] / 所要時間[s]（既定: length）")
    ap.add_argument("--algo", default="astar", choices=["astar", "dijkstra"],
                    help="探索アルゴリズム（既定: astar）")
    args = ap.parse_args()
    if bool(args.orig) != bool(args.dest):
        ap.error("--orig と --dest は両方指定してください")

    ox.settings.use_cache = not args.cache_off
    ox.settings.log_console = False

    if args.graphml:
        G = ox.load_graphml(args.graphml)
        if args.orig:
            find_route(G, args)
        return

    try:
        G = fetch_graph(args)
    except Exception as e:  # ネットワーク不通・地名が見つからない等
        sys.exit(f"取得に失敗しました: {type(e).__name__}: {e}\n"
                 "インターネット接続（overpass-api.de / nominatim.openstreetmap.org）"
                 "と指定内容を確認してください。")

    ox.save_graphml(G, args.out + ".graphml")
    nodes, edges = save_csv(G, args.out)
    if args.gpkg:
        ox.save_graph_geopackage(G, args.out + ".gpkg")
    plot_graph(G, args.out + ".png")

    total_km = edges["length"].sum() / 1000
    n_comp = nx.number_weakly_connected_components(G)
    print(f"ノード数: {G.number_of_nodes()}  エッジ数: {G.number_of_edges()}  "
          f"総延長: {total_km:.1f} km  連結成分: {n_comp}")
    print(f"出力: {args.out}.graphml / _nodes.csv / _edges.csv / .png"
          + (" / .gpkg" if args.gpkg else ""))
    if args.orig:
        find_route(G, args)


if __name__ == "__main__":
    main()
