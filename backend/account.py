"""登録情報の変更（氏名・フリガナ・パスワード）。

流れ:
  /account        パスワードで本人確認
  /account/edit   本人確認から10分以内なら、氏名・フリガナ・パスワードを変更できる

main.py に次の2行を追加すると有効になる:
    from .account import router as account_router
    app.include_router(account_router)
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from . import auth
from .db import connect

router = APIRouter()
templates = Jinja2Templates(
    directory=Path(__file__).resolve().parent.parent / "frontend" / "templates"
)

SESSION_COOKIE = "session"   # main.py と同じ名前
REAUTH_MINUTES = 10          # 本人確認の有効時間


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _ensure_table() -> None:
    """本人確認の記録用テーブル（なければ作る）。ログイン状態ごとに記録する。"""
    with connect() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS reauth (
                   session_token_hash TEXT PRIMARY KEY
                       REFERENCES sessions(token_hash) ON DELETE CASCADE,
                   expires_at TEXT NOT NULL
               )"""
        )


def _current(request: Request):
    """ログイン中のユーザと、そのログイン状態のトークン（ハッシュ）を返す。"""
    token = request.cookies.get(SESSION_COOKIE)
    user = auth.get_user_by_session(token)
    if user is None:
        return None, None
    return user, auth._hash_token(token)


def _is_reauthed(token_hash: str) -> bool:
    _ensure_table()
    with connect() as conn:
        row = conn.execute(
            "SELECT expires_at FROM reauth WHERE session_token_hash = ?", (token_hash,)
        ).fetchone()
    return row is not None and datetime.fromisoformat(row["expires_at"]) > _now()


def validate_profile(name: str, kana: str, new_password: str, new_password_confirm: str) -> list[str]:
    errors = []
    if not name:
        errors.append("氏名を入力してください。")
    elif len(name) > 50:
        errors.append("氏名は50文字以内で入力してください。")

    if not kana:
        errors.append("フリガナを入力してください。")
    elif not auth.KANA_PATTERN.match(kana):
        errors.append("フリガナはカタカナで入力してください。")
    elif len(kana) > 100:
        errors.append("フリガナは100文字以内で入力してください。")

    # パスワードは、どちらかの欄に入力があるときだけチェック（空なら変更しない）
    if new_password or new_password_confirm:
        if not auth.PASSWORD_PATTERN.match(new_password):
            errors.append(f"新しいパスワードは{auth.PASSWORD_RULE_TEXT}で入力してください。")
        elif new_password != new_password_confirm:
            errors.append("新しいパスワードが2回とも同じになるよう入力してください。")
    return errors


# ---------- ① 本人確認 ----------

@router.get("/account")
def verify_page(request: Request, expired: str = ""):
    user, _ = _current(request)
    if user is None:
        return _redirect("/login")
    error = "時間が経過したため、もう一度パスワードを入力してください。" if expired else None
    return templates.TemplateResponse(request, "account_verify.html", {"user": user, "error": error})


@router.post("/account")
def verify(request: Request, password: str = Form("")):
    user, token_hash = _current(request)
    if user is None:
        return _redirect("/login")
    if not auth.verify_password(password, user["password_hash"]):
        return templates.TemplateResponse(
            request, "account_verify.html",
            {"user": user, "error": "パスワードが正しくありません。"},
            status_code=400,
        )
    _ensure_table()
    with connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO reauth (session_token_hash, expires_at) VALUES (?, ?)",
            (token_hash, (_now() + timedelta(minutes=REAUTH_MINUTES)).isoformat()),
        )
    return _redirect("/account/edit")


# ---------- ② 変更 ----------

@router.get("/account/edit")
def edit_page(request: Request):
    user, token_hash = _current(request)
    if user is None:
        return _redirect("/login")
    if not _is_reauthed(token_hash):
        return _redirect("/account")
    return templates.TemplateResponse(
        request, "account_edit.html",
        {"user": user, "name": user["name"], "kana": user["name_kana"],
         "errors": [], "password_rule": auth.PASSWORD_RULE_TEXT},
    )


@router.post("/account/edit")
def edit(
    request: Request,
    name: str = Form(""),
    kana: str = Form(""),
    new_password: str = Form(""),
    new_password_confirm: str = Form(""),
):
    user, token_hash = _current(request)
    if user is None:
        return _redirect("/login")
    if not _is_reauthed(token_hash):
        return _redirect("/account?expired=1")

    name = name.strip()
    kana = auth.normalize_kana(kana)
    errors = validate_profile(name, kana, new_password, new_password_confirm)
    if errors:
        return templates.TemplateResponse(
            request, "account_edit.html",
            {"user": user, "name": name, "kana": kana,
             "errors": errors, "password_rule": auth.PASSWORD_RULE_TEXT},
            status_code=400,
        )

    with connect() as conn:
        conn.execute(
            "UPDATE users SET name = ?, name_kana = ? WHERE id = ?",
            (name, kana, user["id"]),
        )
        if new_password:
            conn.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (auth.hash_password(new_password), user["id"]),
            )
            # この端末以外のログイン状態を終了する
            conn.execute(
                "DELETE FROM sessions WHERE user_id = ? AND token_hash != ?",
                (user["id"], token_hash),
            )
        # 本人確認は1回の変更で使い切り
        conn.execute("DELETE FROM reauth WHERE session_token_hash = ?", (token_hash,))

    return _redirect("/home?updated=1")
