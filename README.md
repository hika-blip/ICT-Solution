# ひたふら

地理特性と歩行能力に応じた避難支援Webアプリ（日立市）

## ブラウザで起動する

```
pip install -r requirements.txt
uvicorn backend.app:app --reload
```

http://127.0.0.1:8000/ を開く。出発地・目的地に施設名または「緯度,経度」を入力して検索、
または地図を2回クリック（1回目=出発地、2回目=目的地）で検索できる。
別のグラフを使う場合は環境変数 `GRAPHML` でパスを指定する。

## サーバーなしで動かす（静的Web版）

`web/index.html` をブラウザで開くだけで動く（Python・サーバー不要。地図タイルと施設名検索にはネット接続が必要）。
道路データを更新するときは、graphml から `web/graph.js` を作り直す:

```
python scripts/export_graph_js.py osm_network.graphml web/graph.js
```
