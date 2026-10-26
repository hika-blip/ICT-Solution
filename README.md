# ひたふら

地理特性と歩行能力に応じた避難支援Webアプリ（日立市）

testで追加した行です。

こんにちは

## 起動方法（Windows / コマンドプロンプト）

### 前提
- Python 3.12 がインストールされていること
- このリポジトリを `C:\dev\ICT-Solution` に clone 済みであること

### 初回だけ：仮想環境の作成とライブラリのインストール
```bat
cd C:\dev\ICT-Solution
python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### 起動
```bat
cd C:\dev\ICT-Solution
.venv\Scripts\activate
uvicorn backend.main:app --reload
```
`Uvicorn running on http://127.0.0.1:8000` と表示されたら、ブラウザで http://localhost:8000 を開く。

- `--reload` を付けているので、コードを保存すると自動で再起動する
- 停止するときは `Ctrl + C`

### スマホ表示での確認
Chrome で `F12` → 左上のスマホのアイコン（デバイスツールバー）を押すと、スマホ幅で表示できる。

### 登録データのリセット
サーバを停止してから `data\app.db` を削除する。次の起動時に空の状態で作り直され、ユーザIDも 100001 から始まる。
（`data/` は `.gitignore` 済みのため、データベースは各自のPCにだけ存在する）

### 経路検索用の地図データの作成（初回だけ）
ログイン後のホームで経路検索するには、`data\map.graphml` が必要。`data/` は共有されないので、各自が次のコマンドで作る（日立駅〜常陸多賀駅の周辺、歩行用）。

```bat
python osm_to_network.py --bbox 140.620,36.542,140.674,36.601 --type walk --out data/map --no-map
```

- インターネット接続が必要。Overpass API が混雑して失敗したときは、少し待ってもう一度実行する
- 作ったあとはサーバを再起動する（地図データは起動時に1回だけ読み込む）
- ファイルが無くてもサーバは起動するが、経路検索だけ「地図データがありません」と表示される

### 施設（駅・学校など）のピンと入口の登録
地図に表示する施設と、経路のゴールにする入口は `seeds/` の CSV で管理する（GitHub で共有される）。
サーバ起動時に CSV の内容で DB が作り直されるので、CSV を編集したらサーバを再起動する。

- `seeds/facilities.csv`：施設（`id,name,category,lat,lon,description`）
  - `id` は英小文字・数字・ハイフンで、施設ごとに重複しない名前にする（例：`hitachi-station`）
  - `category` は `駅`・`学校`・`観光地`・`避難所` のどれか
  - `lat,lon` はピンを表示する位置
- `seeds/entrances.csv`：入口（`facility_id,name,lat,lon,note`）
  - `facility_id` は facilities.csv の `id`
  - 施設を目的地にすると、登録された入口のうち道のりが一番短いものがゴールになる
  - 入口の座標は、建物の出入口そのものではなく「歩いて着く道に面した位置」に置く（道から離れていると、近くの別の道につながって遠回りになることがある）
- `seeds/facility_areas.csv`：敷地（`facility_id,lat,lon`）※任意
  - 施設の外周の点を、外周に沿った順番で1行ずつ書く（3点以上）
  - 出発地・目的地にした地点が敷地の中なら、その地点ではなく、地点から一番近い入口（門）から経路を探す（地点から門までは直線距離で数える）
- 座標の調べ方：Google マップで入口の位置を右クリックすると、一番上に緯度・経度が表示され、クリックでコピーできる
- 地図を拡大（ズーム17以上）すると、入口の位置が青い点で表示されるので、位置が正しいか確認できる

### git pull のあとに動かなくなったら
他のメンバーがライブラリを追加した可能性がある。仮想環境を有効にした状態で、もう一度インストールする。
```bat
pip install -r requirements.txt
```

### ライブラリを追加するとき
```bat
pip install ライブラリ名
pip freeze > requirements.txt
```
`requirements.txt` の変更も一緒にコミットする。

### 変更を GitHub に上げる
main には直接 push せず、作業ごとにブランチを作って Pull Request を出す。
```bat
git switch main
git pull
git switch -c feature/作業名
（ファイルを編集）
git add .
git commit -m "変更内容"
git push -u origin feature/作業名
```
GitHub で「Compare & pull request」から Pull Request を作成する。

- 作成済みのブランチに戻るときは `-c` を付けずに `git switch feature/作業名`
- `a branch named ... already exists` と出たら、そのブランチは作成済み