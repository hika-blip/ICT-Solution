"""経路検索用の地図データ（data/map.graphml）に標高を書き込む。

坂道を考慮した経路検索に使う。標高は国土地理院の標高タイルから取る（backend/elevation.py）。
取得したタイルは data/dem_tiles/ に保存され、歩行ログの坂道の判定にも使われる。

書き込むもの:
  ノード  elevation     交差点などの標高[m]
  辺      elev_profile  道に沿って約 SAMPLE_STEP_M ごとに測った標高[m]をカンマ区切りにしたもの
                        （両端を含み、等間隔。backend/routing.py が坂道の長さを計算するのに使う）
橋・トンネルは地面の標高が道の高さと違うため、両端の標高だけを使う（途中は一定の傾きとみなす）。

リポジトリのルートで、仮想環境を有効にしてから実行する（インターネット接続が必要なのはこのときだけ）:
    python scripts/add_elevation.py
data/map.graphml を作り直したときは、もう一度実行する。実行後はサーバを再起動する。
"""
import argparse
import math
import sys
from pathlib import Path

import osmnx as ox

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import osm_to_network as osm  # noqa: E402
from backend.elevation import SAMPLE_STEP_M, DemTiles, ElevationUnavailable  # noqa: E402

DEFAULT_GRAPH = ROOT / "data" / "map.graphml"


def edge_points(G, u, v, data) -> list[tuple[float, float]]:
    """辺に沿った (緯度, 経度) の列（u 側から）。"""
    geom = data.get("geometry")
    if geom is None:
        return [(G.nodes[u]["y"], G.nodes[u]["x"]), (G.nodes[v]["y"], G.nodes[v]["x"])]
    pts = [(y, x) for x, y in geom.coords]
    uy, ux = G.nodes[u]["y"], G.nodes[u]["x"]
    if osm.haversine(uy, ux, *pts[0]) > osm.haversine(uy, ux, *pts[-1]):
        pts.reverse()
    return pts


def resample(pts: list[tuple[float, float]], step: float) -> list[tuple[float, float]]:
    """折れ線の上に、両端を含めて等間隔（step 以下）に点を置く。"""
    seg = [osm.haversine(*a, *b) for a, b in zip(pts, pts[1:])]
    total = sum(seg)
    count = max(1, math.ceil(total / step))
    out = []
    i, start = 0, 0.0  # i: いまいる区間, start: その区間の始まりまでの距離
    for k in range(count + 1):
        target = total * k / count
        while i < len(seg) - 1 and start + seg[i] < target:
            start += seg[i]
            i += 1
        t = 0.0 if seg[i] == 0 else min(1.0, max(0.0, (target - start) / seg[i]))
        (lat1, lon1), (lat2, lon2) = pts[i], pts[i + 1]
        out.append((lat1 + (lat2 - lat1) * t, lon1 + (lon2 - lon1) * t))
    return out


def is_structure(data) -> bool:
    """橋・トンネルなど、道の高さが地面と違うところか。"""
    return any(str(data.get(tag, "no")).lower() not in ("no", "nan", "none", "")
               for tag in ("bridge", "tunnel"))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--graphml", type=Path, default=DEFAULT_GRAPH, help="書き込む graphml（既定: data/map.graphml）")
    args = parser.parse_args()
    if not args.graphml.exists():
        sys.exit(f"{args.graphml} がありません。先に README の手順で地図データを作ってください。")

    osm.prefer_ipv4()
    G = ox.load_graphml(args.graphml)
    dem = DemTiles()
    try:
        add_elevations(G, dem)
    except ElevationUnavailable as e:
        sys.exit(f"{e}\nインターネット接続を確認して、もう一度実行してください（取得済みのタイルは data/dem_tiles/ に残ります）。")
    ox.save_graphml(G, args.graphml)
    print(f"保存しました: {args.graphml}（使ったタイル {dem.tile_count} 枚）")
    print("サーバを再起動すると、坂道を考慮した経路検索になります。")


def add_elevations(G, dem: DemTiles) -> None:
    """G のノードに elevation、辺に elev_profile を書き込む。"""
    print(f"ノード {G.number_of_nodes()} 件の標高を取得しています…")
    missing_nodes = 0
    for n, d in G.nodes(data=True):
        h = dem.elevation(d["y"], d["x"])
        if h is None:
            missing_nodes += 1
            d.pop("elevation", None)
        else:
            d["elevation"] = h

    print(f"辺 {G.number_of_edges()} 件に沿った標高を取得しています…")
    missing_edges = 0
    for u, v, d in G.edges(data=True):
        hu, hv = G.nodes[u].get("elevation"), G.nodes[v].get("elevation")
        if hu is None or hv is None:
            d.pop("elev_profile", None)
            missing_edges += 1
            continue
        if is_structure(d):
            profile = [hu, hv]
        else:
            pts = resample(edge_points(G, u, v, d), SAMPLE_STEP_M)
            profile = [hu] + [dem.elevation(*p) for p in pts[1:-1]] + [hv]
            if any(h is None for h in profile):
                profile = [hu, hv]  # 途中が測れないときは両端だけで傾きを見る
        d["elev_profile"] = ",".join(f"{h:.2f}" for h in profile)

    if missing_nodes:
        print(f"※ 標高を取れなかったノード {missing_nodes} 件・辺 {missing_edges} 件は、平らな道として扱います。")


if __name__ == "__main__":
    main()
