"""OpenStreetMap を表示し、現在地をマーカーで示す基本の地図アプリ"""

import os

from kivy.app import App
from kivy.core.text import DEFAULT_FONT, LabelBase
from kivy.lang import Builder
from kivy_garden.mapview import MapMarker

from location_service import LocationService

# Kivy の標準フォントは日本語を表示できないため、日本語フォントを探して登録する
FONT_CANDIDATES = [
    os.path.join(os.path.dirname(__file__), "fonts", "NotoSansJP-Regular.ttf"),  # 同梱する場合
    "/system/fonts/NotoSansCJK-Regular.ttc",  # Android
    "C:/Windows/Fonts/meiryo.ttc",  # Windows
    "C:/Windows/Fonts/msgothic.ttc",  # Windows
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",  # macOS
]
for font_path in FONT_CANDIDATES:
    if os.path.exists(font_path):
        LabelBase.register(DEFAULT_FONT, font_path)
        break

KV = """
#:import MapView kivy_garden.mapview.MapView

BoxLayout:
    orientation: "vertical"

    FloatLayout:
        MapView:
            id: map
            lat: 35.681236
            lon: 139.767125
            zoom: 16
            size_hint: 1, 1
            pos_hint: {"x": 0, "y": 0}

        Label:
            text: "© OpenStreetMap contributors"
            color: 0, 0, 0, 1
            font_size: "11sp"
            size_hint: None, None
            size: self.texture_size[0] + dp(8), self.texture_size[1] + dp(4)
            pos_hint: {"right": 1, "y": 0}
            canvas.before:
                Color:
                    rgba: 1, 1, 1, 0.8
                Rectangle:
                    pos: self.pos
                    size: self.size

    BoxLayout:
        size_hint_y: None
        height: dp(72)
        padding: dp(8)
        spacing: dp(8)

        Label:
            id: status
            text: "現在地を取得しています…"
            font_size: "13sp"
            halign: "left"
            valign: "middle"
            text_size: self.size

        Button:
            text: "現在地"
            size_hint_x: None
            width: dp(96)
            on_release: app.center_on_current()
"""


class MapApp(App):
    title = "避難マップ"

    def build(self):
        self.root = Builder.load_string(KV)
        self.marker = None
        self.location_service = LocationService(
            on_location=self.on_location,
            on_status=self.on_status,
        )
        return self.root

    def on_start(self):
        self.location_service.start()

    # ---- 位置情報のコールバック -------------------------------------------
    def on_location(self, location):
        mapview = self.root.ids.map
        if self.marker is None:
            # 最初に位置が取れたときにマーカーを置き、地図をそこへ移動する
            self.marker = MapMarker(lat=location.lat, lon=location.lon)
            mapview.add_marker(self.marker)
            mapview.center_on(location.lat, location.lon)
        else:
            self.marker.lat = location.lat
            self.marker.lon = location.lon
            mapview.trigger_update(True)

        text = f"緯度 {location.lat:.6f} / 経度 {location.lon:.6f}"
        if location.accuracy is not None:
            text += f"\n精度 ±{location.accuracy:.0f} m"
        if location.is_mock:
            text += "（モック）"
        self.root.ids.status.text = text

    def on_status(self, status, message):
        # 位置がまだ取れていない間や、権限拒否・GPS オフ時にメッセージを出す
        if self.location_service.last_location is None or status in (
            "permission-denied",
            "provider-disabled",
        ):
            self.root.ids.status.text = message

    # ---- UI 操作 ---------------------------------------------------------
    def center_on_current(self):
        location = self.location_service.last_location
        if location is None:
            self.root.ids.status.text = "まだ現在地を取得できていません"
            return
        self.root.ids.map.center_on(location.lat, location.lon)

    # ---- アプリのライフサイクル ------------------------------------------
    def on_pause(self):
        # バックグラウンドに回ったら電池節約のため GPS を止める
        self.location_service.stop()
        return True

    def on_resume(self):
        self.location_service.start()

    def on_stop(self):
        self.location_service.stop()


if __name__ == "__main__":
    MapApp().run()
