"""施設（駅・学校・観光地など）と、その入口の管理。

施設と入口の座標は seeds/ の CSV ファイルで管理し、GitHub で共有する。
アプリ起動時に CSV の内容で DB の facilities / facility_entrances を作り直す
（DB を直接書き換えても、次の起動で CSV の内容に戻る）。

  seeds/facilities.csv  : id,name,category,lat,lon,description
  seeds/entrances.csv   : facility_id,name,lat,lon,note
  seeds/facility_areas.csv : facility_id,lat,lon（敷地の外周を順番に並べた多角形。任意）

敷地（facility_areas）が登録された施設では、出発地・目的地にした地点が敷地の中なら、
その地点ではなく施設の入口（門）から経路を探す（敷地内の通路は地図データに無いため）。

API:
  GET /api/facilities                 施設と入口の一覧
  GET /api/route/plan                 経路検索。出発地・目的地のそれぞれに地点か施設を指定できる
                                      （施設のとき・敷地内の地点のときは、入口の組み合わせのうち
                                        一番近いものを使う）
"""
import csv
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import auth, routing
from .db import BASE_DIR, connect

router = APIRouter()

SESSION_COOKIE = "session"   # main.py と同じ名前
SEEDS_DIR = BASE_DIR / "seeds"

# 施設の種類（seeds/facilities.csv の category に書ける値）
CATEGORIES = ["駅", "学校", "観光地", "避難所"]

# 施設の敷地（施設ID → [(緯度, 経度), ...]）。起動時に seeds/facility_areas.csv から読む
_areas: dict[str, list[tuple[float, float]]] = {}


def _read_csv(name: str) -> list[dict]:
    path = SEEDS_DIR / name
    if not path.exists():
        return []
    # Excel で保存した CSV（BOM付き）でも読めるよう utf-8-sig にする
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [
            {k.strip(): (v or "").strip() for k, v in row.items() if k}
            for row in csv.DictReader(f)
        ]


