"""現在地情報の取得モジュール

Android では plyer の GPS（GPS + ネットワーク測位）を使って現在地を取得する。
Windows では Windows の位置情報サービス（Wi-Fi 測位など）を使って現在地を取得する。
（winrt-Windows.Devices.Geolocation が必要。Windows の設定で位置情報をオンにしておく）
macOS / Linux など上記以外の環境では、固定座標を返すモック（疑似位置）で動作する。

使い方:
    service = LocationService(on_location=callback)
    service.start()   # 権限の確認 → 位置情報の取得開始
    service.stop()    # 取得停止
    service.last_location  # 最後に取得した位置（Location または None）
"""

import asyncio
import math
import random
import threading
import time
from dataclasses import dataclass, field

from kivy.clock import Clock, mainthread
from kivy.utils import platform

# PC で動かすときの疑似位置（東京駅）
DEFAULT_MOCK_LOCATION = (35.681236, 139.767125)


@dataclass
class Location:
    """1回分の位置情報"""

    lat: float  # 緯度
    lon: float  # 経度
    accuracy: float | None = None  # 誤差の目安 [m]
    altitude: float | None = None  # 高度 [m]
    speed: float | None = None  # 速度 [m/s]
    bearing: float | None = None  # 進行方向 [度]
    timestamp: float = field(default_factory=time.time)
    is_mock: bool = False  # True ならモック（疑似位置）


