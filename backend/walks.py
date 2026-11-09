"""歩行ログ（歩いた道の記録）API と画面。

  POST   /api/walks        記録を保存（記録中の点をまとめて受け取る）
  GET    /api/walks        自分の記録一覧（新しい順）
  GET    /api/walks/speed  自分の歩く速さ [m/分]（記録が足りなければ標準の速さ）と坂道での速さ
  GET    /api/walks/{id}   記録のまとめと点の座標
  DELETE /api/walks/{id}   自分の記録を削除
  GET    /walks            一覧の画面
  GET    /walks/{id}       詳細（地図）の画面

記録は本人にだけ見える。

坂道での速さ:
  保存するときに、記録した点の標高（国土地理院の標高タイル。backend/elevation.py）から
  坂道を歩いた区間を見分け、その距離と時間を walks に保存する（_slope_stats）。
  普通の道の速さは坂道を除いた区間で、坂道での速さは坂道の区間だけで計算する（my_speed）。
"""
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import List, NamedTuple, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import auth, elevation, routing
from .db import connect

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "frontend" / "templates"
)

SESSION_COOKIE = "session"   # main.py と同じ名前
MAX_POINTS = 5000            # 1回の記録の点の上限（数秒ごとに記録して数時間分）
MAX_ACCURACY_M = 50          # これより誤差の大きい点は使わない
STOP_SPEED = 0.3             # これより遅い区間は「止まっていた」とみなす [m/秒]
JUMP_SPEED = 5.0             # これより速い区間は測位の飛びとみなして点を捨てる [m/秒]
DEFAULT_SPEED = 80.0         # 記録がないときの標準の歩く速さ [m/分]（不動産の徒歩表示と同じ）
SPEED_RECENT = 10            # 歩く速さの計算に使う直近の記録の件数
SPEED_MIN_DISTANCE = 200     # これより短い記録は歩く速さの計算に使わない [m]
# 坂道での速さの実測値が無いときに、普通の道の速さに掛ける割合（仮の値。坂道の判定は routing.SLOPE_GRADE）
SLOPE_SPEED_RATIO = 0.5
SLOPE_MIN_DISTANCE = 100     # 直近の記録で坂道をこれだけ歩いたら、坂道の速さに実測値を使う [m]


def _user(request: Request):
    return auth.get_user_by_session(request.cookies.get(SESSION_COOKIE))


def _error(message: str, status: int):
    return JSONResponse({"error": message}, status_code=status)


def _iso(ms: float) -> str:
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec="seconds")


def _haversine(lat1, lon1, lat2, lon2) -> float:
    """2点間の距離 [m]"""
    r = 6371000
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _clean(points: list) -> tuple[list, float, float]:
    """誤差の大きい点・飛んだ点を除き、(使う点, 歩いた距離, 動いていた時間) を返す。

    止まっていた区間（STOP_SPEED 未満）は、その場での測位のゆれなので距離にも時間にも入れない。
    """
    points = sorted(
        (p for p in points if p.accuracy is None or p.accuracy <= MAX_ACCURACY_M),
        key=lambda p: p.t,
    )
    kept, distance, moving = [], 0.0, 0.0
    for p in points:
        if not kept:
            kept.append(p)
            continue
        prev = kept[-1]
        dt = (p.t - prev.t) / 1000
        if dt <= 0:
            continue
        d = _haversine(prev.lat, prev.lon, p.lat, p.lon)
        if d / dt > JUMP_SPEED:
            continue
        if d / dt >= STOP_SPEED:
            distance += d
            moving += dt
        kept.append(p)
    return kept, distance, moving


class _StoredPoint(NamedTuple):
    """DB に保存した点（_slope_stats に渡す形）。"""
    lat: float
    lon: float
    t: float  # UNIX時間 [ミリ秒]


