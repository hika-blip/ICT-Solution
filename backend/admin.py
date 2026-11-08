"""自治体向けの管理画面（管理者アカウント）。

住民のアカウントとは別に「管理者」のアカウントを持ち、/admin から入る。
管理者は画面からは作れない。最初の1人はコマンドで作る:
    python scripts/create_admin.py

画面:
  GET  /admin            管理画面（施設・避難先・ピン・CSV）
  GET  /admin/login      ログイン画面
  POST /admin/login      ログイン
  POST /admin/logout     ログアウト

API（どれも管理者のログインが必要）:
  GET    /admin/api/facilities             施設の一覧（非公開も含む。入口・敷地つき）
  POST   /admin/api/facilities             施設を追加（入口もまとめて送る）
  PUT    /admin/api/facilities/{id}        施設を更新（入口は送った内容で入れ替える）
  DELETE /admin/api/facilities/{id}        施設を削除（入口・敷地も消える）
  GET    /admin/api/evac-sites             避難先の一覧（非公開も含む）
  POST   /admin/api/evac-sites             避難先を追加
  PUT    /admin/api/evac-sites/{id}        避難先を更新
  DELETE /admin/api/evac-sites/{id}        避難先を削除
  GET    /admin/api/pins                   住民が投稿したピンの一覧（公開ピンだけ。非公開ピンは見せない）
  GET    /admin/api/pins/{id}/photo        ピンの写真（公開ピンだけ）
  DELETE /admin/api/pins/{id}              ピンを削除（写真も消す）
  GET    /admin/api/csv                    CSV と DB の件数
  POST   /admin/api/csv/export             DB の施設・避難先を seeds/ の CSV に書き出す
  POST   /admin/api/csv/import             seeds/ の CSV で DB の施設・避難先を入れ替える

管理者のログインの Cookie は SameSite=Strict にし、ほかのサイトから管理画面の操作を
送らせることができないようにしている。
"""
import hashlib
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Form, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import auth, evacuate, facilities, pins, routing
from .db import connect

router = APIRouter()
templates = Jinja2Templates(directory=Path(__file__).resolve().parent.parent / "frontend" / "templates")

ADMIN_COOKIE = "admin_session"
SESSION_HOURS = 12
COOKIE_SECURE = os.getenv("COOKIE_SECURE") == "1"   # main.py と同じ
MAX_NAME = 100
MAX_TEXT = 500


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _error(message: str, status: int = 400):
    return JSONResponse({"error": message}, status_code=status)


# ---------- テーブル・管理者アカウント ----------

def ensure_tables() -> None:
    """起動時に呼ぶ。管理者とそのログイン状態のテーブルを作る。"""
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                login_id      TEXT NOT NULL UNIQUE,   -- ログインに使うID（英数字）
                name          TEXT NOT NULL,          -- 表示名（例：日立市 防災課）
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token_hash TEXT PRIMARY KEY,
                admin_id   INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
                expires_at TEXT NOT NULL
            );
            """
        )


LOGIN_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,32}$")
ADMIN_PASSWORD_PATTERN = re.compile(r"^.{8,64}$")


def create_admin(login_id: str, name: str, password: str) -> int:
    """管理者を作る（scripts/create_admin.py から使う）。作った管理者の番号を返す。"""
    if not LOGIN_ID_PATTERN.match(login_id):
        raise ValueError("ログインIDは半角英数字（- と _ も可）で3〜32文字にしてください")
    if not name.strip():
        raise ValueError("表示名を入力してください")
    if not ADMIN_PASSWORD_PATTERN.match(password):
        raise ValueError("パスワードは8文字以上にしてください")
    ensure_tables()
    with connect() as conn:
        if conn.execute("SELECT 1 FROM admins WHERE login_id = ?", (login_id,)).fetchone():
            raise ValueError(f"ログインID '{login_id}' はすでに使われています")
        cur = conn.execute(
            "INSERT INTO admins (login_id, name, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (login_id, name.strip(), auth.hash_password(password), _now().isoformat()),
        )
        return cur.lastrowid


def _current_admin(request: Request):
    token = request.cookies.get(ADMIN_COOKIE)
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT admins.*, admin_sessions.expires_at FROM admin_sessions
               JOIN admins ON admins.id = admin_sessions.admin_id
               WHERE admin_sessions.token_hash = ?""",
            (_hash_token(token),),
        ).fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) < _now():
            conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (_hash_token(token),))
            return None
    return row


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# ---------- 画面 ----------

