# 負荷テスト（シナリオC：経路検索の限界測定）

経路検索APIを、同時ユーザ数を段階的に増やしながら叩き、**何人まで基準内で応答できるか**を測る。

- 基準：p95 応答時間 3 秒以内、エラー率 1% 以下（`plot.py` の `P95_LIMIT_MS` / `ERROR_LIMIT`）
- 人数：1 → 5 → 10 → 20 → 40 → 80 → 160 人、各 2 分（`--steps` / `--step-seconds` で変更可）
- 各ユーザは最初に1回だけログインし、「検索 → 1〜3 秒待つ」を繰り返す

| ユーザの種類 | 測るAPI | 中身 |
|---|---|---|
| `RouteUser` | `/api/route` | A* 探索 1 回 |
| `PlanUser` | `/api/route/plan` | 入口の組み合わせ × 時間優先・距離優先 |
| `EvacUser` | `/api/evacuate/routes` | 近い避難先それぞれへの経路 |

**本番や共有のサーバには絶対に負荷をかけないこと。** 自分のPCで起動したサーバに対してだけ行う。

## 準備（初回だけ）

コマンドプロンプトで、リポジトリのルートで実行する。`data\map.graphml` が作成済みであること。

```bat
.venv\Scripts\activate
pip install -r loadtest\requirements.txt

rem 本物のDBのコピーを負荷テスト用に使う
copy data\app.db data\loadtest.db
set APP_DB_PATH=data\loadtest.db

rem テスト用ユーザ（ID・パスワードは loadtest\users.json に保存。共有しない）
python loadtest\setup_users.py --count 10

rem 道の上の地点を200個抜き出す（loadtest\points.json）
python loadtest\make_points.py
```

## 実行

ウィンドウ1：サーバを起動する（`--reload` は付けない。本番に近い状態で測るため）

```bat
.venv\Scripts\activate
set APP_DB_PATH=data\loadtest.db
uvicorn backend.main:app --port 8000 --workers 1
```

ウィンドウ2：負荷をかける（1回 約14分。最後の名前を変えて3種類を測る）

```bat
.venv\Scripts\activate
locust -f loadtest\locustfile.py --headless -H http://127.0.0.1:8000 --csv loadtest\results\route_w1 --csv-full-history --only-summary RouteUser
```

画面でグラフを見ながら行いたいときは `--headless` と `--only-summary` を外し、http://localhost:8089 を開いて Start を押す。

## 結果をまとめる

```bat
python loadtest\plot.py loadtest\results\route_w1
```

人数ごとの RPS・p50・p95・エラー率の表と「基準内の最大人数」を表示し、`loadtest\results\route_w1_summary.png` にグラフを保存する。
人数を増やした直後の 30 秒は不安定なので除いて平均している。

### 比較（発表の考察用）

経路計算は CPU を使う処理なので、Python では 1 プロセスあたり実質 CPU 1 コア分しか使えない。
サーバを `--workers 4` で起動し直して同じテストを行い、重ねて比べる：

```bat
python loadtest\plot.py loadtest\results\route_w1 loadtest\results\route_w4
```

## 注意

- Locust も同じPCで動くため、CPU を取り合う。発表ではこの条件を明記する。
- テスト中はタスクマネージャーで CPU 使用率も見ておく（「1コアだけ100%」なら上の GIL の話の裏付けになる）。
- やり直すときは `data\loadtest.db` を消して、準備からやり直せばよい。