class LocationService:
    """現在地を継続的に取得し、更新のたびにコールバックで通知するクラス

    on_location(location: Location)        : 位置が更新されたときに呼ばれる
    on_status(status: str, message: str)   : 状態が変わったときに呼ばれる（任意）
        status の例: "started", "stopped", "permission-denied",
                     "provider-enabled", "provider-disabled", "mock"
    どちらのコールバックも Kivy のメインスレッドで呼ばれるので、
    中で UI を直接書き換えてよい。
    """

    def __init__(
        self,
        on_location,
        on_status=None,
        min_time_ms=1000,
        min_distance_m=1.0,
        mock_location=DEFAULT_MOCK_LOCATION,
    ):
        self.on_location = on_location
        self.on_status = on_status
        self.min_time_ms = min_time_ms  # 更新間隔の最小値 [ms]
        self.min_distance_m = min_distance_m  # この距離以上動いたら更新 [m]
        self.mock_location = mock_location

        self.last_location = None
        self._running = False
        self._gps = None
        self._mock_event = None
        self._win_stop = None  # Windows の取得スレッドを止めるためのイベント

    # ------------------------------------------------------------ 公開メソッド
    def start(self):
        if self._running:
            return
        if platform == "android":
            self._request_android_permissions()
        elif platform == "win":
            self._start_windows()
        else:
            self._start_mock("この環境では位置情報を取得できないため、モック位置を使用しています")

    def stop(self):
        if self._gps is not None:
            self._gps.stop()
            self._gps = None
        if self._mock_event is not None:
            self._mock_event.cancel()
            self._mock_event = None
        if self._win_stop is not None:
            self._win_stop.set()
            self._win_stop = None
        if self._running:
            self._running = False
            self._notify_status("stopped", "位置情報の取得を停止しました")

    @property
    def is_running(self):
        return self._running

    # --------------------------------------------------------- Android: 権限
    def _request_android_permissions(self):
        from android.permissions import Permission, check_permission, request_permissions

        permissions = [
            Permission.ACCESS_FINE_LOCATION,  # 精密な位置（GPS）
            Permission.ACCESS_COARSE_LOCATION,  # おおよその位置（ネットワーク）
        ]
        if all(check_permission(p) for p in permissions):
            self._start_gps()
        else:
            # 端末に許可ダイアログが表示される
            request_permissions(permissions, self._on_permission_result)

    def _on_permission_result(self, permissions, grants):
        # Android 12 以降はユーザーが「おおよその位置」だけ許可することもあるので、
        # どちらか一方でも許可されていれば取得を始める
        if any(grants):
            Clock.schedule_once(lambda dt: self._start_gps())
        else:
            self._notify_status("permission-denied", "位置情報の権限が許可されませんでした")

    # ----------------------------------------------------------- Android: GPS
    def _start_gps(self):
        from plyer import gps

        try:
            gps.configure(on_location=self._on_gps_location, on_status=self._on_gps_status)
            gps.start(minTime=self.min_time_ms, minDistance=self.min_distance_m)
        except NotImplementedError:
            # plyer が対応していない環境ではモックに切り替える
            self._start_mock("GPS が使えないため、モック位置を使用しています")
            return
        self._gps = gps
        self._running = True
        self._notify_status("started", "現在地を取得しています…")

    @mainthread
    def _on_gps_location(self, **kwargs):
        # plyer からは Java のスレッドで呼ばれるため @mainthread で UI スレッドに移す
        location = Location(
            lat=kwargs["lat"],
            lon=kwargs["lon"],
            accuracy=kwargs.get("accuracy"),
            altitude=kwargs.get("altitude"),
            speed=kwargs.get("speed"),
            bearing=kwargs.get("bearing"),
        )
        self._update(location)

    @mainthread
    def _on_gps_status(self, stype, status):
        # stype: "provider-enabled" / "provider-disabled" / "provider-status"
        # status: プロバイダ名（"gps", "network" など）
        if stype == "provider-disabled":
            message = f"位置情報({status})がオフです。端末の設定でオンにしてください"
        elif stype == "provider-enabled":
            message = f"位置情報({status})がオンになりました"
        else:
            message = f"{stype}: {status}"
        self._notify_status(stype, message)

    # ------------------------------------------------- Windows: 位置情報サービス
    def _start_windows(self):
        try:
            import winrt.windows.devices.geolocation  # noqa: F401
        except ImportError:
            self._start_mock(
                "winrt-Windows.Devices.Geolocation が未インストールのため、モック位置を使用しています"
            )
            return
        # Windows の API は await で待つ必要があるので、別スレッドで asyncio を回す
        stop_event = threading.Event()
        self._win_stop = stop_event
        self._running = True
        threading.Thread(target=self._run_windows, args=(stop_event,), daemon=True).start()
        self._notify_status("started", "現在地を取得しています…")

    def _run_windows(self, stop_event):
        asyncio.run(self._windows_loop(stop_event))

    async def _windows_loop(self, stop_event):
        from winrt.windows.devices.geolocation import GeolocationAccessStatus, Geolocator

        try:
            access = await Geolocator.request_access_async()
        except Exception as e:
            self._on_windows_error(stop_event, f"位置情報サービスを利用できません: {e}")
            return
        if access != GeolocationAccessStatus.ALLOWED:
            self._on_windows_denied(stop_event)
            return

        geolocator = Geolocator()
        geolocator.desired_accuracy_in_meters = 10
        while not stop_event.is_set():
            try:
                position = await geolocator.get_geoposition_async()
            except Exception as e:
                self._on_windows_error(stop_event, f"現在地を取得できませんでした: {e}")
            else:
                self._on_windows_position(stop_event, position)
            stop_event.wait(self.min_time_ms / 1000)

    @mainthread
    def _on_windows_position(self, stop_event, position):
        if stop_event.is_set():  # 停止後に届いた古い結果は捨てる
            return
        coordinate = position.coordinate
        point = coordinate.point.position
        location = Location(
            lat=point.latitude,
            lon=point.longitude,
            accuracy=_number_or_none(coordinate.accuracy),
            altitude=_number_or_none(point.altitude),
            speed=_number_or_none(coordinate.speed),
            bearing=_number_or_none(coordinate.heading),
        )
        self._update(location)

    @mainthread
    def _on_windows_denied(self, stop_event):
        if stop_event.is_set():
            return
        self._running = False
        self._win_stop = None
        self._notify_status(
            "permission-denied",
            "位置情報がオフです。Windows の設定 > プライバシーとセキュリティ > 位置情報 で"
            "オンにしてください",
        )

    @mainthread
    def _on_windows_error(self, stop_event, message):
        if stop_event.is_set():
            return
        self._notify_status("error", message)

    # ------------------------------------------------------------ PC: モック
    def _start_mock(self, message):
        self._running = True
        self._notify_status("mock", message)
        self._emit_mock(0)
        self._mock_event = Clock.schedule_interval(self._emit_mock, self.min_time_ms / 1000)

    def _emit_mock(self, dt):
        lat, lon = self.mock_location
        # 更新されていることが分かるよう、ごくわずかに位置を揺らす（数 m 程度）
        location = Location(
            lat=lat + random.uniform(-0.00003, 0.00003),
            lon=lon + random.uniform(-0.00003, 0.00003),
            accuracy=10.0,
            is_mock=True,
        )
        self._update(location)

    # ------------------------------------------------------------------ 共通
    def _update(self, location):
        self.last_location = location
        if self.on_location:
            self.on_location(location)

    def _notify_status(self, status, message):
        if self.on_status:
            self.on_status(status, message)


def _number_or_none(value):
    """値がない（None や NaN）ときは None に揃える"""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value
