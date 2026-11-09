"""危険個所の投稿API。

  GET    /api/hazard-tags        危険の種類（タグ）の一覧
  GET    /api/hazards            表示範囲内の危険個所一覧
  POST   /api/hazards            危険個所を投稿（位置・通行可否が必須。写真は1枚まで・任意）
  DELETE /api/hazards/{id}       自分の投稿を削除
  GET    /api/hazards/{id}/photo 写真（ログイン必須）

通行可否（status）:
  passable = 歩いて通れる（注意）
  blocked  = 通行不可
後で避難経路の探索で「blocked は通らない、passable は避ける」に使う想定。
写真の保存・縮小は pins.py の処理を使い回す（data/uploads/ に保存）。
"""
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

from . import auth
from . import routing
from .db import connect
from .pins import MAX_PHOTO_BYTES, UPLOAD_DIR, _delete_photo, _save_photo

router = APIRouter()

SESSION_COOKIE = "session"   # main.py と同じ名前
STATUSES = ("passable", "blocked")
# 危険の種類（表記ゆれを防ぐため固定リスト）。フロントにも /api/hazard-tags で渡す
TAGS = ["冠水", "倒木", "崩落・土砂", "段差・陥没", "停電(信号・照明)", "障害物", "その他"]
MAX_HAZARDS = 500  # 1回の取得上限


def _user(request: Request):
    return auth.get_user_by_session(request.cookies.get(SESSION_COOKIE))


def _error(message: str, status: int):
    return JSONResponse({"error": message}, status_code=status)


def _row_to_dict(row, tags, me_id: int) -> dict:
    return {
        "id": row["id"],
        "lat": row["lat"],
        "lon": row["lon"],
        "status": row["status"],
        "tags": tags,
        "has_photo": row["photo_file"] is not None,
        "author_id": row["user_id"],
        "created_at": row["created_at"],
        "mine": row["user_id"] == me_id,
    }


@router.get("/api/hazard-tags")
def hazard_tags():
    return TAGS


@router.get("/api/hazards/blocked-edges")
def blocked_edges(request: Request):
    """通行不可の危険個所のせいで通れない道（緊急時の地図に赤線と×で示す）。"""
    if _user(request) is None:
        return _error("ログインが必要です", 401)
    return routing.blocked_edges()


@router.get("/api/hazards")
def list_hazards(request: Request, south: float, west: float, north: float, east: float):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM hazards WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
            "ORDER BY created_at DESC LIMIT ?",
            (south, north, west, east, MAX_HAZARDS),
        ).fetchall()
        tag_map: dict = {}
        if rows:
            marks = ",".join("?" * len(rows))
            for t in conn.execute(
                f"SELECT hazard_id, tag FROM hazard_tags WHERE hazard_id IN ({marks})", [r["id"] for r in rows]
            ):
                tag_map.setdefault(t["hazard_id"], []).append(t["tag"])
    return [_row_to_dict(r, tag_map.get(r["id"], []), user["id"]) for r in rows]


@router.post("/api/hazards")
async def create_hazard(
    request: Request,
    lat: float = Form(...),
    lon: float = Form(...),
    status: str = Form(...),
    tags: List[str] = Form([]),
    photo: Optional[UploadFile] = File(None),
):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return _error("位置が正しくありません。", 400)
    if status not in STATUSES:
        return _error("通行可否を選んでください。", 400)
    tags = [t for t in dict.fromkeys(tags) if t in TAGS]
    photo_file = None
    if photo is not None and photo.filename:
        raw = await photo.read(MAX_PHOTO_BYTES + 1)
        if len(raw) > MAX_PHOTO_BYTES:
            return _error("写真は5MB以下にしてください。", 400)
        photo_file = _save_photo(raw)
        if photo_file is None:
            return _error("写真はJPEG・PNG・WebPの画像を選んでください。", 400)

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO hazards (user_id, lat, lon, status, photo_file, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (user["id"], lat, lon, status, photo_file, created_at),
            )
            conn.executemany(
                "INSERT INTO hazard_tags (hazard_id, tag) VALUES (?, ?)", [(cur.lastrowid, t) for t in tags]
            )
            row = conn.execute("SELECT * FROM hazards WHERE id = ?", (cur.lastrowid,)).fetchone()
    except Exception:
        _delete_photo(photo_file)
        raise
    return _row_to_dict(row, tags, user["id"])


@router.delete("/api/hazards/{hazard_id}")
def delete_hazard(request: Request, hazard_id: int):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    with connect() as conn:
        row = conn.execute("SELECT user_id, photo_file FROM hazards WHERE id = ?", (hazard_id,)).fetchone()
        if row is None:
            return _error("投稿が見つかりません。", 404)
        if row["user_id"] != user["id"]:
            return _error("自分の投稿だけ削除できます。", 403)
        conn.execute("DELETE FROM hazards WHERE id = ?", (hazard_id,))
    _delete_photo(row["photo_file"])
    return {"ok": True}


@router.get("/api/hazards/{hazard_id}/photo")
def hazard_photo(request: Request, hazard_id: int):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    with connect() as conn:
        row = conn.execute("SELECT photo_file FROM hazards WHERE id = ?", (hazard_id,)).fetchone()
    if row is None or row["photo_file"] is None or not (UPLOAD_DIR / row["photo_file"]).is_file():
        return _error("写真が見つかりません。", 404)
    return FileResponse(UPLOAD_DIR / row["photo_file"], media_type="image/jpeg")
