"""Webアプリ本体（FastAPI）。

起動方法（リポジトリのルートで、仮想環境を有効にしてから）:
    uvicorn backend.main:app --reload
ブラウザで http://localhost:8000 を開く。
"""
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth
from .db import init_db

BASE_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

SESSION_COOKIE = "session"
NEW_ID_COOKIE = "new_user_id"  # 登録完了画面にIDを渡すための一時的なCookie
# 本番（HTTPS）では環境変数 COOKIE_SECURE=1 にする
COOKIE_SECURE = os.getenv("COOKIE_SECURE") == "1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(title="ひたふら", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")
templates = Jinja2Templates(directory=FRONTEND_DIR / "templates")


def current_user(request: Request):
    return auth.get_user_by_session(request.cookies.get(SESSION_COOKIE))


def redirect(url: str) -> RedirectResponse:
    # 303: フォーム送信のあとに別ページへ移動させる（再読み込みでの二重送信を防ぐ）
    return RedirectResponse(url, status_code=303)


# ---------- トップ ----------

@app.get("/")
def index(request: Request):
    return redirect("/home" if current_user(request) else "/login")


# ---------- ログイン ----------

@app.get("/login")
def login_page(request: Request, user_id: str = ""):
    if current_user(request):
        return redirect("/home")
    return templates.TemplateResponse(request, "login.html", {"user_id": user_id, "error": None})


@app.post("/login")
def login(request: Request, user_id: str = Form(""), password: str = Form("")):
    user = auth.authenticate(user_id, password)
    if user is None:
        return templates.TemplateResponse(
            request, "login.html",
            {"user_id": user_id, "error": "IDまたはパスワードが正しくありません。"},
            status_code=400,
        )
    token = auth.create_session(user["id"])
    response = redirect("/home")
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=auth.SESSION_DAYS * 24 * 60 * 60,
        httponly=True, samesite="lax", secure=COOKIE_SECURE,
    )
    return response


@app.post("/logout")
def logout(request: Request):
    auth.delete_session(request.cookies.get(SESSION_COOKIE))
    response = redirect("/login")
    response.delete_cookie(SESSION_COOKIE)
    return response


# ---------- 新規登録 ----------

@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse(
        request, "register.html",
        {"name": "", "kana": "", "errors": [], "password_rule": auth.PASSWORD_RULE_TEXT},
    )


@app.post("/register")
def register(
    request: Request,
    name: str = Form(""),
    kana: str = Form(""),
    password: str = Form(""),
    password_confirm: str = Form(""),
):
    name = name.strip()
    kana = auth.normalize_kana(kana)
    errors = auth.validate_registration(name, kana, password, password_confirm)
    if errors:
        return templates.TemplateResponse(
            request, "register.html",
            {"name": name, "kana": kana, "errors": errors, "password_rule": auth.PASSWORD_RULE_TEXT},
            status_code=400,
        )
    new_id = auth.create_user(name, kana, password)
    response = redirect("/register/complete")
    response.set_cookie(NEW_ID_COOKIE, str(new_id), max_age=600, httponly=True, samesite="lax", secure=COOKIE_SECURE)
    return response


@app.get("/register/complete")
def register_complete(request: Request):
    new_id = request.cookies.get(NEW_ID_COOKIE)
    if not new_id:
        return redirect("/login")
    # 再読み込みしても表示できるよう、Cookieは期限（10分）まで残す
    return templates.TemplateResponse(request, "register_complete.html", {"user_id": new_id})


# ---------- ログイン後のホーム（今回は白紙） ----------

@app.get("/home")
def home(request: Request):
    user = current_user(request)
    if user is None:
        return redirect("/login")
    return templates.TemplateResponse(request, "home.html", {"user": user})
