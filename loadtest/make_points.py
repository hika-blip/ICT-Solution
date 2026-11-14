"""負荷テスト用の地点（道の上の点）を地図データから抜き出し、loadtest/points.json に保存する。

ランダムな緯度経度だと海や山の中になり「経路が見つかりません」が混ざるので、
経路検索と同じグラフ（細道・通行不可を除いたもの）の「互いに行き来できる一番大きなまとまり」から
ノードを選ぶ。どの2点を選んでも経路が見つかる。

リポジトリのルートで、仮想環境を有効にしてから実行する:
    python loadtest/make_points.py            # 200地点
    python loadtest/make_points.py --count 300 --seed 1
"""
import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import networkx as nx  # noqa: E402

from backend import routing  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent / "points.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=200, help="抜き出す地点の数")
    parser.add_argument("--seed", type=int, default=0, help="乱数の種（同じ値なら毎回同じ地点になる）")
    args = parser.parse_args()

    if not routing.load_graph():
        sys.exit(f"地図データがありません: {routing.GRAPH_PATH}")
    G = routing._search_graph()
    biggest = max(nx.strongly_connected_components(G), key=len)
    print(f"ノード数: 全体 {G.number_of_nodes()} / 行き来できる最大のまとまり {len(biggest)}")

    nodes = random.Random(args.seed).sample(sorted(biggest), min(args.count, len(biggest)))
    points = [[round(G.nodes[n]["y"], 7), round(G.nodes[n]["x"], 7)] for n in nodes]
    OUT_PATH.write_text(json.dumps(points), encoding="utf-8")
    print(f"{len(points)} 地点を保存しました: {OUT_PATH}")


if __name__ == "__main__":
    main()
