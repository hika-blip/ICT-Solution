// 管理画面（admin.html）。左に一覧・入力欄、右に地図。
// データは /admin/api/... から読み書きする（backend/admin.py）。
(function () {
  const { categories, kinds, tags, area } = window.ADMIN;

  // 施設の種類ごとの印（住民の画面の facilities.js と同じ見た目）
  const CATEGORY_STYLE = {
    "駅": { label: "駅", cls: "station" },
    "学校": { label: "学", cls: "school" },
    "観光地": { label: "観", cls: "tourism" },
    "避難所": { label: "避", cls: "shelter" },
  };

  // ---------- 共通 ----------
  const $ = (id) => document.getElementById(id);
  function el(tag, className, text) {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  let toastTimer = null;
  function toast(text, isError = false) {
    const t = $("toast");
    t.textContent = text;
    t.classList.toggle("error", isError);
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 3000);
  }
  async function api(method, url, body) {
    const res = await fetch(url, {
      method,
      headers: body ? { "Content-Type": "application/json" } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    if (res.status === 401) { location.href = "/admin/login"; throw new Error("ログインが必要です"); }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || "処理できませんでした。");
    return data;
  }
  const fmt = (v) => Number(v).toFixed(6);

  // ---------- 地図 ----------
  const map = L.map("map").setView([36.5995, 140.651], 14);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);
  if (area) {
    // 経路検索できる範囲（地図データの範囲）。この外には登録できない
    const bounds = L.latLngBounds([[area.south, area.west], [area.north, area.east]]);
    L.rectangle(bounds, { color: "#2E6BB0", weight: 2, fill: false, interactive: false }).addTo(map);
    map.fitBounds(bounds);
  }
  const dataLayer = L.featureGroup().addTo(map);  // 一覧の印
  let fitted = !!area;  // 地図データの範囲が無いときは、最初の一覧の印が全部見えるように合わせる
  const editLayer = L.layerGroup().addTo(map);  // 編集中の印（ドラッグで動かせる）
  const mapWrap = document.querySelector(".admin-map-wrap");
  function setHint(text) {
    $("map-hint").textContent = text || "";
    $("map-hint").hidden = !text;
    mapWrap.classList.toggle("picking", !!text);
  }

  function facilityIcon(category, hidden) {
    const s = CATEGORY_STYLE[category] || { label: "●", cls: "other" };
    return L.divIcon({
      className: `facility-icon facility-icon-${s.cls}${hidden ? " is-hidden" : ""}`,
      html: `<span>${s.label}</span>`, iconSize: [34, 34], iconAnchor: [17, 17],
    });
  }
  const evacIcon = (hidden) => L.divIcon({
    className: `admin-evac-icon${hidden ? " is-hidden" : ""}`, html: "<span>避</span>",
    iconSize: [22, 22], iconAnchor: [11, 11],
  });
  const pinIcon = L.divIcon({ className: "admin-pin-icon", html: "<span></span>", iconSize: [22, 22], iconAnchor: [11, 20] });
  const entranceIcon = (n) => L.divIcon({
    className: "admin-entrance-icon", html: `<span>${n}</span>`, iconSize: [20, 20], iconAnchor: [10, 10],
  });

  // ---------- 状態 ----------
  let tab = "facilities";
  let items = [];        // 今のタブの一覧
  let editing = null;    // 編集中 { type: "facility" | "site", item, entrances, marker }
  let pickEntrance = false;  // 「入口を追加」を押して、地図のクリックを待っている

  // ---------- タブ ----------
  document.querySelectorAll(".admin-tab").forEach((btn) => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  function switchTab(next) {
    closeForm();
    tab = next;
    document.querySelectorAll(".admin-tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    $("list-view").hidden = tab === "csv";
    $("csv-view").hidden = tab !== "csv";
    $("add").hidden = tab === "pins";
    $("search").value = "";
    $("search").placeholder = tab === "pins" ? "本文・投稿者で検索" : "名前で検索";
    const filter = $("filter");
    filter.replaceChildren();
    const options = tab === "facilities" ? [["", "すべての種類"], ...categories.map((c) => [c, c]), ["__hidden", "非公開のみ"]]
      : tab === "sites" ? [["", "すべての種別"], ...kinds.map((k) => [k, `指定${k}`]), ["__hidden", "非公開のみ"]]
      : [["", "すべてのタグ"], ...tags.map((t) => [t, t])];
    options.forEach(([v, label]) => { const o = el("option", "", label); o.value = v; filter.appendChild(o); });
    filter.hidden = tab === "csv";
    if (tab === "csv") { dataLayer.clearLayers(); loadCsvStatus(); return; }
    load();
  }

  async function load() {
    const url = tab === "facilities" ? "/admin/api/facilities"
      : tab === "sites" ? "/admin/api/evac-sites"
      : "/admin/api/pins" + ($("filter").value ? "?tag=" + encodeURIComponent($("filter").value) : "");
    try {
      items = await api("GET", url);
    } catch (e) { toast(e.message, true); items = []; }
    render();
  }

  // ---------- 一覧と地図の印 ----------
  function visibleItems() {
    const q = $("search").value.trim();
    const f = $("filter").value;
    return items.filter((it) => {
      if (tab === "pins") {
        return !q || (it.body || "").includes(q) || (it.author_name || "").includes(q) || String(it.author_id).includes(q);
      }
      if (q && !it.name.includes(q) && !(it.address || "").includes(q)) return false;
      if (f === "__hidden") return it.hidden;
      if (f && tab === "facilities") return it.category === f;
      if (f && tab === "sites") return it.kinds.includes(f);
      return true;
    });
  }

  function render() {
    const list = $("list");
    list.replaceChildren();
    dataLayer.clearLayers();
    const shown = visibleItems();
    const unit = tab === "pins" ? "件のピン（公開中のもののみ）" : "件";
    $("count").textContent = `${shown.length}${unit}` + (shown.length !== items.length ? `（全${items.length}件中）` : "");
    shown.forEach((it) => {
      if (tab === "pins") { renderPin(it, list); return; }
      const li = el("li");
      const btn = el("button", "admin-item");
      btn.type = "button";
      const chip = tab === "facilities"
        ? el("span", `admin-chip admin-chip-${(CATEGORY_STYLE[it.category] || {}).cls || ""}`, it.category)
        : el("span", "admin-chip admin-chip-evac", it.kinds.join("・"));
      const main = el("span", "admin-item-main");
      main.append(el("span", "admin-item-name", it.name),
        el("span", "admin-item-sub", tab === "facilities"
          ? (it.entrances.length ? `入口：${it.entrances.map((e) => e.name).join("・")}` : "入口なし（ピンの位置がゴール）")
          : (it.address || `${fmt(it.lat)}, ${fmt(it.lon)}`)));
      btn.append(chip, main);
      if (it.hidden) btn.appendChild(el("span", "admin-hidden-badge", "非公開"));
      btn.addEventListener("click", () => openForm(it));
      li.appendChild(btn);
      list.appendChild(li);
      // 編集中のものは、ドラッグできる印（editLayer）の方だけ出す
      if (editing && editing.item && editing.item.id === it.id) return;
      const icon = tab === "facilities" ? facilityIcon(it.category, it.hidden) : evacIcon(it.hidden);
      L.marker([it.lat, it.lon], { icon, title: it.name }).addTo(dataLayer).on("click", () => openForm(it));
    });
    fitToItemsOnce();
  }

  function fitToItemsOnce() {
    if (fitted || !dataLayer.getLayers().length) return;
    map.fitBounds(dataLayer.getBounds(), { padding: [40, 40], maxZoom: 16 });
    fitted = true;
  }

  function renderPin(p, list) {
    const li = el("li", "admin-pin");
    li.dataset.id = p.id;
    const head = el("div", "admin-pin-head");
    head.append(el("span", "", new Date(p.created_at).toLocaleString("ja-JP")),
      el("span", "", `投稿者：${p.author_name}（ID ${p.author_id}）`));
    p.tags.forEach((t) => head.appendChild(el("span", "admin-chip admin-chip-pin", t)));
    li.appendChild(head);
    if (p.body) li.appendChild(el("p", "admin-pin-body", p.body));
    if (p.has_photo) {
      const img = el("img", "admin-pin-photo");
      img.src = `/admin/api/pins/${p.id}/photo`;
      img.alt = "投稿された写真";
      img.loading = "lazy";
      li.appendChild(img);
    }
    const del = el("button", "admin-pin-delete", "このピンを削除");
    del.type = "button";
    del.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("このピンを削除します。写真も消え、元に戻せません。よろしいですか？")) return;
      try {
        await api("DELETE", `/admin/api/pins/${p.id}`);
        toast("ピンを削除しました");
        load();
      } catch (err) { toast(err.message, true); }
    });
    li.appendChild(del);
    const marker = L.marker([p.lat, p.lon], { icon: pinIcon }).addTo(dataLayer);
    const select = () => {
      document.querySelectorAll(".admin-pin.active").forEach((x) => x.classList.remove("active"));
      li.classList.add("active");
      map.setView([p.lat, p.lon], Math.max(map.getZoom(), 16));
    };
    li.addEventListener("click", select);
    marker.on("click", () => { select(); li.scrollIntoView({ block: "nearest", behavior: "smooth" }); });
    list.appendChild(li);
  }

  $("search").addEventListener("input", render);
  $("filter").addEventListener("change", () => (tab === "pins" ? load() : render()));
  $("add").addEventListener("click", () => openForm(null));

  // ---------- 追加・編集の入力欄 ----------
  const formFor = (type) => $(type === "facility" ? "facility-form" : "site-form");

  function openForm(item) {
    closeForm();
    const type = tab === "facilities" ? "facility" : "site";
    const form = formFor(type);
    editing = { type, item, entrances: item && item.entrances ? item.entrances.map((e) => ({ ...e })) : [], marker: null };
    $("list-view").hidden = true;
    form.hidden = false;
    form.reset();
    $(`${type}-error`).hidden = true;
    $(`${type}-form-title`).textContent = (item ? "編集：" : "") + (type === "facility" ? (item ? item.name : "施設を追加") : (item ? item.name : "避難先を追加"));
    form.querySelector("[data-delete]").hidden = !item;
    if (item) {
      form.name.value = item.name;
      form.lat.value = fmt(item.lat);
      form.lon.value = fmt(item.lon);
      form.is_hidden.checked = item.hidden;
      if (type === "facility") {
        form.category.value = item.category;
        form.description.value = item.description || "";
      } else {
        form.address.value = item.address || "";
        form.querySelectorAll("[name=kinds]").forEach((c) => { c.checked = item.kinds.includes(c.value); });
      }
      placeMarker(L.latLng(item.lat, item.lon));
      map.setView([item.lat, item.lon], Math.max(map.getZoom(), 16));
      setHint("");
    } else {
      setHint(type === "facility" ? "地図をクリックして、施設のピンの位置を選んでください" : "地図をクリックして、避難先の位置を選んでください");
    }
    if (type === "facility") {
      $("area-note").hidden = !(item && item.area && item.area.length >= 3);
      if (item && item.area && item.area.length >= 3) {
        L.polygon(item.area, { color: "#6A3FA0", weight: 1.5, dashArray: "4 6", fillOpacity: 0.06, interactive: false }).addTo(editLayer);
      }
      renderEntrances();
    }
    render();
  }

  function closeForm() {
    ["facility", "site"].forEach((t) => { formFor(t).hidden = true; });
    $("list-view").hidden = tab === "csv";
    editLayer.clearLayers();
    editing = null;
    setPickEntrance(false);
    setHint("");
  }

  // 位置の印（ドラッグで動かせる）
  function placeMarker(latlng) {
    const form = formFor(editing.type);
    form.lat.value = fmt(latlng.lat);
    form.lon.value = fmt(latlng.lng);
    const icon = editing.type === "facility" ? facilityIcon(form.category.value, false) : evacIcon(false);
    if (editing.marker) { editing.marker.setLatLng(latlng).setIcon(icon); return; }
    editing.marker = L.marker(latlng, { icon, draggable: true, zIndexOffset: 1000 }).addTo(editLayer);
    editing.marker.on("dragend", () => {
      const p = editing.marker.getLatLng();
      form.lat.value = fmt(p.lat);
      form.lon.value = fmt(p.lng);
    });
  }

  // 緯度・経度を直接入力したら、印を動かす
  ["facility", "site"].forEach((type) => {
    const form = formFor(type);
    const onInput = () => {
      if (!editing) return;
      const lat = parseFloat(form.lat.value), lon = parseFloat(form.lon.value);
      if (Number.isFinite(lat) && Number.isFinite(lon)) { placeMarker(L.latLng(lat, lon)); setHint(""); }
    };
    form.lat.addEventListener("change", onInput);
    form.lon.addEventListener("change", onInput);
    form.querySelector("[data-cancel]").addEventListener("click", () => { closeForm(); render(); });
    form.addEventListener("submit", (e) => { e.preventDefault(); save(); });
    form.querySelector("[data-delete]").addEventListener("click", remove);
  });
  $("facility-form").category.addEventListener("change", () => {
    if (editing && editing.marker) placeMarker(editing.marker.getLatLng());
  });

  map.on("click", (e) => {
    if (!editing) return;
    if (pickEntrance) {
      const n = editing.entrances.length + 1;
      editing.entrances.push({ name: n === 1 ? "正門" : `入口${n}`, lat: e.latlng.lat, lon: e.latlng.lng, note: "" });
      setPickEntrance(false);
      renderEntrances();
      const inputs = $("entrances").querySelectorAll("input");
      if (inputs.length) { inputs[inputs.length - 1].focus(); inputs[inputs.length - 1].select(); }
      return;
    }
    placeMarker(e.latlng);
    setHint("");
  });

  // ---------- 入口（施設だけ） ----------
  function setPickEntrance(on) {
    pickEntrance = on;
    $("entrance-add").classList.toggle("picking", on);
    $("entrance-add").textContent = on ? "地図をクリックして入口を置いてください（もう一度押すと取り消し）" : "＋ 入口を追加（地図をクリックして置く）";
    if (editing) setHint(on ? "地図をクリックして、入口の位置を選んでください" : "");
  }
  $("entrance-add").addEventListener("click", () => setPickEntrance(!pickEntrance));

  function renderEntrances() {
    const ul = $("entrances");
    ul.replaceChildren();
    editLayer.eachLayer((l) => { if (l.options.entrance) editLayer.removeLayer(l); });
    editing.entrances.forEach((e, i) => {
      const li = el("li", "admin-entrance");
      const name = el("input");
      name.type = "text";
      name.value = e.name;
      name.maxLength = 100;
      name.setAttribute("aria-label", `入口${i + 1}の名前`);
      name.addEventListener("input", () => { e.name = name.value; marker.setTooltipContent(name.value); });
      const remove = el("button", "admin-entrance-remove", "×");
      remove.type = "button";
      remove.title = "この入口を削除";
      remove.addEventListener("click", () => { editing.entrances.splice(i, 1); renderEntrances(); });
      li.append(el("span", "admin-entrance-num", String(i + 1)), name,
        el("span", "admin-entrance-pos", `${fmt(e.lat)}, ${fmt(e.lon)}`), remove);
      ul.appendChild(li);
      const marker = L.marker([e.lat, e.lon], { icon: entranceIcon(i + 1), draggable: true, entrance: true })
        .addTo(editLayer).bindTooltip(e.name);
      marker.on("dragend", () => {
        const p = marker.getLatLng();
        e.lat = p.lat; e.lon = p.lng;
        li.querySelector(".admin-entrance-pos").textContent = `${fmt(e.lat)}, ${fmt(e.lon)}`;
      });
    });
  }

  // ---------- 保存・削除 ----------
  async function save() {
    const { type, item } = editing;
    const form = formFor(type);
    const errEl = $(`${type}-error`);
    errEl.hidden = true;
    const lat = parseFloat(form.lat.value), lon = parseFloat(form.lon.value);
    if (!Number.isFinite(lat) || !Number.isFinite(lon)) {
      errEl.textContent = "地図をクリックして位置を選ぶか、緯度・経度を入力してください。";
      errEl.hidden = false;
      return;
    }
    const body = type === "facility"
      ? { name: form.name.value, category: form.category.value, lat, lon, description: form.description.value,
          hidden: form.is_hidden.checked, entrances: editing.entrances }
      : { name: form.name.value, address: form.address.value, lat, lon, hidden: form.is_hidden.checked,
          kinds: [...form.querySelectorAll("[name=kinds]:checked")].map((c) => c.value) };
    const base = type === "facility" ? "/admin/api/facilities" : "/admin/api/evac-sites";
    try {
      await api(item ? "PUT" : "POST", item ? `${base}/${encodeURIComponent(item.id)}` : base, body);
      toast(item ? "保存しました" : "追加しました");
      closeForm();
      load();
    } catch (e) {
      errEl.textContent = e.message;
      errEl.hidden = false;
      errEl.scrollIntoView({ block: "nearest" });
    }
  }

  async function remove() {
    const { type, item } = editing;
    const label = type === "facility" ? "施設（入口・敷地も含む）" : "避難先";
    if (!confirm(`「${item.name}」を削除します。元に戻せません。\n住民の画面から一時的に隠すだけなら「非公開にする」を使ってください。\n\n${label}を削除してよろしいですか？`)) return;
    const base = type === "facility" ? "/admin/api/facilities" : "/admin/api/evac-sites";
    try {
      await api("DELETE", `${base}/${encodeURIComponent(item.id)}`);
      toast("削除しました");
      closeForm();
      load();
    } catch (e) { toast(e.message, true); }
  }

  // ---------- CSV ----------
  async function loadCsvStatus() {
    try {
      const { db } = await api("GET", "/admin/api/csv");
      $("csv-counts").textContent = `いまの登録件数：施設 ${db.facilities}件（入口 ${db.entrances}件）・避難先 ${db.evac_sites}件`;
    } catch (e) { toast(e.message, true); }
  }
  function showResult(text, isError) {
    $("csv-result").textContent = text;
    $("csv-result").classList.toggle("error", !!isError);
  }
  $("csv-export").addEventListener("click", async () => {
    try {
      const r = await api("POST", "/admin/api/csv/export");
      showResult(`書き出しました：施設 ${r.facilities}件・入口 ${r.entrances}件・敷地 ${r.areas}件・避難先 ${r.evac_sites}件\n`
        + "seeds/ のファイルを commit・push すると、班のみんなと共有できます。");
    } catch (e) { showResult(e.message, true); }
  });
  $("csv-import").addEventListener("click", async () => {
    if (!confirm("seeds/ のCSVの内容で、施設・避難先をすべて入れ替えます。\n書き出していない変更は消えます。よろしいですか？")) return;
    try {
      const r = await api("POST", "/admin/api/csv/import");
      showResult(`読み込み直しました：施設 ${r.facilities}件・入口 ${r.entrances}件・敷地 ${r.areas}件・避難先 ${r.evac_sites}件`);
      loadCsvStatus();
    } catch (e) { showResult(e.message, true); }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && pickEntrance) setPickEntrance(false);
  });

  switchTab("facilities");
})();