@router.get("/admin")
def admin_page(request: Request):
    admin = _current_admin(request)
    if admin is None:
        return _redirect("/admin/login")
    return templates.TemplateResponse(
        request, "admin.html",
        {"admin": admin, "categories": facilities.CATEGORIES, "kinds": evacuate.KINDS, "tags": pins.TAGS,
         "area": routing.bounds()},
    )


@router.get("/admin/login")
def admin_login_page(request: Request):
    if _current_admin(request):
        return _redirect("/admin")
    return templates.TemplateResponse(request, "admin_login.html", {"login_id": "", "error": None})


@router.post("/admin/login")
def admin_login(request: Request, login_id: str = Form(""), password: str = Form("")):
    with connect() as conn:
        admin = conn.execute("SELECT * FROM admins WHERE login_id = ?", (login_id.strip(),)).fetchone()
    if admin is None or not auth.verify_password(password, admin["password_hash"]):
        return templates.TemplateResponse(
            request, "admin_login.html",
            {"login_id": login_id, "error": "ログインIDまたはパスワードが正しくありません。"},
            status_code=400,
        )
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute(
            "INSERT INTO admin_sessions (token_hash, admin_id, expires_at) VALUES (?, ?, ?)",
            (_hash_token(token), admin["id"], (_now() + timedelta(hours=SESSION_HOURS)).isoformat()),
        )
    response = _redirect("/admin")
    response.set_cookie(
        ADMIN_COOKIE, token, max_age=SESSION_HOURS * 3600,
        httponly=True, samesite="strict", secure=COOKIE_SECURE, path="/admin",
    )
    return response


@router.post("/admin/logout")
def admin_logout(request: Request):
    token = request.cookies.get(ADMIN_COOKIE)
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM admin_sessions WHERE token_hash = ?", (_hash_token(token),))
    response = _redirect("/admin/login")
    response.delete_cookie(ADMIN_COOKIE, path="/admin")
    return response


# ---------- 入力チェック（施設・避難先で共通） ----------

def _check_point(lat: float, lon: float, label: str) -> Optional[str]:
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return f"{label}の緯度・経度が正しくありません。"
    area = routing.bounds()  # 地図データの範囲（地図データが無いときは None）
    if area and not (area["south"] <= lat <= area["north"] and area["west"] <= lon <= area["east"]):
        return f"{label}が地図データの範囲（青い枠）の外です。"
    return None


def _check_text(value: str, label: str, required: bool, limit: int = MAX_NAME) -> Optional[str]:
    if required and not value.strip():
        return f"{label}を入力してください。"
    if len(value) > limit:
        return f"{label}は{limit}文字以内にしてください。"
    return None


# ---------- 施設 ----------

class Entrance(BaseModel):
    name: str
    lat: float
    lon: float
    note: str = ""


class FacilityIn(BaseModel):
    name: str
    category: str
    lat: float
    lon: float
    description: str = ""
    hidden: bool = False
    entrances: List[Entrance] = []


def _validate_facility(data: FacilityIn) -> Optional[str]:
    for err in (
        _check_text(data.name, "施設名", True),
        None if data.category in facilities.CATEGORIES else "種類を選んでください。",
        _check_point(data.lat, data.lon, "ピンの位置"),
        _check_text(data.description, "説明", False, MAX_TEXT),
    ):
        if err:
            return err
    for i, e in enumerate(data.entrances, start=1):
        err = (_check_text(e.name, f"入口{i}の名前", True)
               or _check_point(e.lat, e.lon, f"入口「{e.name}」")
               or _check_text(e.note, f"入口「{e.name}」のメモ", False, MAX_TEXT))
        if err:
            return err
    return None


def _facility_list(conn) -> list:
    rows = conn.execute("SELECT * FROM facilities ORDER BY category, name").fetchall()
    ents: dict = {}
    for e in conn.execute("SELECT * FROM facility_entrances ORDER BY id"):
        ents.setdefault(e["facility_id"], []).append(
            {"name": e["name"], "lat": e["lat"], "lon": e["lon"], "note": e["note"]})
    areas: dict = {}
    for p in conn.execute("SELECT * FROM facility_area_points ORDER BY facility_id, seq"):
        areas.setdefault(p["facility_id"], []).append([p["lat"], p["lon"]])
    return [
        {"id": r["id"], "name": r["name"], "category": r["category"], "lat": r["lat"], "lon": r["lon"],
         "description": r["description"], "hidden": bool(r["hidden"]),
         "entrances": ents.get(r["id"], []), "area": areas.get(r["id"], [])}
        for r in rows
    ]


