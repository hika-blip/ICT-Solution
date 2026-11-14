"""Locust の結果（--csv で出した *_stats_history.csv）から、発表用のグラフと表を作る。

段階（同時ユーザ数）ごとに、人数を増やした直後の30秒を除いた区間の平均をとる。
    python loadtest/plot.py loadtest/results/route_w1
    python loadtest/plot.py loadtest/results/route_w1 loadtest/results/route_w4   # 重ねて比べる
→ 1つ目の結果と同じ場所に <名前>_summary.png を保存し、表を画面に出す。
"""
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

plt.rcParams["font.family"] = ["Yu Gothic", "Meiryo", "MS Gothic", "sans-serif"]

WARMUP_S = 30          # 人数を増やした直後は不安定なので除く[秒]
P95_LIMIT_MS = 3000    # 限界とみなす p95
ERROR_LIMIT = 0.01     # 限界とみなすエラー率


def summarize(prefix: str) -> pd.DataFrame:
    path = f"{prefix}_stats_history.csv"
    try:
        df = pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:   # Windows では Locust が Shift_JIS で書くことがある
        df = pd.read_csv(path, encoding="cp932")
    df = df[df["Name"] == "Aggregated"].copy()
    df["t"] = df["Timestamp"] - df["Timestamp"].min()
    df["step_start"] = df.groupby("User Count")["t"].transform("min")
    df = df[(df["t"] - df["step_start"] >= WARMUP_S) & (df["User Count"] > 0)]
    rows = []
    for users, g in df.groupby("User Count"):
        last = g.iloc[-1]
        rows.append({
            "users": users,
            "rps": g["Requests/s"].mean(),
            "p50_ms": g["50%"].mean(),
            "p95_ms": g["95%"].mean(),
            "error_rate": g["Failures/s"].sum() / max(g["Requests/s"].sum(), 1e-9),
            "total_requests": last["Total Request Count"],
        })
    out = pd.DataFrame(rows)
    out["over_limit"] = (out["p95_ms"] > P95_LIMIT_MS) | (out["error_rate"] > ERROR_LIMIT)
    return out


def main() -> None:
    prefixes = sys.argv[1:]
    if not prefixes:
        sys.exit(__doc__)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    for prefix in prefixes:
        label = Path(prefix).name
        s = summarize(prefix)
        print(f"\n== {label} ==")
        print(s.to_string(index=False, float_format=lambda v: f"{v:.2f}"))
        ok = s[~s["over_limit"]]
        print(f"→ 基準内の最大人数: {int(ok['users'].max()) if len(ok) else 'なし'} 人"
              f"（p95 ≤ {P95_LIMIT_MS} ms, エラー率 ≤ {ERROR_LIMIT:.0%}）")
        ax1.plot(s["users"], s["p95_ms"] / 1000, marker="o", label=label)
        ax2.plot(s["users"], s["rps"], marker="o", label=label)
    ax1.axhline(P95_LIMIT_MS / 1000, color="gray", linestyle="--", linewidth=1)
    ax1.set(xlabel="同時ユーザ数", ylabel="p95 応答時間 [秒]", title="応答時間（p95）")
    ax2.set(xlabel="同時ユーザ数", ylabel="リクエスト/秒", title="処理量（RPS）")
    for ax in (ax1, ax2):
        ax.grid(alpha=0.3)
        ax.legend()
    fig.tight_layout()
    out = Path(f"{prefixes[0]}_summary.png")
    fig.savefig(out, dpi=150)
    print(f"\nグラフを保存しました: {out}")


if __name__ == "__main__":
    main()
