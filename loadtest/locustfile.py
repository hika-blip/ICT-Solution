"""経路検索APIの負荷テスト（シナリオC：経路検索の限界測定）。

測るAPIごとにユーザの種類を分けてある。コマンドの最後に1つ指定して、1つずつ測る:
    RouteUser  … /api/route             （A*探索 1回）
    PlanUser   … /api/route/plan        （入口の組み合わせ × 時間優先・距離優先）
    EvacUser   … /api/evacuate/routes   （近い避難先それぞれへの経路）

人数は StepLoad が段階的に増やす（--steps と --step-seconds で変えられる）。
各ユーザは最初に1回だけログインし、検索 → 1〜3秒待つ、を繰り返す。
結果の名前には直線距離で「近（〜1km）・中（1〜2.5km）・遠（2.5km〜）」を付けて、距離ごとにも見られるようにする。

準備と実行の手順は loadtest/README.md を参照。
"""
import itertools
import json
import math
import random
from pathlib import Path

from locust import HttpUser, LoadTestShape, between, events, task

HERE = Path(__file__).resolve().parent
POINTS = json.loads((HERE / "points.json").read_text(encoding="utf-8"))
USERS = json.loads((HERE / "users.json").read_text(encoding="utf-8"))
_user_cycle = itertools.cycle(USERS)


@events.init_command_line_parser.add_listener
def _add_options(parser):
    parser.add_argument("--steps", type=str, default="1,5,10,20,40,80,160",
                        help="段階ごとの同時ユーザ数（カンマ区切り）")
    parser.add_argument("--step-seconds", type=int, default=120, help="1段階の長さ[秒]")


def _straight_m(a, b) -> float:
    """2点の直線距離[m]（近い範囲なので平面で近似）。"""
    dy = (a[0] - b[0]) * 110_574
    dx = (a[1] - b[1]) * 111_320 * math.cos(math.radians(a[0]))
    return math.hypot(dx, dy)


def _band(m: float) -> str:
    if m < 1000:
        return "近"
    if m < 2500:
        return "中"
    return "遠"


class _LoggedInUser(HttpUser):
    abstract = True
    wait_time = between(1, 3)

    def on_start(self):
        account = next(_user_cycle)
        with self.client.post("/login", data=account, allow_redirects=False,
                              name="/login", catch_response=True) as res:
            if res.status_code not in (302, 303) or "session" not in self.client.cookies:
                res.failure(f"ログイン失敗: {res.status_code}")

    def _get(self, path: str, params: dict, name: str):
        with self.client.get(path, params=params, name=name, catch_response=True) as res:
            if res.status_code != 200:
                res.failure(f"{res.status_code}: {res.text[:100]}")

    def _pair(self):
        orig, dest = random.sample(POINTS, 2)
        return orig, dest, _band(_straight_m(orig, dest))


class RouteUser(_LoggedInUser):
    @task
    def route(self):
        orig, dest, band = self._pair()
        self._get("/api/route", {"orig_lat": orig[0], "orig_lon": orig[1],
                                 "dest_lat": dest[0], "dest_lon": dest[1]}, f"/api/route [{band}]")


class PlanUser(_LoggedInUser):
    @task
    def plan(self):
        orig, dest, band = self._pair()
        self._get("/api/route/plan", {"orig_lat": orig[0], "orig_lon": orig[1],
                                      "dest_lat": dest[0], "dest_lon": dest[1]}, f"/api/route/plan [{band}]")


class EvacUser(_LoggedInUser):
    @task
    def evacuate(self):
        point = random.choice(POINTS)
        self._get("/api/evacuate/routes", {"lat": point[0], "lon": point[1], "count": 3},
                  "/api/evacuate/routes")


class StepLoad(LoadTestShape):
    """--steps の人数を --step-seconds ずつ順に試し、最後まで行ったら終わる。"""

    def tick(self):
        opts = self.runner.environment.parsed_options
        steps = [int(s) for s in opts.steps.split(",") if s.strip()]
        index = int(self.get_run_time() // opts.step_seconds)
        if index >= len(steps):
            return None
        users = steps[index]
        return users, users  # 1秒でその人数まで増やす
