"""指定避難場所の CSV(data/evacuation_sites.csv)を web/places.csv の形式に変換して追記する。

使い方:
  python scripts/import_evacuation.py [入力CSV] [出力 places.csv]
既定: data/evacuation_sites.csv → web/places.csv
places.csv に同じ名称がすでにあれば、その行は追加しない（何度実行しても重複しない）。
入力は UTF-8（BOM付き可）。Shift_JIS(cp932) で保存されたものも自動で読み替える。
"""
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEADER = ["名称", "読み", "別名", "緯度", "経度", "入口緯度", "入口経度", "種別", "備考"]


def read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    raise SystemExit(f"{path}: 文字コードを判別できません（UTF-8 か Shift_JIS で保存してください）")


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "evacuation_sites.csv"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "web" / "places.csv"

    rows = list(csv.reader(read_text(src).splitlines()))
    # 先頭のタイトル行を飛ばし、「No.」で始まる見出し行の次からをデータとする
    start = next(i for i, r in enumerate(rows) if r and r[0].strip() == "No.") + 1

    existing = set()
    if dst.exists():
        existing = {r["名称"] for r in csv.DictReader(read_text(dst).splitlines())}

    new = []
    for r in rows[start:]:
        if len(r) < 5 or not r[1].strip():
            continue
        name, addr, lat, lon = r[1].strip(), r[2].strip(), r[3].strip(), r[4].strip()
        float(lat), float(lon)  # 数値でなければここで止める
        if name in existing:
            continue
        existing.add(name)
        new.append([name, "", "", lat, lon, "", "", "避難所", f"指定避難場所 {addr}"])

    need_header = not dst.exists() or dst.stat().st_size == 0
    needs_newline = False
    if not need_header:
        needs_newline = not dst.read_bytes().endswith(b"\n")
    with open(dst, "a", encoding="utf-8", newline="") as f:
        if needs_newline:
            f.write("\n")
        w = csv.writer(f, lineterminator="\n")
        if need_header:
            w.writerow(HEADER)
        w.writerows(new)
    print(f"{len(new)} 件を {dst} に追加しました")


if __name__ == "__main__":
    main()