def load_seeds() -> None:
    """起動時に呼ぶ。テーブルを作り、CSV の内容で入れ直す。"""
    facilities = _read_csv("facilities.csv")
    entrances = _read_csv("entrances.csv")
    area_points = _read_csv("facility_areas.csv")

    ids = {f["id"] for f in facilities}
    for f in facilities:
        if f["category"] not in CATEGORIES:
            raise ValueError(f"seeds/facilities.csv: 種類 '{f['category']}' は使えません（{CATEGORIES}）")
    for e in entrances:
        if e["facility_id"] not in ids:
            raise ValueError(f"seeds/entrances.csv: 施設ID '{e['facility_id']}' が facilities.csv にありません")

    _areas.clear()
    for a in area_points:
        if a["facility_id"] not in ids:
            raise ValueError(f"seeds/facility_areas.csv: 施設ID '{a['facility_id']}' が facilities.csv にありません")
        _areas.setdefault(a["facility_id"], []).append((float(a["lat"]), float(a["lon"])))
    for fid, pts in _areas.items():
        if len(pts) < 3:
            raise ValueError(f"seeds/facility_areas.csv: '{fid}' の敷地は3点以上必要です")

    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS facilities (
                id          TEXT PRIMARY KEY,       -- 例: hitachi-station
                name        TEXT NOT NULL,
                category    TEXT NOT NULL,          -- 駅 / 学校 / 観光地 / 避難所
                lat         REAL NOT NULL,          -- ピンを立てる位置
                lon         REAL NOT NULL,
                description TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS facility_entrances (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                facility_id TEXT NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
                name        TEXT NOT NULL,          -- 例: 正門、中央口
                lat         REAL NOT NULL,          -- 経路のゴールにする位置
                lon         REAL NOT NULL,
                note        TEXT NOT NULL DEFAULT ''
            );
            """
        )
        conn.execute("DELETE FROM facility_entrances")
        conn.execute("DELETE FROM facilities")
        conn.executemany(
            "INSERT INTO facilities (id, name, category, lat, lon, description) VALUES (?, ?, ?, ?, ?, ?)",
            [(f["id"], f["name"], f["category"], float(f["lat"]), float(f["lon"]), f.get("description", ""))
             for f in facilities],
        )
        conn.executemany(
            "INSERT INTO facility_entrances (facility_id, name, lat, lon, note) VALUES (?, ?, ?, ?, ?)",
            [(e["facility_id"], e["name"], float(e["lat"]), float(e["lon"]), e.get("note", ""))
             for e in entrances],
        )


def _inside(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    """点が多角形の中にあるか（レイキャスティング法）。"""
    lat, lon = point
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        yi, xi = polygon[i]
        yj, xj = polygon[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def facility_containing(point: tuple[float, float]) -> Optional[str]:
    """地点を敷地に含む施設のIDを返す（どの敷地にも入っていなければ None）。"""
    for fid, polygon in _areas.items():
        if _inside(point, polygon):
            return fid
    return None


def _logged_in(request: Request) -> bool:
    return auth.get_user_by_session(request.cookies.get(SESSION_COOKIE)) is not None


def _get_facility(conn, facility_id: str):
    return conn.execute("SELECT * FROM facilities WHERE id = ?", (facility_id,)).fetchone()


@router.get("/api/facilities")
def list_facilities(request: Request):
    if not _logged_in(request):
        return JSONResponse({"error": "ログインが必要です"}, status_code=401)
    with connect() as conn:
        rows = conn.execute("SELECT * FROM facilities ORDER BY category, name").fetchall()
        ents = conn.execute("SELECT * FROM facility_entrances ORDER BY id").fetchall()
    by_facility: dict = {}
    for e in ents:
        by_facility.setdefault(e["facility_id"], []).append(
            {"name": e["name"], "lat": e["lat"], "lon": e["lon"]}
        )
    return [
        {"id": r["id"], "name": r["name"], "category": r["category"],
         "lat": r["lat"], "lon": r["lon"], "description": r["description"],
         "entrances": by_facility.get(r["id"], []),
         "area": [[la, lo] for la, lo in _areas.get(r["id"], [])]}
        for r in rows
    ]


def _facility_points(conn, facility_id: str):
    """施設の入口の一覧（入口が未登録ならピンの位置）と、施設情報を返す。"""
    facility = _get_facility(conn, facility_id)
    if facility is None:
        return None, None
    ents = conn.execute(
        "SELECT name, lat, lon FROM facility_entrances WHERE facility_id = ?", (facility_id,)
    ).fetchall()
    points = [(e["name"], (e["lat"], e["lon"])) for e in ents] or [("", (facility["lat"], facility["lon"]))]
    return points, {"id": facility["id"], "name": facility["name"]}


def _endpoints(conn, facility_id: str, lat, lon, label: str):
    """施設IDか緯度経度から、経路の候補地点のリストと施設情報を作る。

    候補地点は (名前, (緯度, 経度), 追加距離[m])。
    地点が施設の敷地の中なら、その地点から一番近い入口（門）を使い、
    地点から門までの直線距離を追加距離にする（敷地内の通路は地図データに無いため直線で数える）。
    """
    if facility_id:
        points, info = _facility_points(conn, facility_id)
        if points is None:
            raise LookupError(f"{label}の施設が見つかりません")
        return [(name, pt, 0.0) for name, pt in points], info
    if lat is None or lon is None:
        raise LookupError(f"{label}を指定してください")
    inside_id = facility_containing((lat, lon))
    if inside_id:
        points, info = _facility_points(conn, inside_id)
        if points is not None:
            info["inside"] = True  # 敷地内の地点から自動で門を使った
            name, pt = min(points, key=lambda p: routing.straight_m((lat, lon), p[1]))
            return [(name, pt, routing.straight_m((lat, lon), pt))], info
    return [("", (lat, lon), 0.0)], None


@router.get("/api/route/plan")
def route_plan(
    request: Request,
    orig_facility: str = "",
    orig_lat: Optional[float] = None,
    orig_lon: Optional[float] = None,
    dest_facility: str = "",
    dest_lat: Optional[float] = None,
    dest_lon: Optional[float] = None,
):
    if not _logged_in(request):
        return JSONResponse({"error": "ログインが必要です"}, status_code=401)
    if orig_facility and orig_facility == dest_facility:
        return JSONResponse({"error": "出発地と目的地が同じ施設です"}, status_code=400)
    try:
        with connect() as conn:
            starts, orig_info = _endpoints(conn, orig_facility, orig_lat, orig_lon, "出発地")
            goals, dest_info = _endpoints(conn, dest_facility, dest_lat, dest_lon, "目的地")
    except LookupError as e:
        return JSONResponse({"error": str(e)}, status_code=404)
    if orig_info and dest_info and orig_info["id"] == dest_info["id"]:
        return JSONResponse({"error": f"出発地と目的地が同じ施設（{orig_info['name']}）の中です"}, status_code=400)
    try:
        result = routing.find_route_between(starts, goals)
    except routing.RouteError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    result["orig_facility"] = orig_info
    result["dest_facility"] = dest_info
    return result
