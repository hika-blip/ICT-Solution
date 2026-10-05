"""登録・ログイン・ログイン状態（セッション）の処理。"""
import hashlib
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone

import bcrypt

from .db import connect

# ログイン状態を保つ日数
SESSION_DAYS = 30

# パスワード：半角英数字で2文字以上（プロトタイプ用。本番前に見直す）
PASSWORD_PATTERN = re.compile(r"^[A-Za-z0-9]{2,64}$")
PASSWORD_RULE_TEXT = "半角英数字で2文字以上"

# フリガナ：カタカナ・長音・スペースのみ
KANA_PATTERN = re.compile(r"^[ァ-ヶー　]+$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- 入力のチェック ----------

def normalize_kana(text: str) -> str:
    """半角カナ→全角、ひらがな→カタカナに直し、空白を全角スペース1つにそろえる。"""
    text = unicodedata.normalize("NFKC", text).strip()
    text = "".join(
        chr(ord(c) + 0x60) if "ぁ" <= c <= "ゖ" else c for c in text
    )
    return re.sub(r"\s+", "　", text)


def validate_registration(name: str, kana: str, password: str, password_confirm: str) -> list[str]:
    """登録内容をチェックし、エラーメッセージのリストを返す（空なら問題なし）。"""
    errors = []
    if not name:
        errors.append("氏名を入力してください。")
    elif len(name) > 50:
        errors.append("氏名は50文字以内で入力してください。")

    if not kana:
        errors.append("フリガナを入力してください。")
    elif not KANA_PATTERN.match(kana):
        errors.append("フリガナはカタカナで入力してください。")
    elif len(kana) > 100:
        errors.append("フリガナは100文字以内で入力してください。")

    if not PASSWORD_PATTERN.match(password):
        errors.append(f"パスワードは{PASSWORD_RULE_TEXT}で入力してください。")
    elif password != password_confirm:
        errors.append("確認用のパスワードが一致しません。")
    return errors


# ---------- パスワード ----------

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


# ---------- ユーザ ----------

def create_user(name: str, kana: str, password: str) -> int:
    """ユーザを登録し、割り振ったIDを返す。"""
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO users (name, name_kana, password_hash, created_at) VALUES (?, ?, ?, ?)",
            (name, kana, hash_password(password), _now().isoformat()),
        )
        return cur.lastrowid


def authenticate(user_id_text: str, password: str):
    """IDとパスワードが正しければユーザ情報を返す。違えば None。"""
    user_id_text = unicodedata.normalize("NFKC", user_id_text).strip()
    if not user_id_text.isdigit():
        return None
    with connect() as conn:
        user = conn.execute(
            "SELECT * FROM users WHERE id = ?", (int(user_id_text),)
        ).fetchone()
    if user is None or not verify_password(password, user["password_hash"]):
        return None
    return user


# ---------- セッション（ログイン状態） ----------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(user_id: int) -> str:
    """ログイン状態を作り、ブラウザのCookieに入れるトークンを返す。"""
    token = secrets.token_urlsafe(32)
    now = _now()
    with connect() as conn:
        conn.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (_hash_token(token), user_id, now.isoformat(),
             (now + timedelta(days=SESSION_DAYS)).isoformat()),
        )
    return token


def get_user_by_session(token: str | None):
    """Cookieのトークンからログイン中のユーザを返す。期限切れ・無効なら None。"""
    if not token:
        return None
    with connect() as conn:
        row = conn.execute(
            """SELECT users.*, sessions.expires_at FROM sessions
               JOIN users ON users.id = sessions.user_id
               WHERE sessions.token_hash = ?""",
            (_hash_token(token),),
        ).fetchone()
        if row is None:
            return None
        if datetime.fromisoformat(row["expires_at"]) < _now():
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
            return None
    return row


def delete_session(token: str | None) -> None:
    if token:
        with connect() as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