def _new_facility_id(conn) -> str:
    """管理画面で追加した施設のID（facility-1, facility-2, ...）。"""
    used = {r[0] for r in conn.execute("SELECT id FROM facilities")}
    n = 1
    while f"facility-{n}" in used:
        n += 1
    return f"facility-{n}"


def _save_entrances(conn, facility_id: str, entrances: List[Entrance]) -> None:
    conn.execute("DELETE FROM facility_entrances WHERE facility_id = ?", (facility_id,))
    conn.executemany(
        "INSERT INTO facility_entrances (facility_id, name, lat, lon, note) VALUES (?, ?, ?, ?, ?)",
        [(facility_id, e.name.strip(), e.lat, e.lon, e.note.strip()) for e in entrances],
    )


@router.get("/admin/api/facilities")
def admin_list_facilities(request: Request):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    with connect() as conn:
        return _facility_list(conn)


@router.post("/admin/api/facilities")
def admin_create_facility(request: Request, data: FacilityIn):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    err = _validate_facility(data)
    if err:
        return _error(err)
    with connect() as conn:
        fid = _new_facility_id(conn)
        conn.execute(
            "INSERT INTO facilities (id, name, category, lat, lon, description, hidden) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fid, data.name.strip(), data.category, data.lat, data.lon, data.description.strip(), int(data.hidden)),
        )
        _save_entrances(conn, fid, data.entrances)
    facilities.reload_areas()
    return {"ok": True, "id": fid}


@router.put("/admin/api/facilities/{facility_id}")
def admin_update_facility(request: Request, facility_id: str, data: FacilityIn):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    err = _validate_facility(data)
    if err:
        return _error(err)
    with connect() as conn:
        cur = conn.execute(
            "UPDATE facilities SET name = ?, category = ?, lat = ?, lon = ?, description = ?, hidden = ? WHERE id = ?",
            (data.name.strip(), data.category, data.lat, data.lon, data.description.strip(), int(data.hidden),
             facility_id),
        )
        if cur.rowcount == 0:
            return _error("施設が見つかりません。", 404)
        _save_entrances(conn, facility_id, data.entrances)
    facilities.reload_areas()
    return {"ok": True, "id": facility_id}


@router.delete("/admin/api/facilities/{facility_id}")
def admin_delete_facility(request: Request, facility_id: str):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    with connect() as conn:
        cur = conn.execute("DELETE FROM facilities WHERE id = ?", (facility_id,))
        if cur.rowcount == 0:
            return _error("施設が見つかりません。", 404)
    facilities.reload_areas()
    return {"ok": True}


# ---------- 避難先 ----------

class EvacSiteIn(BaseModel):
    name: str
    address: str = ""
    lat: float
    lon: float
    kinds: List[str]
    hidden: bool = False


def _validate_site(data: EvacSiteIn) -> Optional[str]:
    if not any(k in evacuate.KINDS for k in data.kinds):
        return "種別（避難場所・避難所）を1つ以上選んでください。"
    return (_check_text(data.name, "名称", True)
            or _check_text(data.address, "住所", False)
            or _check_point(data.lat, data.lon, "位置"))


def _site_values(data: EvacSiteIn) -> tuple:
    return (data.name.strip(), data.address.strip(), data.lat, data.lon,
            int("避難場所" in data.kinds), int("避難所" in data.kinds), int(data.hidden))


@router.get("/admin/api/evac-sites")
def admin_list_sites(request: Request):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    with connect() as conn:
        rows = conn.execute("SELECT * FROM evacuation_sites ORDER BY id").fetchall()
    return [
        {"id": r["id"], "name": r["name"], "address": r["address"], "lat": r["lat"], "lon": r["lon"],
         "kinds": [k for k, col in (("避難場所", "is_place"), ("避難所", "is_shelter")) if r[col]],
         "hidden": bool(r["hidden"])}
        for r in rows
    ]


