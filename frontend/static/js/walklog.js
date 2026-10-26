// 歩行ログの記録。home.html の地図 `map` と、current_location.js が出す "current-location" を使う。
// 記録中の点はブラウザにためておき、「記録停止」でまとめて /api/walks に送る。
// 途中で再読み込みしても続きから記録できるよう、記録中のデータは localStorage にも保存する。
(function () {
  const DRAFT_KEY = "walklog-draft";
  const MAX_ACCURACY_M = 50;  // 線を描くのに使う点の誤差の上限（サーバと同じ）
  const toggleBtn = document.getElementById("walk-toggle");
  const infoEl = document.getElementById("walk-info");

  let draft = null;      // 記録中のデータ { startedAt, points: [{lat, lon, accuracy, t}] }
  let unsaved = null;    // 送信に失敗した記録（もう一度送れるように残す）
  let line = null;       // 記録中の道の線
  let distance = 0;      // 線の長さ [m]（表示用。保存する距離はサーバで計算する）
  let lastGood = null;   // 線に使った最後の点
  let timer = null;
  let wakeLock = null;

  window.walkRecording = false;

  function loadDraft() {
    try { return JSON.parse(localStorage.getItem(DRAFT_KEY)); } catch (e) { return null; }
  }
  function saveDraft() {
    try { localStorage.setItem(DRAFT_KEY, JSON.stringify(draft)); } catch (e) { /* 保存できなくても記録は続ける */ }
  }
  function clearDraft() {
    try { localStorage.removeItem(DRAFT_KEY); } catch (e) { /* 何もしない */ }
  }

  const fmtDuration = walkFormat.duration, fmtDistance = walkFormat.distance;  // walk_format.js
  function showInfo() {
    infoEl.textContent = `記録中　${fmtDuration((Date.now() - draft.startedAt) / 1000)}・${fmtDistance(distance)}`;
  }

  // ---- 画面を消さないようにする（Wake Lock API） ----
  async function keepScreenOn() {
    if (!("wakeLock" in navigator)) return false;
    try { wakeLock = await navigator.wakeLock.request("screen"); return true; } catch (e) { return false; }
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && window.walkRecording) keepScreenOn();
  });

  // ---- 点の追加 ----
  function addToLine(p) {
    if (p.accuracy != null && p.accuracy > MAX_ACCURACY_M) return;
    const latlng = L.latLng(p.lat, p.lon);
    if (lastGood) distance += lastGood.distanceTo(latlng);
    lastGood = latlng;
    line.addLatLng(latlng);
  }

  document.addEventListener("current-location", (e) => {
    if (!window.walkRecording) return;
    const { latlng, accuracy, time } = e.detail;
    const p = { lat: latlng.lat, lon: latlng.lng, accuracy, t: time };
    draft.points.push(p);
    saveDraft();
    addToLine(p);
    showInfo();
  });

  // ---- 開始・停止 ----
  async function start(resumed) {
    unsaved = null;
    if (!resumed) draft = { startedAt: Date.now(), points: [] };
    window.walkRecording = true;
    distance = 0;
    lastGood = null;
    if (line) map.removeLayer(line);
    line = L.polyline([], { color: "#E8710A", weight: 5 }).addTo(map);
    draft.points.forEach(addToLine);
    saveDraft();
    toggleBtn.textContent = "記録停止";
    toggleBtn.classList.add("walk-recording");
    showInfo();
    timer = setInterval(showInfo, 1000);
    if (!(await keepScreenOn())) {
      infoEl.textContent += "（記録中は画面をつけたままにしてください）";
    }
  }

  function stop() {
    window.walkRecording = false;
    clearInterval(timer);
    if (wakeLock) { wakeLock.release().catch(() => {}); wakeLock = null; }
    toggleBtn.classList.remove("walk-recording");
    unsaved = draft;
    draft = null;
    send();
  }

  async function send() {
    toggleBtn.disabled = true;
    infoEl.textContent = "保存しています…";
    try {
      const res = await fetch("/api/walks", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ points: unsaved.points }),
      });
      if (res.status === 401) { location.href = "/login"; return; }
      const data = await res.json();
      if (res.ok) {
        infoEl.textContent = `保存しました　${fmtDistance(data.distance_m)}・${fmtDuration(data.duration_s)}`
          + `・${walkFormat.speed(data.speed_m_per_min)}`;
        document.dispatchEvent(new CustomEvent("walk-saved", { detail: data }));
      } else {
        // 位置が少なすぎるなど、送り直しても保存できない記録は捨てる
        infoEl.textContent = data.error || "保存できませんでした。";
      }
      unsaved = null;
      clearDraft();
      toggleBtn.textContent = "記録開始";
    } catch (e) {
      infoEl.textContent = "通信に失敗しました。電波のよい場所で「保存し直す」を押してください。";
      toggleBtn.textContent = "保存し直す";
    } finally {
      toggleBtn.disabled = false;
    }
  }

  toggleBtn.addEventListener("click", () => {
    if (window.walkRecording) stop();
    else if (unsaved) send();
    else start(false);
  });

  // 再読み込み前に記録中だったら、続きから記録する
  const saved = loadDraft();
  if (saved && Array.isArray(saved.points) && saved.startedAt) {
    draft = saved;
    start(true);
  }
})();
