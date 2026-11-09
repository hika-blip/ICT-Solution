r"""道路の幅を計算して road_widths.json（リポジトリ直下）に保存する（経路検索の「細道を通らない」処理が読む）。

  入力:  data/FG-GML-*.zip（基盤地図情報の道路縁）と data/map.graphml
  出力:  road_widths.json（リポジトリ直下）   [[始点ノード, 終点ノード, 長さ[m], 幅[m]], ...]（幅が測れた道だけ）

実行（リポジトリのルートで）:
    .venv\Scripts\python.exe scripts\build_road_widths.py
zip や map.graphml を入れ替えたときにやり直す。数十秒〜数分かかる。
幅の測り方は try_road_width.py と同じ（このファイルはそれを呼ぶだけ）。
"""
import glob
import json
import sys

import try_road_width as trw   # 同じ scripts/ フォルダにある


def main():
    zips = sorted(glob.glob(str(trw.DATA / "FG-GML-*.zip")))
    if not zips:
        sys.exit(f"{trw.DATA} に FG-GML-*.zip がありません")
    results, _, _ = trw.compute_widths(zips)
    rows = [[r["u"], r["v"], r["len"], r["w"]] for r in results if r["w"] is not None]
    out = trw.BASE / "road_widths.json"
    out.write_text(json.dumps(rows), encoding="utf-8")
    trw.summarize(results)
    trw.log(f"\n保存しました: {out}（幅が測れた道 {len(rows)} 本 / 全 {len(results)} 本）")


if __name__ == "__main__":
    main()