@router.post("/admin/api/evac-sites")
def admin_create_site(request: Request, data: EvacSiteIn):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    err = _validate_site(data)
    if err:
        return _error(err)
    with connect() as conn:
        cur = conn.execute(
            """INSERT INTO evacuation_sites (name, address, lat, lon, is_place, is_shelter, hidden)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            _site_values(data),
        )
        site_id = cur.lastrowid
    evacuate.reload_sites()
    return {"ok": True, "id": site_id}


@router.put("/admin/api/evac-sites/{site_id}")
def admin_update_site(request: Request, site_id: int, data: EvacSiteIn):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    err = _validate_site(data)
    if err:
        return _error(err)
    with connect() as conn:
        cur = conn.execute(
            """UPDATE evacuation_sites SET name = ?, address = ?, lat = ?, lon = ?,
               is_place = ?, is_shelter = ?, hidden = ? WHERE id = ?""",
            _site_values(data) + (site_id,),
        )
        if cur.rowcount == 0:
            return _error("避難先が見つかりません。", 404)
    evacuate.reload_sites()
    return {"ok": True, "id": site_id}


@router.delete("/admin/api/evac-sites/{site_id}")
def admin_delete_site(request: Request, site_id: int):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    with connect() as conn:
        cur = conn.execute("DELETE FROM evacuation_sites WHERE id = ?", (site_id,))
        if cur.rowcount == 0:
            return _error("避難先が見つかりません。", 404)
    evacuate.reload_sites()
    return {"ok": True}


# ---------- 住民のピン（公開ピンだけを扱う。非公開ピンは管理者にも見せない） ----------

@router.get("/admin/api/pins")
def admin_list_pins(request: Request, tag: str = ""):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    sql = ("SELECT pins.*, users.name AS author_name FROM pins "
           "JOIN users ON users.id = pins.user_id WHERE pins.is_public = 1 ")
    args: list = []
    if tag:
        sql += "AND pins.id IN (SELECT pin_id FROM pin_tags WHERE tag = ?) "
        args.append(tag)
    sql += "ORDER BY pins.created_at DESC"
    with connect() as conn:
        rows = conn.execute(sql, args).fetchall()
        tag_map: dict = {}
        for t in conn.execute("SELECT pin_id, tag FROM pin_tags"):
            tag_map.setdefault(t["pin_id"], []).append(t["tag"])
    return [
        {"id": r["id"], "lat": r["lat"], "lon": r["lon"], "body": r["body"],
         "has_photo": r["photo_file"] is not None, "tags": tag_map.get(r["id"], []),
         "author_id": r["user_id"], "author_name": r["author_name"], "created_at": r["created_at"]}
        for r in rows
    ]


@router.get("/admin/api/pins/{pin_id}/photo")
def admin_pin_photo(request: Request, pin_id: int):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    with connect() as conn:
        row = conn.execute(
            "SELECT photo_file FROM pins WHERE id = ? AND is_public = 1 AND photo_file IS NOT NULL", (pin_id,)
        ).fetchone()
    if row is None or not (pins.UPLOAD_DIR / row["photo_file"]).is_file():
        return _error("写真が見つかりません。", 404)
    return FileResponse(pins.UPLOAD_DIR / row["photo_file"], media_type="image/jpeg")


@router.delete("/admin/api/pins/{pin_id}")
def admin_delete_pin(request: Request, pin_id: int):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    with connect() as conn:
        row = conn.execute("SELECT photo_file FROM pins WHERE id = ? AND is_public = 1", (pin_id,)).fetchone()
        if row is None:
            return _error("ピンが見つかりません。", 404)
        conn.execute("DELETE FROM pins WHERE id = ?", (pin_id,))
    pins._delete_photo(row["photo_file"])
    return {"ok": True}


# ---------- CSV の書き出し・読み込み直し ----------

@router.get("/admin/api/csv")
def admin_csv_status(request: Request):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    with connect() as conn:
        counts = {
            "facilities": conn.execute("SELECT COUNT(*) FROM facilities").fetchone()[0],
            "entrances": conn.execute("SELECT COUNT(*) FROM facility_entrances").fetchone()[0],
            "evac_sites": conn.execute("SELECT COUNT(*) FROM evacuation_sites").fetchone()[0],
        }
    return {"db": counts}


@router.post("/admin/api/csv/export")
def admin_csv_export(request: Request):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    result = facilities.export_to_csv()
    result["evac_sites"] = evacuate.export_to_csv()
    return {"ok": True, **result}


@router.post("/admin/api/csv/import")
def admin_csv_import(request: Request):
    if _current_admin(request) is None:
        return _error("管理者のログインが必要です", 401)
    try:
        result = facilities.import_from_csv()
        result["evac_sites"] = evacuate.import_from_csv()
    except ValueError as e:
        return _error(f"CSVを読み込めませんでした：{e}")
    return {"ok": True, **result}
