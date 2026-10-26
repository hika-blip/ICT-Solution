// 緊急時モード。home.html の地図 `map` を使う。
// 現在地（取れなければ地図のタップ位置）から、近い避難場所3か所への経路を /api/evacuate/routes で探して地図に描く。
// 平常時のタブに戻すと、避難経路を消して元の操作（経路検索・ピン・歩行ログ）に戻る。
(function () {
  const COLORS = ["#D93025", "#F29900", "#8E24AA"];  // 1位・2位・3位
  const tabNormal = document.getElementById("tab-normal");
  const tabEmergency = document.getElementById("tab-emergency");
  const panelNormal = document.getElementById("panel-normal");
  const panelEmergency = document.getElementById("panel-emergency");
  const messageEl = document.getElementById("evac-message");
  const listEl = document.getElementById("evac-list");
  const START_MESSAGE = "「避難場所を探す」を押すと、現在地から近い避難場所3か所への経路を表示します。";

  let origin = null;     // 避難経路の出発地（現在地かタップした地点）
  let originMarker = null;
  let routes = [];       // [{ line, marker, item, data }]
  let searching = false;

  window.emergencyMode = false;

  function setMessage(text) { messageEl.textContent = text; }

  function clearRoutes() {
    routes.forEach((r) => { map.removeLayer(r.line); map.removeLayer(r.marker); });
    routes = [];
    listEl.replaceChildren();
  }

  function clearAll() {
    clearRoutes();
    if (originMarker) map.removeLayer(originMarker);
    originMarker = origin = null;
  }

  function setOrigin(latlng) {
    origin = latlng;
    if (originMarker) map.removeLayer(originMarker);
    originMarker = L.marker(latlng).addTo(map).bindTooltip("出発地", { permanent: true });
  }

  // 選んだ経路を太く前面に出し、ほかは細く薄くする
  function select(index) {
    routes.forEach((r, i) => {
      const on = i === index;
      r.line.setStyle({ weight: on ? 8 : 4, opacity: on ? 1 : 0.45 });
      if (on) r.line.bringToFront();
      r.item.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const r = routes[index];
    map.fitBounds(r.line.getBounds(), { padding: [30, 30] });
    r.marker.openTooltip();
    const d = r.data;
    setMessage(`${d.site.name} へ約 ${Math.round(d.distance_m)} m${walkTimeText(d.distance_m)}`);
  }

  function showRoutes(list) {
    clearRoutes();
    list.forEach((data, i) => {
      const color = COLORS[i % COLORS.length];
      const line = L.polyline(data.coords, { color, weight: 5, opacity: 0.9 }).addTo(map);
      const marker = L.marker([data.site.lat, data.site.lon]).addTo(map).bindTooltip(data.site.name);
      line.on("click", () => select(i));
      marker.on("click", () => select(i));

      const rank = document.createElement("span");
      rank.className = "evac-rank";
      rank.textContent = i + 1;
      const name = document.createElement("span");
      name.className = "evac-name";
      name.textContent = data.site.name;
      const detail = document.createElement("span");
      detail.className = "evac-detail";
      detail.textContent = `${data.site.kinds.join("・")}　約 ${Math.round(data.distance_m)} m${walkTimeText(data.distance_m)}`;
      const text = document.createElement("span");
      text.append(name, detail);

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "evac-item";
      btn.style.setProperty("--evac-color", color);
      btn.setAttribute("aria-pressed", "false");
      btn.append(rank, text);
      btn.addEventListener("click", () => select(i));
      const li = document.createElement("li");
      li.append(btn);
      listEl.append(li);
      routes.push({ line, marker, item: btn, data });
    });
    select(0);
  }

  // 経路を探して表示する。表示できたら true
  async function search() {
    if (searching) return false;
    if (!origin) {
      setMessage("現在地を取得できていません。地図をタップして出発地を選んでください。");
      return false;
    }
    searching = true;
    clearRoutes();
    setMessage("避難場所を検索しています…");
    try {
      const res = await fetch(`/api/evacuate/routes?lat=${origin.lat}&lon=${origin.lng}&count=3`);
      if (res.status === 401) { location.href = "/login"; return false; }
      const data = await res.json();
      if (!res.ok) {
        setMessage((data.error || "経路を取得できませんでした。") + " 地図をタップして出発地を選び直せます。");
        return false;
      }
      showRoutes(data.routes);
      return true;
    } catch (e) {
      setMessage("通信に失敗しました。");
      return false;
    } finally {
      searching = false;
    }
  }

  function setMode(emergency) {
    if (window.emergencyMode === emergency) return;
    window.emergencyMode = emergency;
    tabNormal.setAttribute("aria-selected", emergency ? "false" : "true");
    tabEmergency.setAttribute("aria-selected", emergency ? "true" : "false");
    panelNormal.hidden = emergency;
    panelEmergency.hidden = !emergency;
    if (emergency) {
      reset();  // 平常時の経路・印を消す（home.html）
      setMessage(START_MESSAGE);
      if (window.currentLocation) setOrigin(window.currentLocation);
    } else {
      clearAll();
    }
    setTimeout(() => map.invalidateSize(), 0);  // パネルの高さが変わるので地図の大きさを取り直す
  }

  tabNormal.addEventListener("click", () => setMode(false));
  tabEmergency.addEventListener("click", () => setMode(true));
  document.getElementById("evac-search").addEventListener("click", () => {
    // 現在地が取れているなら、いつでも最新の現在地から探し直す
    if (window.currentLocation) setOrigin(window.currentLocation);
    search();
  });
  document.getElementById("evac-reset").addEventListener("click", () => {
    clearAll();
    setMessage(START_MESSAGE);
  });

  // ---- テストモード：地図の範囲（/api/area）の中のランダムな地点から探す ----
  // 道から遠い地点（範囲内でも海や山）は経路が出ないので、出るまで数回やり直す
  const RANDOM_TRIES = 15;
  document.getElementById("evac-random").addEventListener("click", async () => {
    const area = map.options.maxBounds;
    if (!area) { setMessage("地図の範囲を取得できていません。"); return; }
    for (let i = 0; i < RANDOM_TRIES; i++) {
      const lat = area.getSouth() + Math.random() * (area.getNorth() - area.getSouth());
      const lon = area.getWest() + Math.random() * (area.getEast() - area.getWest());
      setOrigin(L.latLng(lat, lon));
      if (await search()) {
        setMessage(`【テスト】ランダムな地点(${lat.toFixed(5)}, ${lon.toFixed(5)})から　` + messageEl.textContent);
        return;
      }
      if (searching) return;  // 検索中のボタン連打
    }
    setMessage("【テスト】経路の出る地点が見つかりませんでした。もう一度押してください。");
  });

  // 緊急時は地図のタップで出発地を決められる（現在地が取れないとき用）
  map.on("click", (e) => {
    if (!window.emergencyMode || window.pinMode) return;
    clearRoutes();
    setOrigin(e.latlng);
    search();
  });
})();
