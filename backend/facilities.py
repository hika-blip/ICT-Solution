"""施設（駅・学校・観光地など）と、その入口の管理。

施設・入口・敷地は DB（facilities / facility_entrances / facility_area_points）が正本で、
管理画面（backend/admin.py）から追加・編集する。
seeds/ の CSV は「最初の読み込み」と「班での共有・バックアップ」に使う：
  - 起動時に DB の施設が空なら、CSV から読み込む
  - 管理画面の「CSVに書き出す」で DB → CSV、「CSVから読み込み直す」で CSV → DB

  seeds/facilities.csv  : id,name,category,lat,lon,description,hidden（hidden は 1 で非公開。省略可）
  seeds/entrances.csv   : facility_id,name,lat,lon,note
  seeds/facility_areas.csv : facility_id,lat,lon（敷地の外周を順番に並べた多角形。任意）

敷地（facility_areas）が登録された施設では、出発地・目的地にした地点が敷地の中なら、
その地点ではなく施設の入口（門）から経路を探す（敷地内の通路は地図データに無いため）。
非公開（hidden=1）の施設は、住民の画面・経路検索には出さない。

API:
  GET /api/facilities                 施設と入口の一覧（公開中のもの）
  GET /api/route/plan                 経路検索。出発地・目的地のそれぞれに地点か施設を指定できる
                                      （施設のとき・敷地内の地点のときは、入口の組み合わせのうち
                                        一番近いものを使う）
"""
import csv
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import auth, routing, walks
from .db import BASE_DIR, connect

router = APIRouter()

SESSION_COOKIE = "session"   # main.py と同じ名前
SEEDS_DIR = BASE_DIR / "seeds"

# 施設の種類（seeds/facilities.csv の category に書ける値）
CATEGORIES = ["駅", "学校", "観光地", "避難所"]

# 公開中の施設の敷地（施設ID → [(緯度, 経度), ...]）。DB から読んでおく（経路検索で毎回使うため）
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


