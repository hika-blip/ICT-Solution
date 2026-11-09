"""【お試し用】基盤地図情報（道路縁）から道路の幅を測り、地図上で色分けして表示する。

アプリ本体（backend/・frontend/）からは使わない単独のスクリプト。
  入力:  data/FG-GML-*.zip（基盤地図情報の道路縁を含むzip。展開せず、zipの中を直接読む）
         data/map.graphml（経路探索に使っている道路網。道路の中心線として使う）
  出力:  data/road_width.html（ブラウザで開くと、幅で色分けした地図が見られる）

実行（リポジトリのルートで）:
    .venv\\Scripts\\python.exe scripts\\try_road_width.py
    .venv\\Scripts\\python.exe scripts\\try_road_width.py --with-edges   # 道路縁の線も重ねられるようにする（重い）

幅の測り方:
  1. 道路の中心線（グラフの辺）の上に STEP_M ごとに点を置く
  2. 各点から、中心線に直角な方向の左右へ線を伸ばし、いちばん近い道路縁までの距離を測る
  3. 左 + 右 をその点の幅とし、辺ごとに中央値を「その道の幅」とする
  ※ 道路縁は種別「真幅道路」だけ使う（庭園路等・徒歩道・トンネル内は使わない）
"""
import argparse
import glob
import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import osmnx as ox
from shapely.geometry import LineString, Point
from shapely.strtree import STRtree

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
OUT = DATA / "road_width.html"

NS = "{http://fgd.gsi.go.jp/spec/2008/FGD_GMLSchema}"
GML = "{http://www.opengis.net/gml/3.2}"
USE_TYPES = {"真幅道路"}          # 幅を測るのに使う道路縁の種別
STEP_M = 5.0                      # 中心線に点を置く間隔[m]
MAX_SIDE_M = 25.0                 # 片側で道路縁を探す最大の距離[m]
END_TRIM_M = 8.0                  # 交差点の影響を避けるため、辺の両端からこの距離は測らない
MIN_VALID_RATIO = 0.3             # 測れた点がこの割合より少ない辺は「不明」にする
NOT_FOR_CARS = {"footway", "path", "cycleway", "steps", "pedestrian", "bridleway", "corridor"}

# 幅の区分 (上限[m], 色, ラベル)
BINS = [
    (3.0, "#B00020", "3m未満（とても細い）"),
    (5.5, "#F28C00", "3〜5.5m（細い）"),
    (9.0, "#2E9D4F", "5.5〜9m（ふつう）"),
    (float("inf"), "#1A73E8", "9m以上（広い）"),
]
UNKNOWN = ("#888888", "不明（道路縁が見つからない）")
PEDESTRIAN = ("#B0B0B0", "歩行者の道（測らない）")


def log(msg):
    print(msg, flush=True)


def read_road_edges(zip_paths):
    """zip の中の道路縁(RdEdg)を読む。戻り値: [[(lat, lon), ...], ...]（USE_TYPES のものだけ）。"""
    lines, skipped = [], 0
    for zp in zip_paths:
        with zipfile.ZipFile(zp) as z:
            names = [n for n in z.namelist() if "-RdEdg-" in n]
            for name in names:
                with z.open(name) as f:
                    for _, el in ET.iterparse(f, events=("end",)):
                        if el.tag != NS + "RdEdg":
                            continue
                        kind = (el.findtext(NS + "type") or "").strip()
                        if kind in USE_TYPES:
                            for pl in el.iter(GML + "posList"):
                                v = [float(t) for t in (pl.text or "").split()]
                                if len(v) >= 4:
                                    lines.append(list(zip(v[0::2], v[1::2])))  # (緯度, 経度)
                        else:
                            skipped += 1
                        el.clear()
        log(f"  読み込み: {Path(zp).name}")
    log(f"  使う道路縁: {len(lines)} 本 / 使わない（種別が対象外）: {skipped} 本")
    return lines


