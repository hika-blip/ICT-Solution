"""管理者アカウントを作る。

管理画面（/admin）に入るためのアカウントは、住民のアカウントとは別で、画面からは作れない。
リポジトリのルートで、仮想環境を有効にしてから実行する:
    python scripts/create_admin.py
ログインID・表示名・パスワードを順に聞かれる（パスワードは入力しても表示されない）。
"""
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import admin  # noqa: E402
from backend.db import init_db  # noqa: E402


def main() -> None:
    init_db()
    print("管理者アカウントを作成します。")
    login_id = input("ログインID（半角英数字 3〜32文字）: ").strip()
    name = input("表示名（例：日立市 防災課）: ").strip()
    password = getpass.getpass("パスワード（8文字以上）: ")
    if password != getpass.getpass("パスワード（確認）: "):
        sys.exit("パスワードが一致しません。もう一度やり直してください。")
    try:
        admin.create_admin(login_id, name, password)
    except ValueError as e:
        sys.exit(f"作成できませんでした：{e}")
    print(f"作成しました。http://localhost:8000/admin から、ログインID「{login_id}」でログインできます。")


if __name__ == "__main__":
    main()
