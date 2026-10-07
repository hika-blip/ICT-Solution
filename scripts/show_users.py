"""DBに登録されているユーザを一覧表示する（読み取り専用）。

実行方法（リポジトリのルートで、仮想環境を有効にしてから）:
    python scripts\show_users.py
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "app.db"
JST = timezone(timedelta(hours=9))


def to_jst(iso_text: str) -> str:
    """UTCで保存された日時を日本時間の文字列にする。"""
    return datetime.fromisoformat(iso_text).astimezone(JST).strftime("%Y-%m-%d %H:%M")


if not DB_PATH.exists():
    print("データベースがありません。アプリを起動してユーザを登録してください。")
    raise SystemExit

# 読み取り専用で開く（アプリ起動中でも安全）
conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

users = conn.execute(
    "SELECT id, name, name_kana, created_at FROM users ORDER BY id"
).fetchall()

print(f"登録ユーザ数：{len(users)}人\n")
print(f"{'ID':<8}{'登録日時':<18}{'氏名':<14}フリガナ")
print("-" * 60)
for u in users:
    print(f"{u['id']:<8}{to_jst(u['created_at']):<18}{u['name']:<14}{u['name_kana']}")

# 現在ログイン中（30日以内にログインし、ログアウトしていない）のユーザ
sessions = conn.execute(
    """SELECT users.id, users.name, sessions.created_at, sessions.expires_at
       FROM sessions JOIN users ON users.id = sessions.user_id
       ORDER BY sessions.created_at DESC"""
).fetchall()

print(f"\nログイン中：{len(sessions)}件\n")
for s in sessions:
    print(f"{s['id']}  {s['name']}  ログイン：{to_jst(s['created_at'])}  有効期限：{to_jst(s['expires_at'])}")

conn.close()