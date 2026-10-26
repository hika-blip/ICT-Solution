"""避難場所への経路検索（現在地から近い順にトップ3）。

避難場所は data/evacuation_sites.csv から起動時に読み込む（DBには入れない）。
  1行目: タイトル行、2行目: 見出し（No.,指定避難場所,住所,緯度,経度）、3行目以降: データ

API:
  GET /api/evacuate/routes?lat=&lon=&count=3
      現在地から、道のりが短い順に避難場所への経路を最大 count 件（既定3）返す。
"""
import csv

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import auth, routing
from .db import BASE_DIR

router = APIRouter()

SESSION_COOKIE = "session"   # main.py と同じ名前
SITES_PATH = BASE_DIR / "data" / "evacuation_sites.csv"

DEFAULT_COUNT = 3
MAX_COUNT = 10
CANDIDATES = 10   # 直線距離で絞る候補数（全件の経路探索は重いため）

# 避難場所 [{"no", "name", "address", "lat", "lon"}, ...]。起動時に読む
_sites: list[dict] = []


def load_sites() -> int:
    """起動時に呼ぶ。CSV から避難場所を読み込み、件数を返す（ファイルが無ければ0件）。"""
    _sites.clear()
    if not SITES_PATH.exists():
        return 0
    with open(SITES_PATH, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    # 1行目はタイトル行なので飛ばし、2行目の見出しから列の位置を決める
    header = [h.strip() for h in rows[1]] if len(rows) > 1 else []
    try:
        i_no, i_name, i_addr = header.index("No."), header.index("指定避難場所"), header.index("住所")
        i_lat, i_lon = header.index("緯度"), header.index("経度")
    except ValueError:
        raise ValueError(f"{SITES_PATH.name}: 見出し行（No.,指定避難場所,住所,緯度,経度）が見つかりません")
    for row in rows[2:]:
        if len(row) <= max(i_lat, i_lon):
            continue
        try:
            lat, lon = float(row[i_lat]), float(row[i_lon])
        except ValueError:
            continue  # 座標が無い行は候補にできない
        _sites.append({
            "no": row[i_no].strip(),
            "name": row[i_name].strip(),
            "address": row[i_addr].strip(),
            "lat": lat,
            "lon": lon,
        })
    return len(_sites)


def top_routes(orig: tuple[float, float], count: int = DEFAULT_COUNT) -> list[dict]:
    """現在地から道のりが短い順に、避難場所への経路を最大 count 件返す。

    直線距離が近い CANDIDATES 件に絞ってから経路探索し、道のりで並べ替える。
    戻り値の各要素: routing.find_route の結果 + {"site": {"no", "name", "address", "lat", "lon"}}
    """
    if not _sites:
        raise routing.RouteError("避難場所のデータがありません")
    nearest = sorted(_sites, key=lambda s: routing.straight_m(orig, (s["lat"], s["lon"])))
    results = []
    error = None
    for site in nearest[:max(CANDIDATES, count)]:
        try:
            route = routing.find_route(orig, (site["lat"], site["lon"]))
        except routing.RouteError as e:
            error = e  # 範囲外・近すぎ・経路なしの避難場所は候補から外す
            continue
        route["site"] = site
        results.append(route)
    if not results:
        raise error or routing.RouteError("避難場所までの経路が見つかりません")
    results.sort(key=lambda r: r["distance_m"])
    return results[:count]


@router.get("/api/evacuate/routes")
def evacuate_routes(request: Request, lat: float, lon: float, count: int = DEFAULT_COUNT):
    if auth.get_user_by_session(request.cookies.get(SESSION_COOKIE)) is None:
        return JSONResponse({"error": "ログインが必要です"}, status_code=401)
    if not 1 <= count <= MAX_COUNT:
        return JSONResponse({"error": f"count は1〜{MAX_COUNT}で指定してください"}, status_code=400)
    try:
        return {"routes": top_routes((lat, lon), count)}
    except routing.RouteError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