def _slope_stats(points) -> tuple[float, float]:
    """坂道を歩いた (距離[m], 時間[秒]) を返す。points は _clean 後の点（lat, lon, t[ミリ秒]、記録順）。

    動いていた区間を道のりで約 SAMPLE_STEP_M ずつにまとめ、まとまりの始めと終わりの標高差 ÷ 道のりが
    routing.SLOPE_GRADE 以上（上り・下りとも）なら、そのまとまりの距離と時間を坂道として数える。
    経路検索の坂道の判定（20mごとの傾きが5%以上）と同じ基準にしている。
    止まっていた区間はどちらにも数えない。標高データが無い場所は平らな道とみなす。
    標高タイルを取得できなければ elevation.ElevationUnavailable。
    """
    if len(points) < 2:
        return 0.0, 0.0
    dem = elevation.shared()

    def steep(a, b, dist: float) -> bool:
        ha, hb = dem.elevation(a.lat, a.lon), dem.elevation(b.lat, b.lon)
        return ha is not None and hb is not None and abs(hb - ha) / dist >= routing.SLOPE_GRADE

    slope_d = slope_s = 0.0
    start = prev = points[0]
    chunk_d = chunk_s = 0.0  # いまのまとまりの、動いていた距離と時間
    for p in points[1:]:
        dt = (p.t - prev.t) / 1000
        if dt <= 0:
            continue
        d = _haversine(prev.lat, prev.lon, p.lat, p.lon)
        if d / dt >= STOP_SPEED:
            chunk_d += d
            chunk_s += dt
        prev = p
        if chunk_d >= elevation.SAMPLE_STEP_M:
            if steep(start, p, chunk_d):
                slope_d += chunk_d
                slope_s += chunk_s
            start, chunk_d, chunk_s = p, 0.0, 0.0
    # 最後の短い残りは、半分以上の長さがあれば同じように判定する
    if chunk_d >= elevation.SAMPLE_STEP_M / 2 and steep(start, prev, chunk_d):
        slope_d += chunk_d
        slope_s += chunk_s
    return slope_d, slope_s


def _slope_stats_or_none(points) -> tuple[float | None, float | None]:
    """_slope_stats の結果。標高を取得できなければ (None, None)（あとで backfill_slope_stats が計算し直す）。"""
    try:
        return _slope_stats(points)
    except elevation.ElevationUnavailable as e:
        logger.warning("歩行ログの坂道の計算を後回しにします: %s", e)
        return None, None


def backfill_slope_stats() -> int:
    """坂道の距離・時間がまだ無い記録（この機能より前の記録・保存時に標高を取れなかった記録）を計算する。

    起動時に別スレッドで呼ぶ。計算できた件数を返す。標高を取得できなくなったら、残りは次の起動時に回す。
    """
    with connect() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM walks WHERE slope_distance_m IS NULL")]
    done = 0
    for walk_id in ids:
        with connect() as conn:
            rows = conn.execute(
                "SELECT lat, lon, recorded_at FROM walk_points WHERE walk_id = ? ORDER BY seq", (walk_id,)
            ).fetchall()
        points = [_StoredPoint(r["lat"], r["lon"], datetime.fromisoformat(r["recorded_at"]).timestamp() * 1000)
                  for r in rows]
        slope_d, slope_s = _slope_stats_or_none(points)
        if slope_d is None:
            break
        with connect() as conn:
            # 保存した時刻は秒単位なので、合計の距離・時間をわずかに超えることがある。超えないようにそろえる
            conn.execute(
                "UPDATE walks SET slope_distance_m = MIN(?, distance_m), slope_moving_s = MIN(?, moving_s)"
                " WHERE id = ?",
                (slope_d, slope_s, walk_id),
            )
        done += 1
    if ids:
        logger.info("歩行ログの坂道の計算: %d / %d 件", done, len(ids))
    return done


def _summary(row) -> dict:
    moving = row["moving_s"]
    slope_d, slope_s = row["slope_distance_m"], row["slope_moving_s"]
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "distance_m": row["distance_m"],
        "duration_s": row["duration_s"],
        "moving_s": moving,
        "speed_m_per_min": row["distance_m"] / moving * 60 if moving > 0 else None,
        "slope_distance_m": slope_d,  # 坂道を歩いた距離（None は未計算）
        "slope_speed_m_per_min": slope_d / slope_s * 60 if slope_s else None,
    }


# ---------- API ----------

class Point(BaseModel):
    lat: float
    lon: float
    accuracy: Optional[float] = None
    t: float  # 記録した日時（UNIX時間 [ミリ秒]）


class WalkIn(BaseModel):
    points: List[Point]


@router.post("/api/walks")
def create_walk(request: Request, data: WalkIn):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    if len(data.points) > MAX_POINTS:
        return _error("記録が長すぎます。", 400)
    if any(not (-90 <= p.lat <= 90 and -180 <= p.lon <= 180) for p in data.points):
        return _error("位置が正しくありません。", 400)
    points, distance, moving = _clean(data.points)
    if len(points) < 2:
        return _error("記録できた位置が少ないため、保存できませんでした。", 400)
    slope_d, slope_s = _slope_stats_or_none(points)

    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO walks (user_id, started_at, ended_at, distance_m, duration_s, moving_s,"
            " slope_distance_m, slope_moving_s) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (user["id"], _iso(points[0].t), _iso(points[-1].t), distance,
             (points[-1].t - points[0].t) / 1000, moving, slope_d, slope_s),
        )
        walk_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO walk_points (walk_id, seq, lat, lon, accuracy, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(walk_id, i, p.lat, p.lon, p.accuracy, _iso(p.t)) for i, p in enumerate(points)],
        )
        row = conn.execute("SELECT * FROM walks WHERE id = ?", (walk_id,)).fetchone()
    return _summary(row)