def compute_widths(zips):
    """道路縁のzipから、道路網の辺ごとの幅を測る。

    戻り値: (辺ごとの結果のリスト, 地図に重ねる道路縁 [[(緯度, 経度), ...], ...], 地図の中心 (緯度, 経度))
    各結果: {"u", "v", "ll", "hw", "name", "len", "w"(幅[m]。測れなければ None), "valid", "total"}
    """
    log("1) 道路縁を読み込み中…")
    edge_ll = read_road_edges(zips)

    log("2) 道路網（中心線）を読み込み中…")
    G = ox.load_graphml(DATA / "map.graphml")
    ys = [d["y"] for _, d in G.nodes(data=True)]
    xs = [d["x"] for _, d in G.nodes(data=True)]
    lat0, lon0 = float(np.mean(ys)), float(np.mean(xs))
    kx, ky = 111_320 * np.cos(np.radians(lat0)), 110_574   # 緯度経度[度] → メートルの係数

    def to_m(lonlat):
        return [((lon - lon0) * kx, (lat - lat0) * ky) for lon, lat in lonlat]

    # 道路縁を、使う範囲（グラフの範囲の少し外まで）に絞ってメートル座標の線にする
    pad = 0.003
    south, north, west, east = min(ys) - pad, max(ys) + pad, min(xs) - pad, max(xs) + pad
    edge_geoms, edge_ll_used = [], []
    for ll in edge_ll:
        if any(south <= la <= north and west <= lo <= east for la, lo in ll):
            edge_geoms.append(LineString(to_m([(lo, la) for la, lo in ll])))
            edge_ll_used.append(ll)
    log(f"  範囲内の道路縁: {len(edge_geoms)} 本")
    tree = STRtree(edge_geoms)

    log("3) 道路ごとに幅を測定中…")
    seen, results = set(), []
    for u, v, k, d in G.edges(keys=True, data=True):
        key = (frozenset((u, v)), round(d["length"], 1))
        if u == v or key in seen:
            continue
        seen.add(key)
        geom = d.get("geometry")
        if geom is not None:
            lonlat = list(geom.coords)
        else:
            lonlat = [(G.nodes[u]["x"], G.nodes[u]["y"]), (G.nodes[v]["x"], G.nodes[v]["y"])]
        hw = d["highway"] if isinstance(d["highway"], str) else d["highway"][0]
        rec = {"u": u, "v": v, "ll": [[lat, lon] for lon, lat in lonlat], "hw": hw,
               "name": d.get("name") if isinstance(d.get("name"), str) else "",
               "len": round(float(d["length"]), 1), "w": None, "valid": 0, "total": 0}
        if hw not in NOT_FOR_CARS:
            line = LineString(to_m(lonlat))
            widths = measure(line, tree, edge_geoms)
            rec["total"], rec["valid"] = widths[1], len(widths[0])
            if widths[1] and len(widths[0]) / widths[1] >= MIN_VALID_RATIO:
                rec["w"] = round(float(np.median(widths[0])), 1)
        results.append(rec)
    return results, edge_ll_used, (lat0, lon0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-edges", action="store_true", help="道路縁の線も地図に重ねられるようにする")
    args = ap.parse_args()

    zips = sorted(glob.glob(str(DATA / "FG-GML-*.zip")))
    if not zips:
        sys.exit(f"{DATA} に FG-GML-*.zip がありません")
    results, edge_ll_used, center = compute_widths(zips)
    summarize(results)
    write_html(results, edge_ll_used if args.with_edges else None, center)
    log(f"出力しました: {OUT}\nブラウザで開いて確認してください。")


def measure(line, tree, edge_geoms):
    """中心線 line の上の各点で、左右の道路縁までの距離を足した幅[m]を測る。

    戻り値: (測れた幅のリスト, 測ろうとした点の数)
    """
    length = line.length
    if length <= 0:
        return [], 0
    start, end = (END_TRIM_M, length - END_TRIM_M) if length > 2 * END_TRIM_M + STEP_M else (0.0, length)
    ds = np.arange(start, end + 1e-9, STEP_M) if end > start else np.array([length / 2])
    widths = []
    for dist in ds:
        p = line.interpolate(dist)
        a, b = line.interpolate(max(dist - 1.0, 0)), line.interpolate(min(dist + 1.0, length))
        tx, ty = b.x - a.x, b.y - a.y
        norm = np.hypot(tx, ty)
        if norm == 0:
            continue
        nx_, ny_ = -ty / norm, tx / norm    # 進行方向に直角（左向き）
        sides = []
        for s in (1.0, -1.0):
            ray = LineString([(p.x, p.y), (p.x + s * nx_ * MAX_SIDE_M, p.y + s * ny_ * MAX_SIDE_M)])
            best = None
            for i in tree.query(ray, predicate="intersects"):
                dd = Point(p.x, p.y).distance(ray.intersection(edge_geoms[i]))
                if best is None or dd < best:
                    best = dd
            sides.append(best)
        if sides[0] is not None and sides[1] is not None:
            widths.append(sides[0] + sides[1])
    return widths, len(ds)


def classify(w):
    for upper, color, label in BINS:
        if w < upper:
            return color, label
    return BINS[-1][1], BINS[-1][2]


def summarize(results):
    counts, lengths = {}, {}
    for r in results:
        if r["hw"] in NOT_FOR_CARS:
            label = PEDESTRIAN[1]
        elif r["w"] is None:
            label = UNKNOWN[1]
        else:
            label = classify(r["w"])[1]
        counts[label] = counts.get(label, 0) + 1
        lengths[label] = lengths.get(label, 0) + r["len"]
    log("\n--- 結果（道路の数 / 合計の長さ）---")
    for label in [b[2] for b in BINS] + [UNKNOWN[1], PEDESTRIAN[1]]:
        log(f"  {label:<24} {counts.get(label, 0):>5} 本 / {lengths.get(label, 0) / 1000:>6.1f} km")
    # 種別ごとの幅の中央値（参考）
    by_hw = {}
    for r in results:
        if r["w"] is not None:
            by_hw.setdefault(r["hw"], []).append(r["w"])
    log("\n--- 種別ごとの幅の中央値（参考）---")
    for hw, ws in sorted(by_hw.items(), key=lambda x: -len(x[1])):
        log(f"  {hw:<16} 中央値 {np.median(ws):>5.1f} m（{len(ws)} 本）")


HTML = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>道幅の色分け（お試し）</title>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<style>
  html, body, #map { height: 100%; margin: 0; }
  .legend { background: #fff; padding: 8px 12px; border-radius: 8px; box-shadow: 0 1px 6px rgba(0,0,0,.3); font: 13px/1.6 sans-serif; }
  .legend i { display: inline-block; width: 24px; height: 5px; margin-right: 8px; vertical-align: middle; border-radius: 2px; }
  .legend label { display: block; margin-top: 6px; }
</style></head><body><div id="map"></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
const ROADS = __ROADS__;
const EDGES = __EDGES__;
const LEGEND = __LEGEND__;
const map = L.map("map", { preferCanvas: true }).setView([__LAT__, __LON__], 15);
// 背景は国土地理院の標準地図（file:// で開いても表示できる。OSM公式タイルは参照元がないと拒否されることがある）
L.tileLayer("https://cyberjapandata.gsi.go.jp/xyz/std/{z}/{x}/{y}.png", { maxZoom: 19, maxNativeZoom: 18, opacity: 0.8,
  attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank">地理院タイル</a> / 基盤地図情報（国土地理院）' }).addTo(map);
const roadLayer = L.layerGroup().addTo(map);
ROADS.forEach((r) => {
  L.polyline(r.ll, { color: r.c, weight: r.t, opacity: 0.9 })
    .bindTooltip(r.tip, { sticky: true }).addTo(roadLayer);
});
map.fitBounds(L.polyline(ROADS.flatMap((r) => r.ll)).getBounds());
const edgeLayer = L.layerGroup();
if (EDGES) EDGES.forEach((ll) => L.polyline(ll, { color: "#000", weight: 1, opacity: 0.7, interactive: false }).addTo(edgeLayer));
const legend = L.control({ position: "bottomright" });
legend.onAdd = () => {
  const div = L.DomUtil.create("div", "legend");
  div.innerHTML = "<b>道幅（道路縁から測定）</b>" + LEGEND.map((x) => `<div><i style="background:${x[0]}"></i>${x[1]}</div>`).join("") +
    (EDGES ? '<label><input type="checkbox" id="edge-toggle"> 道路縁の線を表示</label>' : "");
  L.DomEvent.disableClickPropagation(div);
  return div;
};
legend.addTo(map);
const toggle = document.getElementById("edge-toggle");
if (toggle) toggle.addEventListener("change", () => toggle.checked ? edgeLayer.addTo(map) : map.removeLayer(edgeLayer));
</script></body></html>
"""


def write_html(results, edges_ll, center):
    roads = []
    for r in results:
        if r["hw"] in NOT_FOR_CARS:
            color, weight, label = PEDESTRIAN[0], 2, PEDESTRIAN[1]
            tip = f"{r['name'] or '(名称なし)'} / {r['hw']} / 歩行者の道"
        elif r["w"] is None:
            color, weight, label = UNKNOWN[0], 3, UNKNOWN[1]
            tip = f"{r['name'] or '(名称なし)'} / {r['hw']} / 幅: 不明（測れた点 {r['valid']}/{r['total']}）"
        else:
            color, label = classify(r["w"])
            weight = 5
            tip = f"{r['name'] or '(名称なし)'} / {r['hw']} / 幅 約{r['w']}m（測れた点 {r['valid']}/{r['total']}）"
        roads.append({"ll": r["ll"], "c": color, "t": weight, "tip": tip})
    legend = [[b[1], b[2]] for b in BINS] + [list(UNKNOWN), list(PEDESTRIAN)]
    edges = [[[la, lo] for la, lo in ll] for ll in edges_ll] if edges_ll else None
    html = (HTML.replace("__ROADS__", json.dumps(roads, ensure_ascii=False))
            .replace("__EDGES__", json.dumps(edges))
            .replace("__LEGEND__", json.dumps(legend, ensure_ascii=False))
            .replace("__LAT__", str(center[0])).replace("__LON__", str(center[1])))
    OUT.write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
