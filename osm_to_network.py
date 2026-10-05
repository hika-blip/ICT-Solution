#!/usr/bin/env python3
"""OpenStreetMap から道路ネットワークを取得するプログラム (osmnx 使用)

取得範囲の指定方法（いずれか1つ）:
  --place   地名         例: --place "Katsuta, Hitachinaka, Ibaraki, Japan"
  --point   緯度,経度    例: --point 36.397,140.531 --dist 1500   (半径 dist メートル)
  --bbox    西,南,東,北  例: --bbox 140.50,36.37,140.56,36.42

使い方:
  python osm_to_network.py --place "Hitachinaka, Ibaraki, Japan" --type drive
  python osm_to_network.py --point 36.397,140.531 --dist 1500 --type walk
  python osm_to_network.py --bbox 140.50,36.37,140.56,36.42 --out katsuta

出力（--out で指定した接頭辞。既定は osm_network）:
  <out>.graphml       グラフ（QGIS/Gephi/networkx で読み込み可）
  <out>_nodes.csv     ノード一覧（id, 緯度経度, 次数）
  <out>_edges.csv     エッジ一覧（始点, 終点, 長さ[m], 道路種別, 名称 など）
  <out>.gpkg          GIS用 GeoPackage（--gpkg 指定時）
  <out>.png           地図の可視化

必要ライブラリ:
  pip install osmnx matplotlib
  ※ 実行時に OpenStreetMap の Overpass API / Nominatim へインターネット接続が必要です。
"""
import argparse
import csv
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
    args = ap.parse_args()

    ox.settings.use_cache = not args.cache_off
    ox.settings.log_console = False

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


if __name__ == "__main__":
    main()