@router.get("/api/walks")
def list_walks(request: Request):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM walks WHERE user_id = ? ORDER BY started_at DESC", (user["id"],)
        ).fetchall()
    return [_summary(r) for r in rows]


def my_speed(user_id: int) -> dict:
    """直近の記録から、普通の道と坂道での歩く速さ [m/分] を求める。

    普通の道の速さ（speed_m_per_min）: 坂道の区間を除いた距離 ÷ 時間。記録が足りなければ標準の速さ。
    坂道での速さ（slope_speed_m_per_min）: 直近の記録で坂道を合計 SLOPE_MIN_DISTANCE 以上歩いていれば
      その実測値、足りなければ 普通の道の速さ × SLOPE_SPEED_RATIO（slope_is_default=True）。
    slope_distance_m は直近の記録で坂道を歩いた合計距離（実測値を使うまでの残りの表示に使う）。
    """
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, SUM(distance_m) AS d, SUM(moving_s) AS s,"
            "       SUM(COALESCE(slope_distance_m, 0)) AS sd, SUM(COALESCE(slope_moving_s, 0)) AS ss FROM ("
            "  SELECT distance_m, moving_s, slope_distance_m, slope_moving_s FROM walks"
            "  WHERE user_id = ? AND distance_m >= ? AND moving_s > 0"
            "  ORDER BY started_at DESC LIMIT ?)",
            (user_id, SPEED_MIN_DISTANCE, SPEED_RECENT),
        ).fetchone()
    slope_d, slope_s = (row["sd"] or 0.0), (row["ss"] or 0.0)
    if not row["n"]:
        result = {"speed_m_per_min": DEFAULT_SPEED, "is_default": True, "walks": 0}
    else:
        flat_d, flat_s = row["d"] - slope_d, row["s"] - slope_s
        # ほとんど坂道だけの記録などで平らな区間が無ければ、全体の平均を使う
        flat = flat_d / flat_s * 60 if flat_d > 0 and flat_s > 0 else row["d"] / row["s"] * 60
        result = {"speed_m_per_min": flat, "is_default": False, "walks": row["n"]}
    measured = slope_d >= SLOPE_MIN_DISTANCE and slope_s > 0
    result["slope_is_default"] = not measured
    result["slope_speed_m_per_min"] = (slope_d / slope_s * 60 if measured
                                       else result["speed_m_per_min"] * SLOPE_SPEED_RATIO)
    result["slope_distance_m"] = slope_d
    result["slope_min_distance_m"] = SLOPE_MIN_DISTANCE
    return result


def speeds_from(info: dict) -> routing.WalkSpeeds:
    """my_speed の結果から、経路検索に使う歩く速さ（普通の道・坂道）を作る。"""
    return routing.WalkSpeeds(flat=info["speed_m_per_min"], slope=info["slope_speed_m_per_min"])


def route_speeds(user_id: int) -> routing.WalkSpeeds:
    """経路検索に使う歩く速さ（普通の道・坂道）。"""
    return speeds_from(my_speed(user_id))


@router.get("/api/walks/speed")
def walk_speed(request: Request):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    return my_speed(user["id"])


@router.get("/api/walks/{walk_id}")
def get_walk(request: Request, walk_id: int):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM walks WHERE id = ? AND user_id = ?", (walk_id, user["id"])
        ).fetchone()
        if row is None:
            return _error("記録が見つかりません。", 404)
        points = conn.execute(
            "SELECT lat, lon FROM walk_points WHERE walk_id = ? ORDER BY seq", (walk_id,)
        ).fetchall()
    return {**_summary(row), "coords": [[p["lat"], p["lon"]] for p in points]}


@router.delete("/api/walks/{walk_id}")
def delete_walk(request: Request, walk_id: int):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    with connect() as conn:
        cur = conn.execute("DELETE FROM walks WHERE id = ? AND user_id = ?", (walk_id, user["id"]))
        if cur.rowcount == 0:
            return _error("記録が見つかりません。", 404)
    return {"ok": True}


# ---------- 画面 ----------

@router.get("/walks")
def walks_page(request: Request):
    user = _user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "walks.html", {"user": user})


@router.get("/walks/{walk_id}")
def walk_detail_page(request: Request, walk_id: int):
    user = _user(request)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "walk_detail.html", {"user": user, "walk_id": walk_id})
