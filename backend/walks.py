"""歩行ログ（歩いた道の記録）API と画面。

  POST   /api/walks        記録を保存（記録中の点をまとめて受け取る）
  GET    /api/walks        自分の記録一覧（新しい順）
  GET    /api/walks/speed  自分の歩く速さ [m/分]（記録が足りなければ標準の速さ）
  GET    /api/walks/{id}   記録のまとめと点の座標
  DELETE /api/walks/{id}   自分の記録を削除
  GET    /walks            一覧の画面
  GET    /walks/{id}       詳細（地図）の画面

記録は本人にだけ見える。
"""
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import auth
from .db import connect

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


def _summary(row) -> dict:
    moving = row["moving_s"]
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],
        "distance_m": row["distance_m"],
        "duration_s": row["duration_s"],
        "moving_s": moving,
        "speed_m_per_min": row["distance_m"] / moving * 60 if moving > 0 else None,
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

    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO walks (user_id, started_at, ended_at, distance_m, duration_s, moving_s) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (user["id"], _iso(points[0].t), _iso(points[-1].t), distance,
             (points[-1].t - points[0].t) / 1000, moving),
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
    """直近の記録から歩く速さ [m/分] を求める。記録が足りなければ標準の速さ。"""
    with connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n, SUM(distance_m) AS d, SUM(moving_s) AS s FROM ("
            "  SELECT distance_m, moving_s FROM walks"
            "  WHERE user_id = ? AND distance_m >= ? AND moving_s > 0"
            "  ORDER BY started_at DESC LIMIT ?)",
            (user_id, SPEED_MIN_DISTANCE, SPEED_RECENT),
        ).fetchone()
    if not row["n"]:
        return {"speed_m_per_min": DEFAULT_SPEED, "is_default": True, "walks": 0}
    return {"speed_m_per_min": row["d"] / row["s"] * 60, "is_default": False, "walks": row["n"]}


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
