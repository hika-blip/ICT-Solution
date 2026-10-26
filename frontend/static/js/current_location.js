// 現在地の表示。home.html の地図 `map` を使う。
// frontend/current_location_map.py（Kivy 版）と同じ流れをブラウザで行う：
//   起動時に取得開始 → 最初に位置が取れたら地図をそこへ移動 → 以後はマーカーだけ追従
//   「現在地」ボタンで地図を現在地へ戻す／画面が裏に回ったら電池節約のため取得を止める
(function () {
  const CURRENT_ZOOM = 16;
  const layer = L.layerGroup().addTo(map);
  let marker = null;       // 現在地の青い点
  let accuracyCircle = null;  // 誤差の目安の円
  let watchId = null;

  // 最後に取得した位置（home.html の「現在地」ボタンで出発地にするときに使う）
  window.currentLocation = null;

  // ---- 状態表示（地図の左下） ----
  const StatusControl = L.Control.extend({
    options: { position: "bottomleft" },
    onAdd() {
      const div = L.DomUtil.create("div", "current-status");
      div.textContent = "現在地を取得しています…";
      return div;
    },
  });
  const status = new StatusControl().addTo(map);
  function setStatus(text) {
    const div = status.getContainer();
    div.textContent = text;
    div.hidden = !text;
  }

  // 地図に表示できる範囲（/api/area の枠）の中か
  // （右下の「現在地」ボタンの処理は home.html にある。押すと現在地へ移動して出発地にする）
  function insideArea(latlng) {
    const bounds = map.options.maxBounds;
    return !bounds || bounds.contains(latlng);
  }

  // ---- 位置情報のコールバック ----
  function onLocation(pos) {
    const latlng = L.latLng(pos.coords.latitude, pos.coords.longitude);
    const accuracy = pos.coords.accuracy;
    window.currentLocation = latlng;
    // 歩行ログ（walklog.js）など、位置の更新を使う他の JS へ知らせる
    document.dispatchEvent(new CustomEvent("current-location", {
      detail: { latlng, accuracy, time: pos.timestamp },
    }));

    if (marker === null) {
      // 最初に位置が取れたときにマーカーを置き、地図をそこへ移動する
      marker = L.circleMarker(latlng, {
        radius: 8, color: "#fff", weight: 3, fillColor: "#1A73E8", fillOpacity: 1,
        interactive: false,
      }).addTo(layer);
      accuracyCircle = L.circle(latlng, {
        radius: accuracy, color: "#1A73E8", weight: 1, fillOpacity: 0.12, interactive: false,
      }).addTo(layer);
      if (insideArea(latlng)) {
        map.setView(latlng, CURRENT_ZOOM);
      } else {
        setStatus("現在地が地図の範囲外です");
        return;
      }
    } else {
      marker.setLatLng(latlng);
      accuracyCircle.setLatLng(latlng).setRadius(accuracy);
    }
    setStatus(insideArea(latlng) ? "" : "現在地が地図の範囲外です");
  }

  function onError(err) {
    if (err.code === err.PERMISSION_DENIED) {
      setStatus("位置情報の権限が許可されていません。ブラウザの設定で許可してください");
      stop();
    } else if (!window.currentLocation) {
      // 位置がまだ取れていない間だけメッセージを出す（一時的な失敗は無視する）
      setStatus("現在地を取得できませんでした");
    }
  }

  // ---- 取得の開始・停止 ----
  function start() {
    if (watchId !== null) return;
    if (!navigator.geolocation) { setStatus("この端末では現在地を取得できません"); return; }
    watchId = navigator.geolocation.watchPosition(onLocation, onError, {
      enableHighAccuracy: true, maximumAge: 1000, timeout: 20000,
    });
  }

  function stop() {
    if (watchId === null) return;
    navigator.geolocation.clearWatch(watchId);
    watchId = null;
  }

  // バックグラウンドに回ったら電池節約のため止め、戻ったら再開する
  // （歩行ログの記録中は止めない。walklog.js が window.walkRecording を true にする）
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) { if (!window.walkRecording) stop(); }
    else start();
  });

  start();
})();
