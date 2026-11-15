// 緊急時モード。home.html の地図 `map` を使う。
// 現在地（取れなければ地図のタップ位置）から、近い避難場所3か所への経路を /api/evacuate/routes で探して地図に描く。
// 画面の表示は最小限にする：探す前は「避難場所を探す」ボタンだけ、探した後は避難先の距離・時間だけ。
// 平常時のタブに戻すと、避難経路を消して元の操作（経路検索・ピン・歩行ログ）に戻る。
(function () {
  const COLORS = ["#D93025", "#F29900", "#8E24AA"];  // 1位・2位・3位
  const tabNormal = document.getElementById("tab-normal");
  const tabEmergency = document.getElementById("tab-emergency");
  const panelNormal = document.getElementById("panel-normal");
  const panelEmergency = document.getElementById("panel-emergency");
  const messageEl = document.getElementById("evac-message");
  const listEl = document.getElementById("evac-list");
  const sliderEl = document.getElementById("evac-slider");
  const prevBtn = document.getElementById("evac-prev");
  const nextBtn = document.getElementById("evac-next");
  const searchBtn = document.getElementById("evac-search");

  let origin = null;     // 避難経路の出発地（現在地かタップした地点）
  let originMarker = null;
  let routes = [];       // [{ line, marker, label, item, data }]
  let searching = false;
  let current = 0;       // 選択中の経路の番号

  window.emergencyMode = false;

  // 検索中・エラーの案内。空文字なら行ごと隠す
  function setMessage(text) {
    messageEl.textContent = text;
    messageEl.hidden = !text;
  }

  // 避難先のピン（赤。中の数字は近い順）
  function siteIcon(rank) {
    return L.divIcon({
      className: "evac-pin", iconSize: [30, 40], iconAnchor: [15, 40], tooltipAnchor: [0, -36],
      html: '<svg viewBox="0 0 30 40" aria-hidden="true"><path d="M15 1C7.3 1 1 7.2 1 14.9 1 25 15 39 15 39s14-14 14-24.1C29 7.2 22.7 1 15 1z"'
        + ` fill="#D93025" stroke="#fff" stroke-width="2"/></svg><span>${rank}</span>`,
    });
  }

  function clearRoutes() {
    routes.forEach((r) => [r.line, r.marker, r.label].forEach((layer) => map.removeLayer(layer)));
    routes = [];
    current = 0;
    listEl.replaceChildren();
    sliderEl.hidden = true;
    searchBtn.hidden = false;  // 探す前の状態に戻す
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

  // 一覧（横スライド）の index 番目のカードが先頭に来るスクロール位置
  function slideLeft(index) {
    return routes[index].item.parentElement.offsetLeft - routes[0].item.parentElement.offsetLeft;
  }

  // スワイプが止まったら、いちばん手前に来たカードの経路を選ぶ
  let scrollTimer = null;
  listEl.addEventListener("scroll", () => {
    clearTimeout(scrollTimer);
    scrollTimer = setTimeout(() => {
      if (!routes.length) return;
      let nearest = 0;
      routes.forEach((_, i) => {
        if (Math.abs(slideLeft(i) - listEl.scrollLeft) < Math.abs(slideLeft(nearest) - listEl.scrollLeft)) nearest = i;
      });
      if (routes[nearest].item.getAttribute("aria-pressed") !== "true") select(nearest);
    }, 120);
  });

  // 選んだ経路を太く前面に出し、ほかは細く薄くする
  function select(index) {
    current = index;
    prevBtn.disabled = index === 0;
    nextBtn.disabled = index === routes.length - 1;
    routes.forEach((r, i) => {
      const on = i === index;
      r.line.setStyle({ weight: on ? 8 : 4, opacity: on ? 1 : 0.45 });
      if (on) r.line.bringToFront();
      // 距離・時間のラベルは選んだ経路の脇にだけ出す
      if (on) r.label.addTo(map); else map.removeLayer(r.label);
      r.marker.setZIndexOffset(on ? 1000 : 0);
      r.item.setAttribute("aria-pressed", on ? "true" : "false");
    });
    const r = routes[index];
    map.fitBounds(r.line.getBounds(), { padding: [30, 30] });
    listEl.scrollTo({ left: slideLeft(index), behavior: "smooth" });  // 地図側で選んだときも一覧をそのカードへ
  }

  function showRoutes(list) {
    clearRoutes();
    list.forEach((data, i) => {
      const color = COLORS[i % COLORS.length];
      const line = L.polyline(data.coords, { color, weight: 5, opacity: 0.9 }).addTo(map);
      const marker = L.marker([data.site.lat, data.site.lon], { icon: siteIcon(i + 1) }).addTo(map).bindTooltip(data.site.name);
      const label = routeLabel(data, color);  // 距離・時間（home.html）
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
      detail.textContent = routeNumbers(data);  // 距離・時間（home.html）
      const text = document.createElement("span");
      text.append(detail, name);

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
      routes.push({ line, marker, label, item: btn, data });
    });
    // 「避難場所を探す」ボタンの代わりに、避難先の距離・時間を出す
    setMessage("");
    searchBtn.hidden = true;
    sliderEl.hidden = false;
    select(0);
  }

  prevBtn.addEventListener("click", () => { if (current > 0) select(current - 1); });
  nextBtn.addEventListener("click", () => { if (current < routes.length - 1) select(current + 1); });

  // 経路を探して表示する。表示できたら true
  async function search() {
    if (searching) return false;
    if (!origin) {
      setMessage("現在地を取得できません。地図をタップして出発地を選んでください。");
      return false;
    }
    searching = true;
    clearRoutes();
    setMessage("検索中…");
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
      setMessage("");
      if (window.currentLocation) setOrigin(window.currentLocation);
    } else {
      clearAll();
      window.clearHazardMap();  // 「やり直す」では消さないので clearAll には入れない（hazard.js）
    }
    setTimeout(() => map.invalidateSize(), 0);  // パネルの高さが変わるので地図の大きさを取り直す
    document.dispatchEvent(new CustomEvent("emergency-mode-changed"));  // hazards.js が通れない道を出し入れする
  }

  tabNormal.addEventListener("click", () => setMode(false));
  tabEmergency.addEventListener("click", () => setMode(true));
  searchBtn.addEventListener("click", () => {
    // 現在地が取れているなら、いつでも最新の現在地から探し直す
    if (window.currentLocation) setOrigin(window.currentLocation);
    search();
  });
  document.getElementById("evac-reset").addEventListener("click", () => {
    clearAll();
    setMessage("");
    // 現在地が取れているなら、出発地の印は現在地に戻しておく
    if (window.currentLocation) setOrigin(window.currentLocation);
  });

  // 緊急時は地図のタップで出発地を決められる（現在地が取れないとき用）
  map.on("click", (e) => {
    if (!window.emergencyMode || window.pinMode || window.hazardMode) return;
    clearRoutes();
    setOrigin(e.latlng);
    search();
  });
})();
