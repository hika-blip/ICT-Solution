"""国土地理院の標高タイル（PNG形式）から、緯度経度の標高[m]を引く。

  https://maps.gsi.go.jp/development/demtile.html
  DEM5A（5mメッシュ・航空レーザ測量）→ 無い場所は DEM5B → それも無い場所は DEM10B（10mメッシュ）

取得したタイルは data/dem_tiles/ に保存し、次からはネットにつながずに使う。
scripts/add_elevation.py（地図データへの書き込み）と、歩行ログの坂道の判定（backend/walks.py）で使う。
"""
import io
import math
import threading
from pathlib import Path

import numpy as np
import requests
from PIL import Image

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "dem_tiles"
# 道に沿って標高を見る間隔[m]。経路検索の坂道の判定と歩行ログの坂道の判定で同じ値を使う
# （標高タイルの誤差で傾きがぶれないよう、細かくしすぎない）
SAMPLE_STEP_M = 20.0
# (タイルの種類, ズームレベル)。上から順に使い、標高が無い（海・未整備）ところは次を使う
SOURCES = [("dem5a_png", 15), ("dem5b_png", 15), ("dem_png", 14)]
TILE_URL = "https://cyberjapandata.gsi.go.jp/xyz/{name}/{z}/{x}/{y}.png"
TILE_SIZE = 256
TIMEOUT_S = 15


class ElevationUnavailable(Exception):
    """タイルを取得できなかった（通信の失敗など）。データが無い場所（海など）とは区別する。"""


class DemTiles:
    """標高タイルを取得して、緯度経度から標高を引く（取得したタイルはメモリとファイルに覚えておく）。"""

    def __init__(self, cache_dir: Path = CACHE_DIR):
        self._cache_dir = cache_dir
        self._tiles = {}
        self._lock = threading.Lock()
        self._session = requests.Session()
        self._session.headers["User-Agent"] = "hitafura-elevation/1.0"

    def _load(self, name: str, z: int, x: int, y: int) -> bytes | None:
        """タイルのPNGを返す。その範囲のデータが無ければ None。"""
        png = self._cache_dir / name / str(z) / str(x) / f"{y}.png"
        none = png.with_suffix(".none")  # 「データが無い」ことを覚えておく印
        if png.exists():
            return png.read_bytes()
        if none.exists():
            return None
        try:
            res = self._session.get(TILE_URL.format(name=name, z=z, x=x, y=y), timeout=TIMEOUT_S)
        except requests.RequestException as e:
            raise ElevationUnavailable(f"標高タイルを取得できませんでした: {e}") from e
        png.parent.mkdir(parents=True, exist_ok=True)
        if res.status_code == 404:
            none.touch()
            return None
        if not res.ok:
            raise ElevationUnavailable(f"標高タイルを取得できませんでした: HTTP {res.status_code}")
        png.write_bytes(res.content)
        return res.content

    def _tile(self, name: str, z: int, x: int, y: int):
        key = (name, z, x, y)
        with self._lock:
            if key not in self._tiles:
                data = self._load(name, z, x, y)
                if data is None:
                    self._tiles[key] = None
                else:
                    rgb = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"), dtype=np.int64)
                    v = rgb[:, :, 0] * 65536 + rgb[:, :, 1] * 256 + rgb[:, :, 2]
                    h = np.where(v < 2 ** 23, v, v - 2 ** 24) * 0.01
                    self._tiles[key] = np.where(v == 2 ** 23, np.nan, h).astype(np.float32)  # 2^23 は「標高なし」
            return self._tiles[key]

    def elevation(self, lat: float, lon: float) -> float | None:
        """標高[m]。データが無い場所なら None。取得に失敗したら ElevationUnavailable。"""
        for name, z in SOURCES:
            n = 2 ** z * TILE_SIZE
            px = (lon + 180) / 360 * n
            r = math.radians(lat)
            py = (1 - math.log(math.tan(r) + 1 / math.cos(r)) / math.pi) / 2 * n
            tile = self._tile(name, z, int(px // TILE_SIZE), int(py // TILE_SIZE))
            if tile is None:
                continue
            h = tile[int(py) % TILE_SIZE, int(px) % TILE_SIZE]
            if not np.isnan(h):
                return float(h)
        return None

    @property
    def tile_count(self) -> int:
        return len(self._tiles)


_shared = None


def shared() -> DemTiles:
    """アプリ全体で使い回す DemTiles。"""
    global _shared
    if _shared is None:
        _shared = DemTiles()
    return _shared
