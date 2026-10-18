"""探索範囲内の OSM の施設(POI)を Overpass API で取得し、CSV に書き出す。

使い方:
  python scripts/export_pois.py [出力CSV] [--bbox 西,南,東,北]
既定: data/pois.csv、範囲は日立駅〜常陸多賀駅・茨城大学日立キャンパス周辺
      (osm_to_network.py の取得範囲と同じ 140.610,36.548,140.670,36.595)。

列: カテゴリ(キー=値)・種別名(日本語)・名称・緯度・経度・住所・営業時間・電話・運営者・
    車いす・避難所候補(要確認)・OSM型/ID。UTF-8(BOM付き)なので Excel でそのまま開ける。
"""
import csv
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KEYS = ("amenity", "shop", "tourism", "railway", "leisure", "emergency", "healthcare")

# 日本語の種別名（ここにないものは「キー=値」のまま出す）
LABELS = {
    "amenity=parking": "駐車場", "amenity=place_of_worship": "神社・寺・教会",
    "amenity=restaurant": "飲食店", "amenity=school": "学校", "amenity=fast_food": "ファストフード",
    "amenity=toilets": "公衆トイレ", "amenity=bank": "銀行", "amenity=post_office": "郵便局",
    "amenity=community_centre": "公民館・コミュニティセンター", "amenity=bicycle_parking": "駐輪場",
    "amenity=hospital": "病院", "amenity=pharmacy": "薬局", "amenity=library": "図書館",
    "amenity=atm": "ATM", "amenity=courthouse": "裁判所", "amenity=shelter": "シェルター(雨宿り)",
    "amenity=kindergarten": "幼稚園", "amenity=clinic": "診療所", "amenity=police": "警察",
    "amenity=fire_station": "消防署", "amenity=townhall": "市役所・役場", "amenity=cafe": "カフェ",
    "amenity=college": "短大・専門学校", "amenity=university": "大学", "amenity=bus_station": "バスターミナル",
    "amenity=fuel": "ガソリンスタンド", "amenity=childcare": "保育所", "amenity=social_facility": "福祉施設",
    "shop=convenience": "コンビニ", "shop=supermarket": "スーパー", "shop=car": "自動車販売",
    "shop=mobile_phone": "携帯電話店", "leisure=park": "公園", "leisure=pitch": "競技場(グラウンド等)",
    "leisure=track": "トラック", "leisure=sports_hall": "体育館", "leisure=miniature_golf": "ミニゴルフ",
    "leisure=playground": "遊び場", "leisure=sports_centre": "スポーツセンター",
    "tourism=hotel": "ホテル", "railway=station": "駅", "railway=halt": "停留所",
    "emergency=assembly_point": "緊急集合場所", "emergency=defibrillator": "AED",
    "emergency=fire_hydrant": "消火栓", "amenity=pub": "居酒屋・パブ", "amenity=public_bath": "銭湯",
    "amenity=theatre": "劇場", "amenity=taxi": "タクシー乗り場", "amenity=fountain": "噴水",
    "amenity=car_rental": "レンタカー", "amenity=crematorium": "火葬場", "amenity=grave_yard": "墓地",
    "amenity=prep_school": "学習塾", "amenity=social_centre": "集会所", "amenity=dojo": "武道場",
    "amenity=ice_cream": "アイスクリーム店", "amenity=planetarium": "プラネタリウム",
    "amenity=luggage_locker": "コインロッカー", "amenity=shower": "シャワー",
    "leisure=fitness_centre": "フィットネス", "leisure=swimming_pool": "プール", "leisure=stadium": "競技場(スタジアム)",
    "leisure=garden": "庭園", "leisure=bowling_alley": "ボウリング場", "leisure=amusement_arcade": "ゲームセンター",
    "leisure=adult_gaming_centre": "パチンコ等", "leisure=slipway": "スロープ", "leisure=swimming_area": "海水浴場",
    "shop=books": "書店", "shop=clothes": "衣料品店", "shop=car_repair": "自動車整備", "shop=car_parts": "カー用品",
    "shop=doityourself": "ホームセンター", "shop=alcohol": "酒店", "shop=confectionery": "菓子店", "shop=deli": "惣菜店",
    "healthcare=yes": "医療関連",
}
# 避難所になりうる種別（OSM には「避難所」の情報がないため、公式データでの照合が必要）
CANDIDATES = {"amenity=school", "amenity=community_centre", "amenity=townhall", "amenity=library",
              "amenity=college", "amenity=university", "amenity=kindergarten", "leisure=sports_hall",
              "leisure=sports_centre", "emergency=assembly_point", "amenity=shelter"}


def fetch(bbox):
    west, south, east, north = bbox
    b = f"{south},{west},{north},{east}"
    # railway は線路・信号などを除き、駅・停留所だけにする
    body = "".join(f'nwr["{k}"]({b});' if k != "railway"
                   else f'nwr["railway"~"^(station|halt|tram_stop)$"]({b});' for k in KEYS)
    q = f"[out:json][timeout:120];({body});out tags center;"
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": q}).encode(),
        headers={"User-Agent": "ICT-Solution-poi-export/0.1 (student project)"})
    return json.load(urllib.request.urlopen(req, timeout=160))["elements"]


def category(tags):
    for k in KEYS:
        if k in tags:
            return f"{k}={tags[k]}"
    return ""


def address(t):
    if "addr:full" in t:
        return t["addr:full"]
    parts = [t.get(k, "") for k in ("addr:city", "addr:suburb", "addr:quarter", "addr:neighbourhood",
                                    "addr:block_number", "addr:housenumber")]
    return "".join(parts)


def main():
    args = sys.argv[1:]
    bbox = (140.610, 36.548, 140.670, 36.595)
    if "--bbox" in args:
        i = args.index("--bbox")
        bbox = tuple(float(v) for v in args[i + 1].split(","))
        del args[i:i + 2]
    out = Path(args[0]) if args else ROOT / "data" / "pois.csv"
    elements = fetch(bbox)
    rows = []
    for e in elements:
        t = e.get("tags", {})
        lat = e.get("lat") or e.get("center", {}).get("lat")
        lon = e.get("lon") or e.get("center", {}).get("lon")
        if lat is None:
            continue
        cat = category(t)
        rows.append({
            "カテゴリ": cat, "種別": LABELS.get(cat, cat), "名称": t.get("name", ""),
            "緯度": round(lat, 6), "経度": round(lon, 6), "住所": address(t),
            "営業時間": t.get("opening_hours", ""), "電話": t.get("phone", t.get("contact:phone", "")),
            "運営者": t.get("operator", ""), "車いす": t.get("wheelchair", ""),
            "避難所候補(要確認)": "○" if cat in CANDIDATES else "",
            "OSM型": e["type"], "OSM_ID": e["id"],
        })
    # 名称ありを先に、種別順→名称順
    rows.sort(key=lambda r: (r["名称"] == "", r["種別"], r["名称"]))
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    named = sum(1 for r in rows if r["名称"])
    print(f"{out}: {len(rows)} 件（名称あり {named} 件、避難所候補 "
          f"{sum(1 for r in rows if r['避難所候補(要確認)'])} 件）")


if __name__ == "__main__":
    main()
