"""地図のピン（情報共有）API。

  GET    /api/pins            表示範囲内のピン一覧（tag で絞り込み可）
  POST   /api/pins            ピンを投稿（本文・写真のどちらか一方以上が必須）
  DELETE /api/pins/{id}       自分のピンを削除
  GET    /api/pins/{id}/photo 写真（ログイン必須）

写真は data/uploads/ に保存する。向きを補正し、縮小して JPEG にし直すので、
EXIF（撮影位置など）は残らない。
"""
import io
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel

from . import auth
from .db import BASE_DIR, connect

router = APIRouter()

SESSION_COOKIE = "session"   # main.py と同じ名前
UPLOAD_DIR = BASE_DIR / "data" / "uploads"

# タグ（表記ゆれを防ぐため固定リスト）。フロントにも /api/pin-tags で渡す
TAGS = ["景色", "自販機", "休憩場所", "避難場所", "トイレ", "危険箇所", "通行止め", "その他"]
MAX_BODY = 500
MAX_PHOTO_BYTES = 5 * 1024 * 1024
MAX_PHOTO_PIXELS = 50_000_000
PHOTO_LONG_SIDE = 1280
MAX_PINS = 500  # 1回の取得上限


def _user(request: Request):
    return auth.get_user_by_session(request.cookies.get(SESSION_COOKIE))


def _error(message: str, status: int):
    return JSONResponse({"error": message}, status_code=status)


def _save_photo(raw: bytes) -> Optional[str]:
    """画像を検証・縮小して保存し、ファイル名を返す。画像でなければ None。"""
    try:
        with Image.open(io.BytesIO(raw)) as img:
            if img.format not in ("JPEG", "PNG", "WEBP"):
                return None
            if img.width * img.height > MAX_PHOTO_PIXELS:
                return None
            img = ImageOps.exif_transpose(img)
            img.thumbnail((PHOTO_LONG_SIDE, PHOTO_LONG_SIDE))
            if img.mode != "RGB":
                img = img.convert("RGB")
            UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            name = uuid.uuid4().hex + ".jpg"
            img.save(UPLOAD_DIR / name, "JPEG", quality=85)
            return name
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return None


def _delete_photo(name: Optional[str]) -> None:
    if name:
        (UPLOAD_DIR / name).unlink(missing_ok=True)


def _row_to_dict(row, tags, me_id: int) -> dict:
    return {
        "id": row["id"],
        "lat": row["lat"],
        "lon": row["lon"],
        "body": row["body"],
        "has_photo": row["photo_file"] is not None,
        "tags": tags,
        "author_id": row["user_id"],
        "created_at": row["created_at"],
        "is_public": bool(row["is_public"]),
        "mine": row["user_id"] == me_id,
    }


@router.get("/api/pin-tags")
def pin_tags():
    return TAGS


@router.get("/api/pins")
def list_pins(request: Request, south: float, west: float, north: float, east: float, tag: str = ""):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    sql = (
        "SELECT * FROM pins WHERE lat BETWEEN ? AND ? AND lon BETWEEN ? AND ? "
        "AND (is_public = 1 OR user_id = ?) "
    )
    args: list = [south, north, west, east, user["id"]]
    if tag:
        sql += "AND id IN (SELECT pin_id FROM pin_tags WHERE tag = ?) "
        args.append(tag)
    sql += "ORDER BY created_at DESC LIMIT ?"
    args.append(MAX_PINS)
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        tag_map: dict = {}
        if rows:
            marks = ",".join("?" * len(rows))
            for t in conn.execute(
                f"SELECT pin_id, tag FROM pin_tags WHERE pin_id IN ({marks})", [r["id"] for r in rows]
            ):
                tag_map.setdefault(t["pin_id"], []).append(t["tag"])
    return [_row_to_dict(r, tag_map.get(r["id"], []), user["id"]) for r in rows]


@router.post("/api/pins")
async def create_pin(
    request: Request,
    lat: float = Form(...),
    lon: float = Form(...),
    body: str = Form(""),
    tags: List[str] = Form([]),
    is_public: bool = Form(True),
    photo: Optional[UploadFile] = File(None),
):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return _error("位置が正しくありません。", 400)
    body = body.strip()
    if len(body) > MAX_BODY:
        return _error(f"本文は{MAX_BODY}文字以内にしてください。", 400)
    tags = [t for t in dict.fromkeys(tags) if t in TAGS]

    raw = b""
    if photo is not None and photo.filename:
        raw = await photo.read(MAX_PHOTO_BYTES + 1)
        if len(raw) > MAX_PHOTO_BYTES:
            return _error("写真は5MB以下にしてください。", 400)
    if not body and not raw:
        return _error("本文か写真のどちらかは入力してください。", 400)
    photo_file = None
    if raw:
        photo_file = _save_photo(raw)
        if photo_file is None:
            return _error("写真はJPEG・PNG・WebPの画像を選んでください。", 400)

    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        with connect() as conn:
            cur = conn.execute(
                "INSERT INTO pins (user_id, lat, lon, body, photo_file, is_public, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user["id"], lat, lon, body, photo_file, int(is_public), created_at),
            )
            pin_id = cur.lastrowid
            conn.executemany("INSERT INTO pin_tags (pin_id, tag) VALUES (?, ?)", [(pin_id, t) for t in tags])
            row = conn.execute("SELECT * FROM pins WHERE id = ?", (pin_id,)).fetchone()
    except Exception:
        _delete_photo(photo_file)
        raise
    return _row_to_dict(row, tags, user["id"])


class Visibility(BaseModel):
    is_public: bool


@router.patch("/api/pins/{pin_id}/visibility")
def set_visibility(request: Request, pin_id: int, data: Visibility):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    with connect() as conn:
        cur = conn.execute(
            "UPDATE pins SET is_public = ? WHERE id = ? AND user_id = ?",
            (int(data.is_public), pin_id, user["id"]),
        )
        if cur.rowcount == 0:
            return _error("自分のピンが見つかりません。", 404)
    return {"ok": True, "is_public": data.is_public}


@router.delete("/api/pins/{pin_id}")
def delete_pin(request: Request, pin_id: int):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    with connect() as conn:
        row = conn.execute("SELECT user_id, photo_file FROM pins WHERE id = ?", (pin_id,)).fetchone()
        if row is None:
            return _error("ピンが見つかりません。", 404)
        if row["user_id"] != user["id"]:
            return _error("自分のピンだけ削除できます。", 403)
        conn.execute("DELETE FROM pins WHERE id = ?", (pin_id,))
    _delete_photo(row["photo_file"])
    return {"ok": True}


@router.get("/api/pins/{pin_id}/photo")
def pin_photo(request: Request, pin_id: int):
    user = _user(request)
    if user is None:
        return _error("ログインが必要です", 401)
    with connect() as conn:
        row = conn.execute(
            "SELECT photo_file FROM pins WHERE id = ? AND photo_file IS NOT NULL "
            "AND (is_public = 1 OR user_id = ?)",
            (pin_id, user["id"]),
        ).fetchone()
    if row is None or not (UPLOAD_DIR / row["photo_file"]).is_file():
        return _error("写真が見つかりません。", 404)
    return FileResponse(UPLOAD_DIR / row["photo_file"], media_type="image/jpeg")