def _ensure_tables(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS facilities (
            id          TEXT PRIMARY KEY,       -- 例: hitachi-station
            name        TEXT NOT NULL,
            category    TEXT NOT NULL,          -- 駅 / 学校 / 観光地 / 避難所
            lat         REAL NOT NULL,          -- ピンを立てる位置
            lon         REAL NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            hidden      INTEGER NOT NULL DEFAULT 0   -- 1 なら住民の画面に出さない
        );
        CREATE TABLE IF NOT EXISTS facility_entrances (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            facility_id TEXT NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,          -- 例: 正門、中央口
            lat         REAL NOT NULL,          -- 経路のゴールにする位置
            lon         REAL NOT NULL,
            note        TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS facility_area_points (
            facility_id TEXT NOT NULL REFERENCES facilities(id) ON DELETE CASCADE,
            seq         INTEGER NOT NULL,       -- 外周に沿った順番
            lat         REAL NOT NULL,
            lon         REAL NOT NULL,
            PRIMARY KEY (facility_id, seq)
        );
        """
    )
    # 以前の版の DB には hidden 列が無いので足す
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(facilities)")}
    if "hidden" not in cols:
        conn.execute("ALTER TABLE facilities ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0")


def _read_seed_files():
    """seeds/ の3つの CSV を読み、内容をチェックして返す。"""
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
    areas: dict[str, list[tuple[float, float]]] = {}
    for a in area_points:
        if a["facility_id"] not in ids:
            raise ValueError(f"seeds/facility_areas.csv: 施設ID '{a['facility_id']}' が facilities.csv にありません")
        areas.setdefault(a["facility_id"], []).append((float(a["lat"]), float(a["lon"])))
    for fid, pts in areas.items():
        if len(pts) < 3:
            raise ValueError(f"seeds/facility_areas.csv: '{fid}' の敷地は3点以上必要です")
    return facilities, entrances, areas


def _insert_areas(conn, areas: dict) -> None:
    conn.executemany(
        "INSERT INTO facility_area_points (facility_id, seq, lat, lon) VALUES (?, ?, ?, ?)",
        [(fid, i, la, lo) for fid, pts in areas.items() for i, (la, lo) in enumerate(pts)],
    )


def import_from_csv() -> dict:
    """seeds/ の CSV の内容で、DB の施設・入口・敷地を入れ替える。件数を返す。"""
    facilities, entrances, areas = _read_seed_files()
    with connect() as conn:
        _ensure_tables(conn)
        conn.execute("DELETE FROM facility_area_points")
        conn.execute("DELETE FROM facility_entrances")
        conn.execute("DELETE FROM facilities")
        conn.executemany(
            "INSERT INTO facilities (id, name, category, lat, lon, description, hidden) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(f["id"], f["name"], f["category"], float(f["lat"]), float(f["lon"]),
              f.get("description", ""), 1 if f.get("hidden") in ("1", "true", "TRUE") else 0)
             for f in facilities],
        )
        conn.executemany(
            "INSERT INTO facility_entrances (facility_id, name, lat, lon, note) VALUES (?, ?, ?, ?, ?)",
            [(e["facility_id"], e["name"], float(e["lat"]), float(e["lon"]), e.get("note", ""))
             for e in entrances],
        )
        _insert_areas(conn, areas)
    reload_areas()
    return {"facilities": len(facilities), "entrances": len(entrances), "areas": len(areas)}


def export_to_csv() -> dict:
    """DB の施設・入口・敷地を seeds/ の CSV に書き出す（上書き）。件数を返す。"""
    with connect() as conn:
        facilities = conn.execute("SELECT * FROM facilities ORDER BY category, id").fetchall()
        entrances = conn.execute("SELECT * FROM facility_entrances ORDER BY facility_id, id").fetchall()
        points = conn.execute("SELECT * FROM facility_area_points ORDER BY facility_id, seq").fetchall()
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)

    def write(name, header, rows):
        with open(SEEDS_DIR / name, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows)

    write("facilities.csv", ["id", "name", "category", "lat", "lon", "description", "hidden"],
          [(f["id"], f["name"], f["category"], f"{f['lat']:.6f}", f"{f['lon']:.6f}", f["description"],
            1 if f["hidden"] else 0) for f in facilities])
    write("entrances.csv", ["facility_id", "name", "lat", "lon", "note"],
          [(e["facility_id"], e["name"], f"{e['lat']:.6f}", f"{e['lon']:.6f}", e["note"]) for e in entrances])
    write("facility_areas.csv", ["facility_id", "lat", "lon"],
          [(p["facility_id"], f"{p['lat']:.6f}", f"{p['lon']:.6f}") for p in points])
    return {"facilities": len(facilities), "entrances": len(entrances),
            "areas": len({p["facility_id"] for p in points})}


def reload_areas() -> None:
    """経路検索で使う敷地の一覧を DB から読み直す（施設を追加・編集したあとにも呼ぶ）。"""
    _areas.clear()
    with connect() as conn:
        rows = conn.execute(
            """SELECT p.facility_id, p.lat, p.lon FROM facility_area_points p
               JOIN facilities f ON f.id = p.facility_id
               WHERE f.hidden = 0 ORDER BY p.facility_id, p.seq"""
        ).fetchall()
    for r in rows:
        _areas.setdefault(r["facility_id"], []).append((r["lat"], r["lon"]))
    for fid in [k for k, v in _areas.items() if len(v) < 3]:
        del _areas[fid]


def load_seeds() -> None:
    """起動時に呼ぶ。テーブルを用意し、DB が空のときだけ CSV から読み込む。"""
    with connect() as conn:
        _ensure_tables(conn)
        empty = conn.execute("SELECT COUNT(*) FROM facilities").fetchone()[0] == 0
        no_areas = conn.execute("SELECT COUNT(*) FROM facility_area_points").fetchone()[0] == 0
    if empty:
        import_from_csv()
        return
    if no_areas:
        # 以前の版の DB（敷地を DB に持っていなかった）向け：敷地だけ CSV から足す
        _, _, areas = _read_seed_files()
        with connect() as conn:
            ids = {r[0] for r in conn.execute("SELECT id FROM facilities")}
            _insert_areas(conn, {fid: pts for fid, pts in areas.items() if fid in ids})
    reload_areas()


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
    """公開中の施設を返す（非公開・存在しなければ None）。"""
    return conn.execute("SELECT * FROM facilities WHERE id = ? AND hidden = 0", (facility_id,)).fetchone()


@router.get("/api/facilities")
def list_facilities(request: Request):
    if not _logged_in(request):
        return JSONResponse({"error": "ログインが必要です"}, status_code=401)
    with connect() as conn:
        rows = conn.execute("SELECT * FROM facilities WHERE hidden = 0 ORDER BY category, name").fetchall()
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
    user = auth.get_user_by_session(request.cookies.get(SESSION_COOKIE))
    if user is None:
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
        result = routing.find_route_between(starts, goals, walks.route_speeds(user["id"]))
    except routing.RouteError as e:
        return JSONResponse({"error": str(e)}, status_code=503)
    result["orig_facility"] = orig_info
    result["dest_facility"] = dest_info
    return result
