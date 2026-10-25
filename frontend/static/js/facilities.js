// 施設（駅・学校・観光地など）のピン。home.html の地図 `map` と goToFacility() を使う。
// 施設と入口は seeds/facilities.csv・seeds/entrances.csv で管理している。
(function () {
  const layer = L.layerGroup().addTo(map);
  const entranceLayer = L.layerGroup();  // 入口の小さな点（拡大したときだけ表示）
  const ENTRANCE_MIN_ZOOM = 17;

  // 種類ごとのアイコンの文字と色（CSS の .facility-icon-◯◯ に対応）
  const STYLES = {
    "駅":   { label: "駅", cls: "station" },
    "学校": { label: "学", cls: "school" },
    "観光地": { label: "観", cls: "tourism" },
    "避難所": { label: "避", cls: "shelter" },
  };

  function el(tag, className, text) {
    const e = document.createElement(tag);
    if (className) e.className = className;
    if (text !== undefined) e.textContent = text;
    return e;
  }

  function iconFor(category) {
    const s = STYLES[category] || { label: "●", cls: "other" };
    return L.divIcon({
      className: `facility-icon facility-icon-${s.cls}`,
      html: `<span>${s.label}</span>`,
      iconSize: [34, 34],
      iconAnchor: [17, 17],
      popupAnchor: [0, -16],
    });
  }

  function popupFor(f) {
    const box = el("div", "facility-popup");
    box.appendChild(el("p", "facility-category", f.category));
    box.appendChild(el("p", "facility-name", f.name));
    if (f.description) box.appendChild(el("p", "facility-desc", f.description));
    if (f.entrances.length) {
      box.appendChild(el("p", "facility-entrances",
        "入口：" + f.entrances.map((e) => e.name).join("・")));
    }
    const actions = el("div", "facility-actions");
    const from = el("button", "facility-from", "ここから出発");
    from.type = "button";
    from.addEventListener("click", () => window.startFromFacility(f));
    const go = el("button", "facility-go", "ここへ行く");
    go.type = "button";
    go.addEventListener("click", () => window.goToFacility(f));
    actions.appendChild(from);
    actions.appendChild(go);
    box.appendChild(actions);
    return box;
  }

  function updateEntranceLayer() {
    if (map.getZoom() >= ENTRANCE_MIN_ZOOM) {
      if (!map.hasLayer(entranceLayer)) entranceLayer.addTo(map);
    } else if (map.hasLayer(entranceLayer)) {
      map.removeLayer(entranceLayer);
    }
  }

  async function loadFacilities() {
    try {
      const res = await fetch("/api/facilities");
      if (res.status === 401) { location.href = "/login"; return; }
      if (!res.ok) return;
      const list = await res.json();
      layer.clearLayers();
      entranceLayer.clearLayers();
      list.forEach((f) => {
        L.marker([f.lat, f.lon], { icon: iconFor(f.category), zIndexOffset: 500, title: f.name })
          .addTo(layer)
          .bindPopup(() => popupFor(f), { minWidth: 230, maxWidth: 270 });
        f.entrances.forEach((e) => {
          L.circleMarker([e.lat, e.lon], {
            radius: 6, color: "#fff", weight: 2, fillColor: "#1F4F86", fillOpacity: 1,
          }).addTo(entranceLayer).bindTooltip(`${f.name}（${e.name}）`);
        });
      });
      updateEntranceLayer();
    } catch (e) { /* 通信失敗時は表示しない */ }
  }

  map.on("zoomend", updateEntranceLayer);
  loadFacilities();
})();
