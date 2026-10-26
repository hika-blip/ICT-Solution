"""データベース（SQLite）の接続とテーブル作成。

DBファイルは data/app.db に作られる（data/ は .gitignore 済みで共有されない）。
"""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "app.db"

# 最初に割り振るユーザID（6桁の連番）
FIRST_USER_ID = 100001


@contextmanager
def connect():
    """with connect() as conn: の形で使う。正常終了でコミット、例外でロールバック。"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # 列名で値を取り出せるようにする
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """テーブルがなければ作る。アプリ起動時に1回呼ばれる。"""
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT NOT NULL,   -- 氏名
                name_kana     TEXT NOT NULL,   -- フリガナ（カタカナ）
                password_hash TEXT NOT NULL,   -- ハッシュ化したパスワード
                created_at    TEXT NOT NULL    -- 登録日時（UTC, ISO形式）
            );

            -- ログイン状態（30日間保持）。トークンはハッシュ化して保存する
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            -- 地図に立てるピン（情報共有）。is_public=0 にすると本人にだけ見える
            CREATE TABLE IF NOT EXISTS pins (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                lat        REAL NOT NULL,
                lon        REAL NOT NULL,
                body       TEXT NOT NULL DEFAULT '',  -- 本文（写真だけなら空）
                photo_file TEXT,                      -- 写真のファイル名（なければNULL）
                is_public  INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL              -- 投稿日時（UTC, ISO形式）
            );
            CREATE INDEX IF NOT EXISTS idx_pins_latlon ON pins(lat, lon);

            CREATE TABLE IF NOT EXISTS pin_tags (
                pin_id INTEGER NOT NULL REFERENCES pins(id) ON DELETE CASCADE,
                tag    TEXT NOT NULL,
                PRIMARY KEY (pin_id, tag)
            );

            -- 歩行ログ（1回の記録）。距離・時間は保存時にサーバで計算する
            CREATE TABLE IF NOT EXISTS walks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                started_at TEXT NOT NULL,   -- 記録開始（UTC, ISO形式）
                ended_at   TEXT NOT NULL,   -- 記録終了（UTC, ISO形式）
                distance_m REAL NOT NULL,   -- 歩いた距離 [m]
                duration_s REAL NOT NULL,   -- 開始〜終了の時間 [秒]
                moving_s   REAL NOT NULL    -- 止まっていた時間を除いた時間 [秒]
            );
            CREATE INDEX IF NOT EXISTS idx_walks_user ON walks(user_id, started_at);

            -- 歩行ログの点（記録した位置）
            CREATE TABLE IF NOT EXISTS walk_points (
                walk_id     INTEGER NOT NULL REFERENCES walks(id) ON DELETE CASCADE,
                seq         INTEGER NOT NULL,  -- 記録した順番（0から）
                lat         REAL NOT NULL,
                lon         REAL NOT NULL,
                accuracy    REAL,              -- 誤差の目安 [m]
                recorded_at TEXT NOT NULL,     -- 記録した日時（UTC, ISO形式）
                PRIMARY KEY (walk_id, seq)
            );
            """
        )
        # IDを 100001 から始めるため、連番の開始値を設定する（初回のみ）
        row = conn.execute("SELECT seq FROM sqlite_sequence WHERE name = 'users'").fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO sqlite_sequence (name, seq) VALUES ('users', ?)",
                (FIRST_USER_ID - 1,),
            )
