// 地図のピン（情報共有）。home.html の地図 `map` を使う。
(function () {
  const layer = L.layerGroup().addTo(map);
  const filterEl = document.getElementById("pin-filter");
  const addBtn = document.getElementById("pin-add");
  const sheet = document.getElementById("pin-sheet");
  const form = sheet;
  const tagBox = document.getElementById("pin-tags");
  const errorEl = document.getElementById("pin-error");
  const photoInput = document.getElementById("pin-photo");
  const previewEl = document.getElementById("pin-preview");
  const where = document.getElementById("pin-where");

  let draftMarker = null;
  let draftLatLng = null;

  // route 側の地図クリックを止めるためのフラグ（home.html が参照する）
  window.pinMode = false;

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

  function popupFor(pin) {
    const box = el("div", "pin-popup");
    if (pin.tags.length) {
      const row = el("div", "pin-tagrow");
      pin.tags.forEach((t) => row.appendChild(el("span", "pin-chip", t)));
      box.appendChild(row);
    }
    if (pin.has_photo) {
      const img = el("img", "pin-photo");
      img.src = `/api/pins/${pin.id}/photo`;
      img.alt = "投稿された写真";
      img.loading = "lazy";
      box.appendChild(img);
    }
    if (pin.body) box.appendChild(el("p", "pin-body", pin.body));
    const meta = `投稿者ID: ${pin.author_id} ／ ${fmtDate(pin.created_at)}` + (pin.is_public ? "" : " ／ 非公開");
    box.appendChild(el("p", "pin-meta", meta));
    if (pin.mine) {
      const toggle = el("button", "pin-toggle", pin.is_public ? "非公開にする" : "公開にする");
      toggle.type = "button";
      toggle.addEventListener("click", async () => {
        const res = await fetch(`/api/pins/${pin.id}/visibility`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ is_public: !pin.is_public }),
        });
        if (res.ok) loadPins();
        else alert((await res.json()).error || "変更できませんでした。");
      });
      box.appendChild(toggle);
      const del = el("button", "pin-delete", "このピンを削除");
      del.type = "button";
      del.addEventListener("click", async () => {
        if (!confirm("このピンを削除しますか？")) return;
        const res = await fetch(`/api/pins/${pin.id}`, { method: "DELETE" });
        if (res.ok) loadPins();
        else alert((await res.json()).error || "削除できませんでした。");
      });
      box.appendChild(del);
    }
    return box;
  }

  async function loadPins() {
    const b = map.getBounds();
    const params = new URLSearchParams({
      south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast(),
    });
    if (filterEl.value) params.set("tag", filterEl.value);
    try {
      const res = await fetch("/api/pins?" + params);
      if (res.status === 401) { location.href = "/login"; return; }
      if (!res.ok) return;
      const pins = await res.json();
      layer.clearLayers();
      pins.forEach((pin) => {
        const icon = pin.tags.some((t) => t === "危険箇所" || t === "通行止め")
          ? "pin-icon pin-icon-danger" : "pin-icon";
        L.marker([pin.lat, pin.lon], {
          icon: L.divIcon({ className: icon, html: "<span></span>", iconSize: [26, 26], iconAnchor: [13, 26] }),
        }).addTo(layer).bindPopup(() => popupFor(pin), { minWidth: 200, maxWidth: 260 });
      });
    } catch (e) { /* 通信失敗時は前の表示のまま */ }
  }

  // ---- 投稿 ----
  function setPinMode(on) {
    window.pinMode = on;
    addBtn.textContent = on ? "キャンセル" : "ピンを立てる";
    document.getElementById("map").classList.toggle("map-pin-mode", on);
    document.getElementById("message").textContent = on
      ? "ピンを立てる場所を地図でタップしてください。"
      : "地図をタップして出発地を選んでください。";
    if (!on) closeSheet();
  }

  function closeSheet() {
    sheet.hidden = true;
    form.reset();
    previewEl.hidden = true;
    errorEl.textContent = "";
    if (draftMarker) { map.removeLayer(draftMarker); draftMarker = null; }
    draftLatLng = null;
  }

  addBtn.addEventListener("click", () => setPinMode(!window.pinMode));
  document.getElementById("pin-cancel").addEventListener("click", () => setPinMode(false));

  map.on("click", (e) => {
    if (!window.pinMode) return;
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

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!draftLatLng) return;
    const body = form.elements.body.value.trim();
    if (!body && !photoInput.files[0]) {
      errorEl.textContent = "本文か写真のどちらかは入力してください。";
      return;
    }
    const fd = new FormData();
    fd.append("lat", draftLatLng.lat);
    fd.append("lon", draftLatLng.lng);
    fd.append("body", body);
    fd.append("is_public", form.elements.is_public.checked ? "true" : "false");
    tagBox.querySelectorAll("input:checked").forEach((c) => fd.append("tags", c.value));
    if (photoInput.files[0]) fd.append("photo", photoInput.files[0]);
    const submit = form.querySelector("[type=submit]");
    submit.disabled = true;
    try {
      const res = await fetch("/api/pins", { method: "POST", body: fd });
      if (res.status === 401) { location.href = "/login"; return; }
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        errorEl.textContent = data.error || "投稿できませんでした。";
        return;
      }
      setPinMode(false);
      loadPins();
    } catch (err) {
      errorEl.textContent = "通信に失敗しました。";
    } finally {
      submit.disabled = false;
    }
  });

  // ---- 初期化 ----
  function addTagChoice(t, checked) {
    if ([...tagBox.querySelectorAll("input")].some((i) => i.value === t)) return;
    const label = el("label", "pin-check");
    const input = document.createElement("input");
    input.type = "checkbox";
    input.value = t;
    input.checked = !!checked;
    label.appendChild(input);
    label.appendChild(el("span", "", t));
    tagBox.appendChild(label);
  }

  function addFilterOption(t) {
    if ([...filterEl.options].some((o) => o.value === t)) return;
    const opt = el("option", "", t);
    opt.value = t;
    filterEl.appendChild(opt);
  }

  fetch("/api/pin-tags").then((r) => r.json()).then((list) => {
    list.forEach((t) => { addFilterOption(t); addTagChoice(t); });
  });

  filterEl.addEventListener("change", loadPins);
  map.on("moveend", loadPins);
  loadPins();
})();
