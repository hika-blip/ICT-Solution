// 歩行ログの表示用の書式（walklog.js・walks.html・walk_detail.html で使う）
window.walkFormat = {
  // 秒 → "12:34" または "1:02:03"
  duration(sec) {
    sec = Math.max(0, Math.round(sec));
    const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
    const p = (n) => String(n).padStart(2, "0");
    return h ? `${h}:${p(m)}:${p(s)}` : `${m}:${p(s)}`;
  },
  // m → "850 m" または "1.25 km"
  distance(m) {
    return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
  },
  // m/分 → "約 72 m/分"（計算できないときは "—"）
  speed(v) {
    return v ? `約 ${Math.round(v)} m/分` : "—";
  },
  // ISO形式 → "2026/10/26 14:05"（端末の時刻で表示）
  date(iso) {
    const d = new Date(iso);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  },
};
