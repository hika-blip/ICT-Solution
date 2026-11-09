// 危険個所の投稿・表示。home.html の地図 `map` を使う。
// 地図をタップ → 通行可否を選ぶ → 写真1枚を付けて投稿する。
(function () {
  const STATUS_LABEL = { passable: "歩いて通れる（注意）", blocked: "通行不可" };
  const layer = L.layerGroup().addTo(map);
  const addBtn = document.getElementById("hazard-add");
  const sheet = document.getElementById("hazard-sheet");
  const where = document.getElementById("hazard-where");
  const errorEl = document.getElementById("hazard-error");
  const photoInput = document.getElementById("hazard-photo");
  const previewEl = document.getElementById("hazard-preview");
  const tagBox = document.getElementById("hazard-tags");
  const badgeEl = document.getElementById("hazard-badge");

  let draftMarker = null;
  let draftLatLng = null;

  // 他の地図クリック処理（経路用など）を止めるためのフラグ
  window.hazardMode = false;

  function el(tag, className, text) {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function fmtDate(iso) {
    const d = new Date(iso);
    const p = (n) => String(n).padStart(2, "0");
    return `${d.getFullYear()}/${p(d.getMonth() + 1)}/${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  function popupFor(h) {
    const box = el("div", "pin-popup");
    const row = el("div", "pin-tagrow");
    row.appendChild(el("span", `pin-chip hazard-chip-${h.status}`, STATUS_LABEL[h.status]));
    h.tags.forEach((t) => row.appendChild(el("span", "pin-chip", t)));
    box.appendChild(row);
    if (h.has_photo) {
      const img = el("img", "pin-photo");
      img.src = `/api/hazards/${h.id}/photo`;
      img.alt = "投稿された写真";
      img.loading = "lazy";
      box.appendChild(img);
    }
    box.appendChild(el("p", "pin-meta", `投稿者ID: ${h.author_id} ／ ${fmtDate(h.created_at)}`));
    if (h.mine) {
      const del = el("button", "pin-delete", "この投稿を削除");
      del.type = "button";
      del.addEventListener("click", async () => {
        if (!confirm("この投稿を削除しますか？")) return;
        const res = await fetch(`/api/hazards/${h.id}`, { method: "DELETE" });
        if (res.ok) { loadHazards(); loadBlockedEdges(); }
        else alert((await res.json()).error || "削除できませんでした。");
      });
      box.appendChild(del);
    }
    return box;
  }

  async function loadHazards() {
    const b = map.getBounds();
    const params = new URLSearchParams({
      south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast(),
    });
    try {
      const res = await fetch("/api/hazards?" + params);
      if (res.status === 401) { location.href = "/login"; return; }
      if (!res.ok) return;
      const list = await res.json();
      layer.clearLayers();
      list.forEach((h) => {
        const big = h.status === "passable";  // 通行可能のピンは大きく目立たせる
        L.marker([h.lat, h.lon], {
          icon: L.divIcon({
            className: `hazard-icon hazard-icon-${h.status}`, html: "<span></span>",
            iconSize: big ? [34, 34] : [26, 26], iconAnchor: big ? [17, 34] : [13, 26],
          }),
          zIndexOffset: big ? 1000 : 0,
        }).addTo(layer).bindPopup(() => popupFor(h), { minWidth: 200, maxWidth: 260 });
      });
    } catch (e) { /* 通信失敗時は前の表示のまま */ }
  }

  // ---- 通れない道（緊急時の地図）----
  // 通行不可の危険個所にいちばん近い道1本を、黒と黄色の縞線で示す（拡大すると×印も出る）。範囲内のノードは経路探索が通らない
  const blockedLayer = L.layerGroup();
  const xGroup = L.layerGroup();   // ×印。拡大したときだけ blockedLayer に入れて表示する
  const X_MIN_ZOOM = 17;           // このズーム以上のとき ×印を出す

  function updateXVisibility() {
    if (map.getZoom() >= X_MIN_ZOOM) blockedLayer.addLayer(xGroup);
    else blockedLayer.removeLayer(xGroup);
  }
  map.on("zoomend", updateXVisibility);

  async function loadBlockedEdges() {
    blockedLayer.clearLayers();
    xGroup.clearLayers();
    if (!window.emergencyMode) { map.removeLayer(blockedLayer); return; }
    try {
      const res = await fetch("/api/hazards/blocked-edges");
      if (!res.ok || !window.emergencyMode) return;
      const edges = await res.json();
      blockedLayer.clearLayers();
      xGroup.clearLayers();
      edges.forEach((e) => {
        // 黒の太線の上に黄色の破線を重ねて、バリケード風の縞にする（避難経路の赤・橙・紫と区別する）
        L.polyline(e.coords, { color: "#000", weight: 10, opacity: 0.9, interactive: false }).addTo(blockedLayer);
        L.polyline(e.coords, { color: "#FFD600", weight: 6, dashArray: "10 10", lineCap: "butt", interactive: false })
          .addTo(blockedLayer);
        L.marker(e.mid, {
          icon: L.divIcon({ className: "blocked-x", html: "<span>×</span>", iconSize: [24, 24], iconAnchor: [12, 12] }),
          interactive: false, keyboard: false,
        }).addTo(xGroup);
      });
      updateXVisibility();
      blockedLayer.addTo(map);
    } catch (err) { /* 通信失敗時は表示しない */ }
  }

  // ---- 投稿 ----
  function closeSheet() {
    sheet.hidden = true;
    sheet.reset();
    previewEl.hidden = true;
    errorEl.textContent = "";
    if (draftMarker) { map.removeLayer(draftMarker); draftMarker = null; }
    draftLatLng = null;
  }

  function setMode(on) {
    if (on && window.pinMode) document.getElementById("pin-add").click();  // ピン投稿中なら先にやめる
    window.hazardMode = on;
    addBtn.textContent = on ? "やめる" : "危険";
    document.getElementById("map").classList.toggle("map-pin-mode", on);
    badgeEl.textContent = "危険な場所を地図でタップしてください。";
    badgeEl.hidden = !on;
    if (!on) closeSheet();
  }

  addBtn.addEventListener("click", () => setMode(!window.hazardMode));
  document.getElementById("hazard-cancel").addEventListener("click", () => setMode(false));

  // ピン投稿を始めたら、こちらはやめる（pins.js のボタンが押されたとき）
  document.getElementById("pin-add").addEventListener("click", () => {
    if (window.hazardMode) setMode(false);
  });

  // 平常時／緊急時を切り替えたら、投稿の操作はいったんやめる
  // モードが切り替わった時点（emergency.js が通知する）で、投稿をやめ、通れない道を出し入れする
  document.addEventListener("emergency-mode-changed", () => {
    if (window.hazardMode) setMode(false);
    loadBlockedEdges();
  });

  map.on("click", (e) => {
    if (!window.hazardMode) return;
    draftLatLng = e.latlng;
    if (draftMarker) map.removeLayer(draftMarker);
    draftMarker = L.marker(e.latlng).addTo(map);
    where.textContent = `位置: ${e.latlng.lat.toFixed(5)}, ${e.latlng.lng.toFixed(5)}`;
    sheet.hidden = false;
  });

  photoInput.addEventListener("change", () => {
    const f = photoInput.files[0];
    if (!f) { previewEl.hidden = true; return; }
    previewEl.src = URL.createObjectURL(f);
    previewEl.hidden = false;
  });

  sheet.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!draftLatLng) return;
    const fd = new FormData();
    fd.append("lat", draftLatLng.lat);
    fd.append("lon", draftLatLng.lng);
    fd.append("status", sheet.elements.status.value);
    tagBox.querySelectorAll("input:checked").forEach((c) => fd.append("tags", c.value));
    if (photoInput.files[0]) fd.append("photo", photoInput.files[0]);
    const submit = sheet.querySelector("[type=submit]");
    submit.disabled = true;
    try {
      const res = await fetch("/api/hazards", { method: "POST", body: fd });
      if (res.status === 401) { location.href = "/login"; return; }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        errorEl.textContent = data.error || "投稿できませんでした。";
        return;
      }
      setMode(false);
      loadHazards();
      loadBlockedEdges();
    } catch (err) {
      errorEl.textContent = "通信に失敗しました。";
    } finally {
      submit.disabled = false;
    }
  });

  fetch("/api/hazard-tags").then((r) => r.json()).then((list) => {
    list.forEach((t) => {
      const label = el("label", "pin-check");
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = t;
      label.appendChild(input);
      label.appendChild(el("span", "", t));
      tagBox.appendChild(label);
    });
  });

  map.on("moveend", loadHazards);
  loadHazards();
})();
