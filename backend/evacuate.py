"""避難場所・避難所への経路検索（現在地から近い順にトップ3）。

避難先は DB（evacuation_sites）が正本で、管理画面（backend/admin.py）から追加・編集する。
最初の読み込みとバックアップには、日立市オープンデータ「指定避難場所」「指定避難所」（CC BY 4.0）の CSV を使う：
  https://www.city.hitachi.lg.jp/opendata/datalist/1009216.html
  seeds/evacuation_sites.csv   指定避難場所（082023_evacuation_space2.csv）
  seeds/evacuation_shelters.csv 指定避難所（082023_evacuation_site2.csv を UTF-8 に変換したもの）
  1行目: タイトル行、2行目: 見出し（No.,名称,住所,緯度,経度[,非公開]）、3行目以降: データ
  - 起動時に DB の避難先が空なら CSV から読み込む
  - 管理画面の「CSVに書き出す」で DB → CSV、「CSVから読み込み直す」で CSV → DB
両方に載っている施設（同じ座標）は1件にまとめ、kinds に両方の種別を入れる。
非公開（hidden=1）の避難先は、経路検索の候補にしない。

API:
  GET /api/evacuate/routes?lat=&lon=&count=3
      現在地から、道のりが短い順に避難先への経路を最大 count 件（既定3）返す。
"""
import csv

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import auth, routing
from .db import BASE_DIR, connect

router = APIRouter()

SESSION_COOKIE = "session"   # main.py と同じ名前
SEEDS_DIR = BASE_DIR / "seeds"
# (ファイル名, 種別, DBの列, CSVのタイトル行, CSVの名称の列名)。先に書いたほうの名前を、重複した施設の名前に使う
SOURCES = [
    ("evacuation_sites.csv", "避難場所", "is_place", "指定避難場所", "指定避難場所"),
    ("evacuation_shelters.csv", "避難所", "is_shelter", "指定避難所", "避難所"),
]
KINDS = [kind for _, kind, *_ in SOURCES]

DEFAULT_COUNT = 3
MAX_COUNT = 10
CANDIDATES = 10   # 直線距離で絞る候補数（全件の経路探索は重いため）

# 公開中の避難先 [{"no", "name", "address", "lat", "lon", "kinds"}, ...]。DB から読んでおく
_sites: list[dict] = []


def _ensure_table(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS evacuation_sites (
               id         INTEGER PRIMARY KEY AUTOINCREMENT,
               name       TEXT NOT NULL,
               address    TEXT NOT NULL DEFAULT '',
               lat        REAL NOT NULL,
               lon        REAL NOT NULL,
               is_place   INTEGER NOT NULL DEFAULT 0,  -- 指定避難場所
               is_shelter INTEGER NOT NULL DEFAULT 0,  -- 指定避難所
               hidden     INTEGER NOT NULL DEFAULT 0   -- 1 なら経路検索の候補にしない
           )"""
    )


def _read_sites(path, kind: str) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    # 1行目はタイトル行なので飛ばし、2行目の見出しから列の位置を決める
    # （名称の列名はファイルごとに違う。「指定避難場所」「避難所」）
    header = [h.strip() for h in rows[1]] if len(rows) > 1 else []
    try:
        i_no, i_addr = header.index("No."), header.index("住所")
        i_lat, i_lon = header.index("緯度"), header.index("経度")
    except ValueError:
        raise ValueError(f"{path.name}: 見出し行（No.,名称,住所,緯度,経度）が見つかりません")
    i_hidden = header.index("非公開") if "非公開" in header else None
    i_name = i_no + 1
    sites = []
    for row in rows[2:]:
        if len(row) <= max(i_lat, i_lon, i_name):
            continue
        try:
            lat, lon = float(row[i_lat]), float(row[i_lon])
        except ValueError:
            continue  # 座標が無い行は候補にできない
        hidden = i_hidden is not None and len(row) > i_hidden and row[i_hidden].strip() == "1"
        sites.append({
            "name": row[i_name].strip(),
            "address": row[i_addr].strip(),
            "lat": lat,
            "lon": lon,
            "kind": kind,
            "hidden": hidden,
        })
    return sites


def import_from_csv() -> int:
    """seeds/ の CSV の内容で DB の避難先を入れ替え、件数を返す（ファイルが無ければ飛ばす）。"""
    by_point: dict = {}
    for name, kind, column, *_ in SOURCES:
        path = SEEDS_DIR / name
        if not path.exists():
            continue
        for site in _read_sites(path, kind):
            key = (round(site["lat"], 6), round(site["lon"], 6))
            if key not in by_point:
                by_point[key] = {**site, "is_place": 0, "is_shelter": 0}
            by_point[key][column] = 1
            by_point[key]["hidden"] = by_point[key]["hidden"] and site["hidden"]
    with connect() as conn:
        _ensure_table(conn)
        conn.execute("DELETE FROM evacuation_sites")
        conn.executemany(
            """INSERT INTO evacuation_sites (name, address, lat, lon, is_place, is_shelter, hidden)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            [(s["name"], s["address"], s["lat"], s["lon"], s["is_place"], s["is_shelter"], 1 if s["hidden"] else 0)
             for s in by_point.values()],
        )
    reload_sites()
    return len(by_point)


def export_to_csv() -> int:
    """DB の避難先を seeds/ の2つの CSV（避難場所・避難所）に書き出し、件数を返す。"""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM evacuation_sites ORDER BY id").fetchall()
    SEEDS_DIR.mkdir(parents=True, exist_ok=True)
    for name, kind, column, title, name_header in SOURCES:
        with open(SEEDS_DIR / name, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow([title, "", "", "", "", ""])
            w.writerow(["No.", name_header, "住所", "緯度", "経度", "非公開"])
            for no, r in enumerate([r for r in rows if r[column]], start=1):
                w.writerow([no, r["name"], r["address"], f"{r['lat']:.6f}", f"{r['lon']:.6f}",
                            "1" if r["hidden"] else ""])
    return len(rows)


def reload_sites() -> None:
    """経路検索で使う避難先の一覧を DB から読み直す（管理画面で編集したあとにも呼ぶ）。"""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM evacuation_sites WHERE hidden = 0 ORDER BY id").fetchall()
    _sites.clear()
    for r in rows:
        _sites.append({
            "no": str(r["id"]),
            "name": r["name"],
            "address": r["address"],
            "lat": r["lat"],
            "lon": r["lon"],
            "kinds": [kind for _, kind, column, *_ in SOURCES if r[column]],
        })


def load_sites() -> int:
    """起動時に呼ぶ。DB の避難先が空なら CSV から読み込み、公開中の件数を返す。"""
    with connect() as conn:
        _ensure_table(conn)
        empty = conn.execute("SELECT COUNT(*) FROM evacuation_sites").fetchone()[0] == 0
    if empty:
        import_from_csv()
    else:
        reload_sites()
    return len(_sites)


def top_routes(orig: tuple[float, float], count: int = DEFAULT_COUNT) -> list[dict]:
    """現在地から道のりが短い順に、避難場所への経路を最大 count 件返す。

    直線距離が近い CANDIDATES 件に絞ってから経路探索し、道のりで並べ替える。
    戻り値の各要素: routing.find_route の結果 + {"site": {"no", "name", "address", "lat", "lon", "kinds"}}
    """
    if not _sites:
        raise routing.RouteError("避難先のデータがありません")
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
        raise error or routing.RouteError("避難先までの経路が見つかりません")
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
