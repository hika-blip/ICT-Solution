// ハザードマップの重ね表示（緊急時モード）。home.html の地図 `map` に「重ねるハザードマップ」のタイルを重ねる。
// データのない場所のタイルは 404 が返るのが正常なので、通信が切れているときだけエラーの案内を出す。
(function () {
  const TILE_BASE = "https://disaportaldata.gsi.go.jp/raster/";
  const LEGEND_BASE = "https://disaportal.gsi.go.jp/hazardmap/copyright/img/";
  const ATTRIBUTION = '出典：<a href="https://disaportal.gsi.go.jp/hazardmap/copyright/opendata.html" target="_blank" rel="noopener">ハザードマップポータルサイト</a>';
  const OFFLINE_NOTE = "通信できないためハザードマップを表示できません。";
  // 高潮は日立市付近のデータがないので選択肢に入れない
  const HAZARDS = {
    flood: {
      tiles: ["01_flood_l2_shinsuishin_data"],
      legends: [["shinsui_legend3.png", "洪水の浸水深の凡例"]],
    },
    tsunami: {
      tiles: ["04_tsunami_newlegend_data"],
      legends: [["shinsui_legend3.png", "津波の浸水深の凡例"]],
    },
    landslide: {
      tiles: ["05_dosekiryukeikaikuiki", "05_kyukeishakeikaikuiki", "05_jisuberikeikaikuiki"],
      legends: [
        ["keikai_dosekiryu.png", "土石流の警戒区域の凡例"],
        ["keikai_kyukeisya.png", "急傾斜地の崩壊の警戒区域の凡例"],
        ["keikai_jisuberi.png", "地すべりの警戒区域の凡例"],
      ],
    },
  };

  const selectEl = document.getElementById("hazard-select");
  const noteEl = document.getElementById("hazard-note");
  const legendEl = document.getElementById("hazard-legend");
  const legendImagesEl = document.getElementById("hazard-legend-images");

  let layer = null;  // 表示中のハザードマップ

  function showOfflineNote() {
    if (navigator.onLine !== false) return;
    noteEl.textContent = OFFLINE_NOTE;
    noteEl.hidden = false;
  }

  function createLayer(hazard) {
    const tiles = hazard.tiles.map((path) => L.tileLayer(`${TILE_BASE}${path}/{z}/{x}/{y}.png`, {
      maxNativeZoom: 17,  // 18以上のタイルはないので、17のタイルを拡大して見せる
      maxZoom: 19,
      opacity: 0.65,
      zIndex: 10,
      attribution: ATTRIBUTION,
    }).on("tileerror", showOfflineNote));
    return L.layerGroup(tiles);
  }

  function showLegend(hazard) {
    legendImagesEl.replaceChildren(...hazard.legends.map(([file, alt]) => {
      const img = document.createElement("img");
      img.src = LEGEND_BASE + file;
      img.alt = alt;
      return img;
    }));
  }

  function show(key) {
    if (layer) map.removeLayer(layer);
    layer = null;
    const hazard = HAZARDS[key];
    noteEl.hidden = true;
    legendEl.hidden = !hazard;
    if (!hazard) {
      legendEl.open = false;
      legendImagesEl.replaceChildren();
      return;
    }
    showLegend(hazard);
    layer = createLayer(hazard).addTo(map);
  }

  selectEl.addEventListener("change", () => show(selectEl.value));

  // 平常時のタブに戻したときに呼ばれる（emergency.js）
  window.clearHazardMap = () => {
    selectEl.value = "";
    show("");
  };
})();
