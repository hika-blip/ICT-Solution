"""負荷テスト用のユーザを、負荷テスト用のDBに作る。ID とパスワードは loadtest/users.json に保存する。

本物のDB（data/app.db）を汚さないよう、環境変数 APP_DB_PATH を指定したときだけ動く。
リポジトリのルートで、仮想環境を有効にしてから実行する（コマンドプロンプト）:
    copy data\\app.db data\\loadtest.db
    set APP_DB_PATH=data\\loadtest.db
    python loadtest/setup_users.py --count 10
"""
import argparse
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import auth  # noqa: E402
from backend.db import DB_PATH, init_db  # noqa: E402

OUT_PATH = Path(__file__).resolve().parent / "users.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10, help="作るユーザの数")
    args = parser.parse_args()

    if not os.environ.get("APP_DB_PATH") or DB_PATH.resolve().name == "app.db":
        sys.exit("APP_DB_PATH に負荷テスト用のDB（例: data\\loadtest.db）を指定してください。")

    init_db()
    users = []
    for i in range(args.count):
        password = secrets.token_urlsafe(12)
        user_id = auth.create_user(f"負荷テスト{i + 1}", "ふかてすと", password)
        users.append({"user_id": str(user_id), "password": password})
    OUT_PATH.write_text(json.dumps(users, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{DB_PATH} に {len(users)} 人作りました。ID とパスワード: {OUT_PATH}")


if __name__ == "__main__":
    main()
