from __future__ import annotations

from datetime import datetime
from dataclasses import asdict
import json
from pathlib import Path
import sys
from threading import Timer
from typing import Any

# ウィンドウの親子関係を定義する
import ctypes
import os
import time
import threading
from ctypes import wintypes

from alias_rewrite.utau_plugin import load_utau_plugin_context

from .audio import AudioPlayer
from .controller import (
    apply as apply_request,
    apply_summary as apply_summary_request,
    information_detail as information_detail_request,
    preview as preview_request,
    search_ust as search_ust_request,
    validate_preview_rows,
)
from .dto import (
    AppSettings,
    ApplyRequest,
    ExcludeSettings,
    PreviewRequest,
    PreviewResponse,
    PreviewRowDto,
    PreviewSummary,
    ReplacementRuleDto,
    RewriteSettings,
    SortSpec,
    dto_to_dict,
)
from .settings_store import load_app_settings, save_app_settings
from .theme_store import load_color_schemes
from .runtime_paths import default_logs_dir


# 依存ライブラリを処理する
def _missing_dependency() -> int:
    web_dir = Path(__file__).with_name("web")
    print("pywebview is not installed.")
    print("Install it with: py -m pip install pywebview")
    print(f"You can still inspect the shared static GUI at: {web_dir / 'index.html'}")
    return 1


try:
    import webview
except ModuleNotFoundError:  # pragma: no cover
    raise SystemExit(_missing_dependency())


# ファイルダイアログを処理する
def _file_dialog(name: str, fallback):
    dialog_enum = getattr(webview, "FileDialog", None)
    if dialog_enum is None:
        return fallback
    return getattr(dialog_enum, name, fallback)


WEB_DIR = Path(__file__).with_name("web")
_MAIN_NATIVE_MIN_SIZE = (1, 1)
_INFORMATION_WINDOW_SIZE = (500, 400)
_INFORMATION_WINDOW_MIN_SIZE = (400, 300)
_main_min_size = (0, 0)
_main_hwnd: int | None = None
_main_old_wndproc = None
_main_wndproc = None

# ウィンドウリサイズ診断ログは通常OFF。
# 一時的に有効化する場合はこの値を True にするか、
# 環境変数 ALIASCALE_WINDOW_GEOMETRY_DEBUG=1 を指定する。
_WINDOW_GEOMETRY_DEBUG_DEFAULT = False
_window_geometry_debug_enabled = _WINDOW_GEOMETRY_DEBUG_DEFAULT


# ウィンドウサイズ調整の診断ログを出力する
def _set_window_geometry_debug_enabled(enabled: bool) -> None:
    global _window_geometry_debug_enabled
    _window_geometry_debug_enabled = bool(enabled)


def _window_geometry_debug_log(event: str, **values: Any) -> None:
    env_enabled = os.environ.get("ALIASCALE_WINDOW_GEOMETRY_DEBUG", "").lower() in {"1", "true", "yes", "on"}
    if not (_window_geometry_debug_enabled or env_enabled):
        return
    try:
        log_dir = default_logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"aliascale_window_geometry_{datetime.now().strftime('%Y%m%d')}.log"
        payload = {
            "time": datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
            **values,
        }
        with log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


# ウィンドウの親子関係を定義する
user32 = ctypes.WinDLL("user32", use_last_error=True)

GWLP_HWNDPARENT = -8
GWL_WNDPROC = -4
WM_GETMINMAXINFO = 0x0024
MONITOR_DEFAULTTONEAREST = 0x00000002

SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040

HWND_TOP = 0


EnumWindowsProc = ctypes.WINFUNCTYPE(
    wintypes.BOOL,
    wintypes.HWND,
    wintypes.LPARAM,
)

user32.EnumWindows.argtypes = [EnumWindowsProc, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int

user32.GetWindowTextW.argtypes = [
    wintypes.HWND,
    wintypes.LPWSTR,
    ctypes.c_int,
]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetWindowThreadProcessId.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.DWORD),
]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD

user32.SetWindowPos.argtypes = [
    wintypes.HWND,
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_uint,
]
user32.SetWindowPos.restype = wintypes.BOOL

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL

try:
    GetDpiForWindow = user32.GetDpiForWindow
    GetDpiForWindow.argtypes = [wintypes.HWND]
    GetDpiForWindow.restype = ctypes.c_uint
except AttributeError:
    GetDpiForWindow = None


if ctypes.sizeof(ctypes.c_void_p) == 8:
    SetWindowLongPtr = user32.SetWindowLongPtrW
    GetWindowLongPtr = user32.GetWindowLongPtrW
else:
    SetWindowLongPtr = user32.SetWindowLongW
    GetWindowLongPtr = user32.GetWindowLongW

SetWindowLongPtr.argtypes = [
    wintypes.HWND,
    ctypes.c_int,
    ctypes.c_void_p,
]
SetWindowLongPtr.restype = ctypes.c_void_p

GetWindowLongPtr.argtypes = [
    wintypes.HWND,
    ctypes.c_int,
]
GetWindowLongPtr.restype = ctypes.c_void_p

CallWindowProc = user32.CallWindowProcW
CallWindowProc.argtypes = [
    ctypes.c_void_p,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
CallWindowProc.restype = wintypes.LPARAM

DefWindowProc = user32.DefWindowProcW
DefWindowProc.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
DefWindowProc.restype = wintypes.LPARAM

WindowProc = ctypes.WINFUNCTYPE(
    wintypes.LPARAM,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class MINMAXINFO(ctypes.Structure):
    _fields_ = [
        ("ptReserved", wintypes.POINT),
        ("ptMaxSize", wintypes.POINT),
        ("ptMaxPosition", wintypes.POINT),
        ("ptMinTrackSize", wintypes.POINT),
        ("ptMaxTrackSize", wintypes.POINT),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    ]


user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE

user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFO)]
user32.GetMonitorInfoW.restype = wintypes.BOOL


def _get_window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""

    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _hwnd_value(hwnd) -> int:
    value = getattr(hwnd, "value", hwnd)
    return int(value or 0)


def find_hwnd_by_title(title: str, *, pid: int | None = None) -> int | None:

    # 指定タイトルのトップレベルウィンドウHWNDを探す

    matched: list[int] = []

    if pid is None:
        pid = os.getpid()

    def callback(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))

        if window_pid.value != pid:
            return True

        text = _get_window_text(hwnd)
        if text == title:
            matched.append(hwnd)
            return False

        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)

    if not matched:
        return None

    return matched[0]


def _visible_pid_windows(*, pid: int | None = None) -> list[tuple[int, str, int]]:
    if pid is None:
        pid = os.getpid()
    windows: list[tuple[int, str, int]] = []

    def callback(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True

        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value != pid:
            return True

        rect = wintypes.RECT()
        area = 0
        if user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            area = max(0, int(rect.right - rect.left)) * max(0, int(rect.bottom - rect.top))
        windows.append((_hwnd_value(hwnd), _get_window_text(hwnd), area))
        return True

    user32.EnumWindows(EnumWindowsProc(callback), 0)
    return windows


def _find_main_hwnd(window) -> int | None:
    title = getattr(window, "title", None) or "AliaScale"
    exact = find_hwnd_by_title(title)
    if exact:
        return exact

    windows = _visible_pid_windows()
    alia_windows = [
        item for item in windows
        if "AliaScale" in item[1] and "Settings" not in item[1] and "INFORMATION" not in item[1]
    ]
    if alia_windows:
        return max(alia_windows, key=lambda item: item[2])[0]
    if windows:
        return max(windows, key=lambda item: item[2])[0]
    return None


def set_owned_window(child_hwnd: int, owner_hwnd: int) -> None:

    # child_hwnd を owner_hwnd の owned window にする。

    if not child_hwnd:
        raise RuntimeError("child_hwnd is empty")

    if not owner_hwnd:
        raise RuntimeError("owner_hwnd is empty")

    SetWindowLongPtr(
        wintypes.HWND(child_hwnd),
        GWLP_HWNDPARENT,
        ctypes.c_void_p(owner_hwnd),
    )

    # Z-orderを即時反映させる
    ok = user32.SetWindowPos(
        wintypes.HWND(child_hwnd),
        wintypes.HWND(HWND_TOP),
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
    )

    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


def set_owned_window_by_title(
    *,
    child_title: str,
    owner_title: str,
    timeout_sec: float = 3.0,
) -> bool:
    # pywebviewのウィンドウ生成直後はWin32 HWNDがまだ見つからない場合があるので少しリトライする
    deadline = time.time() + timeout_sec
    pid = os.getpid()

    while time.time() < deadline:
        owner_hwnd = find_hwnd_by_title(owner_title, pid=pid)
        child_hwnd = find_hwnd_by_title(child_title, pid=pid)

        if owner_hwnd and child_hwnd:
            set_owned_window(child_hwnd, owner_hwnd)
            return True

        time.sleep(0.05)

    return False

# データを処理する
def _main_window_wndproc(hwnd, message, wparam, lparam):
    if message == WM_GETMINMAXINFO:
        try:
            min_width, min_height = _main_min_size
            if min_width > 0 and min_height > 0:
                info = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
                info.ptMinTrackSize.x = max(info.ptMinTrackSize.x, int(min_width))
                info.ptMinTrackSize.y = max(info.ptMinTrackSize.y, int(min_height))
                return 0
        except Exception:
            pass
    if _main_old_wndproc:
        return CallWindowProc(_main_old_wndproc, hwnd, message, wparam, lparam)
    return DefWindowProc(hwnd, message, wparam, lparam)


def _install_main_min_size_hook(window) -> bool:
    global _main_hwnd, _main_old_wndproc, _main_wndproc
    if _main_hwnd and _main_old_wndproc:
        return True

    hwnd = _find_main_hwnd(window)
    if not hwnd:
        return False
    hwnd = _hwnd_value(hwnd)

    _main_wndproc = WindowProc(_main_window_wndproc)
    ctypes.set_last_error(0)
    previous = SetWindowLongPtr(
        wintypes.HWND(hwnd),
        GWL_WNDPROC,
        ctypes.cast(_main_wndproc, ctypes.c_void_p).value,
    )
    if not previous and ctypes.get_last_error():
        _main_wndproc = None
        return False

    _main_hwnd = _hwnd_value(hwnd)
    _main_old_wndproc = previous
    return True


def _window_dpi_scale(hwnd: int | None) -> float:
    hwnd = _hwnd_value(hwnd)
    if not hwnd or GetDpiForWindow is None:
        return 1.0
    try:
        dpi = int(GetDpiForWindow(wintypes.HWND(hwnd)) or 96)
    except Exception:
        return 1.0
    return max(0.25, dpi / 96)


def _logical_to_physical_size(width: int, height: int, hwnd: int | None) -> tuple[int, int]:
    scale = _window_dpi_scale(hwnd)
    return max(1, round(width * scale)), max(1, round(height * scale))


def _work_area_logical_size(hwnd: int | None) -> tuple[int | None, int | None]:
    hwnd = _hwnd_value(hwnd)
    if not hwnd:
        return (None, None)
    try:
        monitor = user32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return (None, None)
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return (None, None)
        scale = _window_dpi_scale(hwnd)
        width = max(1, int((info.rcWork.right - info.rcWork.left) / scale))
        height = max(1, int((info.rcWork.bottom - info.rcWork.top) / scale))
        return width, height
    except Exception:
        return (None, None)


def _main_window_metrics(window, current_width: int, current_height: int) -> tuple[int, int, int, int]:
    hwnd = _main_hwnd or _find_main_hwnd(window)
    if hwnd:
        try:
            window_rect = wintypes.RECT()
            client_rect = wintypes.RECT()
            if (
                user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(window_rect))
                and user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(client_rect))
            ):
                scale = _window_dpi_scale(hwnd)
                window_width = max(1, round((window_rect.right - window_rect.left) / scale))
                window_height = max(1, round((window_rect.bottom - window_rect.top) / scale))
                client_width = max(1, round((client_rect.right - client_rect.left) / scale))
                client_height = max(1, round((client_rect.bottom - client_rect.top) / scale))
                return (
                    window_width,
                    window_height,
                    max(0, window_width - client_width),
                    max(0, window_height - client_height),
                )
        except Exception:
            pass

    actual_width = int(getattr(window, "width", current_width) or current_width)
    actual_height = int(getattr(window, "height", current_height) or current_height)
    chrome_width = max(0, actual_width - int(current_width or 0)) if current_width else 0
    chrome_height = max(0, actual_height - int(current_height or 0)) if current_height else 0
    return actual_width, actual_height, chrome_width, chrome_height


# Win32 APIでメインウィンドウを直接リサイズする
def _native_resize_window(hwnd: int | None, logical_width: int, logical_height: int) -> dict[str, Any]:
    hwnd = _hwnd_value(hwnd)
    if not hwnd:
        return {"attempted": False, "ok": False, "reason": "missing_hwnd"}

    physical_width, physical_height = _logical_to_physical_size(logical_width, logical_height, hwnd)
    flags = SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW
    ctypes.set_last_error(0)
    ok = user32.SetWindowPos(
        wintypes.HWND(hwnd),
        wintypes.HWND(0),
        0,
        0,
        int(physical_width),
        int(physical_height),
        flags,
    )
    error = ctypes.get_last_error() if not ok else 0
    return {
        "attempted": True,
        "ok": bool(ok),
        "error": int(error),
        "logical_width": int(logical_width),
        "logical_height": int(logical_height),
        "physical_width": int(physical_width),
        "physical_height": int(physical_height),
        "flags": int(flags),
        "hwnd": int(hwnd),
    }


def _tuple_dataclass(cls, values: Any) -> tuple:
    if not values:
        return ()
    return tuple(cls(**value) if isinstance(value, dict) else value for value in values)


# 設定を処理する
# GUI設定データをアプリ設定へ変換する
def _app_settings(data: dict | None) -> AppSettings:
    data = dict(data or {})
    tuple_fields = {"excluded_call_key_moras", "auto_wav_excluded_moras", "related_file_patterns"}
    for field_name in tuple_fields:
        value = data.get(field_name)
        if isinstance(value, str):
            data[field_name] = tuple(part.strip() for part in value.split(",") if part.strip())
        elif isinstance(value, list):
            data[field_name] = tuple(str(part).strip() for part in value if str(part).strip())
    if "ui_scale" in data:
        try:
            data["ui_scale"] = float(data["ui_scale"])
        except (TypeError, ValueError):
            data["ui_scale"] = 1.0
    if data.get("theme") == "dark":
        data["theme"] = "defoko_dark"
    elif data.get("theme") == "light":
        data["theme"] = "defoko_light"
    if "backup_max_count" in data:
        try:
            data["backup_max_count"] = max(1, min(int(data["backup_max_count"]), 999999))
        except (TypeError, ValueError):
            data["backup_max_count"] = 30
    if data.get("wav_edit_mode") in {"representative", "empty_alias_only"}:
        data["wav_edit_mode"] = "allow"
    if str(data.get("backup_root") or "").strip().lower() == "backups":
        data["backup_root"] = "backup"
    if not str(data.get("backup_root") or "").strip():
        data.pop("backup_root", None)
    return AppSettings(**{key: value for key, value in data.items() if key in AppSettings.__dataclass_fields__})


# 設定を処理する
def _rewrite_settings(data: dict | None) -> RewriteSettings:
    data = dict(data or {})
    exclude = data.get("exclude")
    if isinstance(exclude, dict):
        data["exclude"] = ExcludeSettings(**{key: value for key, value in exclude.items() if key in ExcludeSettings.__dataclass_fields__})
    data["replacement_rules"] = _tuple_dataclass(ReplacementRuleDto, data.get("replacement_rules"))
    data["sort"] = _tuple_dataclass(SortSpec, data.get("sort"))
    return RewriteSettings(**{key: value for key, value in data.items() if key in RewriteSettings.__dataclass_fields__})


# 要求を生成する
def _preview_request(data: dict) -> PreviewRequest:
    return PreviewRequest(
        voice_dir=data.get("voice_dir", ""),
        oto_path=data.get("oto_path", ""),
        mrq_path=data.get("mrq_path", ""),
        frequency_source=data.get("frequency_source", data.get("rewrite", {}).get("frequency_source", "mrq")),
        csv_path=data.get("csv_path", ""),
        ust_root=data.get("ust_root", ""),
        selected_ust_paths=tuple(data.get("selected_ust_paths") or ()),
        ust_selection_known=bool(data.get("ust_selection_known", False)),
        utau_plugin_temp_path=data.get("utau_plugin_temp_path", ""),
        rewrite=_rewrite_settings(data.get("rewrite")),
        settings=_app_settings(data.get("settings")),
    )


# 要求を反映する
def _apply_request(data: dict) -> ApplyRequest:
    rows = _tuple_dataclass(PreviewRowDto, data.get("rows"))
    return ApplyRequest(
        voice_dir=data.get("voice_dir", ""),
        oto_path=data.get("oto_path", ""),
        rows=rows,
        mrq_path=data.get("mrq_path", ""),
        frequency_source=data.get("frequency_source", data.get("rewrite", {}).get("frequency_source", "mrq")),
        ust_root=data.get("ust_root", ""),
        selected_ust_paths=tuple(data.get("selected_ust_paths") or ()),
        utau_plugin_temp_path=data.get("utau_plugin_temp_path", ""),
        rewrite=_rewrite_settings(data.get("rewrite")),
        settings=_app_settings(data.get("settings")),
    )


# JSONで安全に渡せる値へ変換する
def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
        return None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


# 設定画面から呼び出す処理をまとめる
class SettingsApi:
    # 初期状態を設定する
    def __init__(self, owner: "Api") -> None:
        self._owner = owner

    # 設定を反映する
    def apply_settings(self, settings: dict | None = None) -> bool:
        return self._owner.apply_settings(settings or {})

    # 設定を取り消す
    def cancel_settings(self) -> bool:
        return self._owner.close_settings_window()

    # 設定を取得する
    def get_settings(self) -> dict:
        return dto_to_dict(self._owner.settings)

    # 配色定義一覧を取得する
    def get_color_schemes(self) -> list[dict]:
        return self._owner.get_color_schemes()

    # ファイルを選択する
    def choose_file(self, kind: str = "") -> str:
        return self._owner.choose_file(kind, parent_window=self._owner._settings_window)

    # 値を前面へ戻す
    def raise_related_windows(self) -> bool:
        return self._owner.raise_related_windows()


# ウェブGUIから呼び出す処理をまとめる
class InformationApi:
    # 初期状態を設定する
    def __init__(self, owner: "Api") -> None:
        self._owner = owner

    # INFORMATION詳細データを取得する
    def get_detail(self, payload: dict | None = None) -> dict:
        return self._owner.get_information_detail()

    # 配色定義一覧を取得する
    def get_color_schemes(self, _payload: dict | None = None) -> list[dict]:
        return self._owner.get_color_schemes()

    # INFORMATION詳細ウィンドウを閉じる
    def close_information_window(self, _payload: dict | None = None) -> bool:
        return self._owner.close_information_window()

    # 音声を再生する
    def play_audio(self, payload: dict | None = None) -> dict:
        return self._owner.play_audio(payload)

    # 音声を停止する
    def stop_audio(self, payload: dict | None = None) -> dict:
        return self._owner.stop_audio(payload)

    # 原音フォルダを開く
    def open_voice_folder(self, payload: dict | None = None) -> bool:
        return self._owner.open_voice_folder(payload)

    # 関連ウィンドウを前面へ戻す
    def raise_related_windows(self, _payload: dict | None = None) -> bool:
        return self._owner.raise_related_windows()


class Api:
    # 初期状態を設定する
    def __init__(self, utau_plugin_temp_path: str | Path | None = None) -> None:
        self._main_window = None
        self._settings_window = None
        self._information_window = None
        self._information_payload: dict[str, Any] | None = None
        self._closing_settings_window = False
        self._closing_main_window = False
        self._main_close_prompt_active = False
        self._has_unapplied_changes = False
        self.settings = load_app_settings()
        self._audio = AudioPlayer()
        self._plugin_context = load_utau_plugin_context(utau_plugin_temp_path)

    # ウィンドウを設定する
    def set_main_window(self, window) -> None:
        self._main_window = window

    # 設定を取得する
    def get_settings(self, _payload: dict | None = None) -> dict:
        return dto_to_dict(self.settings)

    # 配色定義一覧を取得する
    def get_color_schemes(self, _payload: dict | None = None) -> list[dict]:
        return load_color_schemes()

    # 連携情報を取得する
    def get_plugin_context(self, _payload: dict | None = None) -> dict:
        context = self._plugin_context
        if context is None:
            return {"active": False, "temp_path": "", "voice_dir": "", "note_count": 0}
        return {
            "active": True,
            "temp_path": str(context.temp_path),
            "voice_dir": context.voice_dir,
            "note_count": context.note_count,
        }

    # INFORMATION詳細データを取得する
    def get_information_detail(self, payload: dict | None = None) -> dict:
        if payload is not None:
            self._information_payload = payload
        payload = self._information_payload or {}
        if self._plugin_context is not None:
            payload = dict(payload)
            payload["utau_plugin_temp_path"] = str(self._plugin_context.temp_path)
        payload = dict(payload)
        payload["settings"] = dto_to_dict(self.settings)
        try:
            selected_line = payload.get("selected_line_number")
            selected_line_number = int(selected_line) if selected_line not in (None, "") else None
            return _json_safe(
                information_detail_request(
                    _apply_request(payload),
                    selected_line_number=selected_line_number,
                    usage_stale=bool(payload.get("usage_stale", False)),
                )
            )
        except Exception as exc:
            return {"state": "error", "errors": [str(exc)]}

    # INFORMATION詳細ウィンドウを更新する
    def update_information_window(self, payload: dict | None = None) -> bool:
        if payload is not None:
            self._information_payload = payload
        if self._information_window is None:
            return True
        detail = self.get_information_detail()
        script = f"window.renderInformationDetailFromHost && window.renderInformationDetailFromHost({json.dumps(detail, ensure_ascii=False)})"
        try:
            self._information_window.evaluate_js(script)
        except Exception:
            return False
        return True

    # 値を前面へ戻す
    def raise_related_windows(self, _payload: dict | None = None) -> bool:
        for window in (self._main_window, self._settings_window, self._information_window):
            if window is None:
                continue
            for method_name in ("show", "restore"):
                method = getattr(window, method_name, None)
                if method:
                    try:
                        method()
                    except Exception:
                        pass
        return True

    # 値を生成する
    def preview(self, payload: dict) -> dict:
        try:
            if self._plugin_context is not None:
                payload = dict(payload)
                payload["utau_plugin_temp_path"] = str(self._plugin_context.temp_path)
            return dto_to_dict(preview_request(_preview_request(payload)))
        except Exception as exc:
            return dto_to_dict(
                PreviewResponse(
                    rows=(),
                    summary=PreviewSummary(rows=0, edits=0, warnings=0, danger=1, can_apply=False),
                    information={},
                )
            ) | {"errors": [str(exc)]}

    # UST蜿ら・蝗樊焚繧貞・逅・☆繧・
    def search_ust(self, payload: dict) -> dict:
        try:
            if self._plugin_context is not None:
                payload = dict(payload)
                payload["utau_plugin_temp_path"] = str(self._plugin_context.temp_path)
            return dto_to_dict(search_ust_request(_preview_request(payload)))
        except Exception as exc:
            return dto_to_dict(
                PreviewResponse(
                    rows=(),
                    summary=PreviewSummary(rows=0, edits=0, warnings=0, danger=1, can_apply=False),
                    information={},
                )
            ) | {"errors": [str(exc)]}

    # 値を反映する
    def apply_summary(self, payload: dict) -> dict:
        try:
            if self._plugin_context is not None:
                payload = dict(payload)
                payload["utau_plugin_temp_path"] = str(self._plugin_context.temp_path)
            return apply_summary_request(_apply_request(payload))
        except Exception as exc:
            return {
                "voice_dir": payload.get("voice_dir", ""),
                "oto_changed_rows": 0,
                "ust_write_count": 0,
                "wav_file_count": 0,
                "related_file_count": 0,
                "errors": [str(exc)],
            }

    # 値を反映する
    def apply(self, payload: dict) -> dict:
        try:
            if self._plugin_context is not None:
                payload = dict(payload)
                payload["utau_plugin_temp_path"] = str(self._plugin_context.temp_path)
            return dto_to_dict(apply_request(_apply_request(payload)))
        except Exception as exc:
            return {"written_files": [], "moved_to_conflict_folder": [], "backups": [], "csv_path": "", "warnings": [], "skipped": [], "errors": [str(exc)]}

    def validate_rows(self, payload: dict) -> dict:
        try:
            if self._plugin_context is not None:
                payload = dict(payload)
                payload["utau_plugin_temp_path"] = str(self._plugin_context.temp_path)
            return dto_to_dict(validate_preview_rows(_apply_request(payload)))
        except Exception as exc:
            return dto_to_dict(
                PreviewResponse(
                    rows=(),
                    summary=PreviewSummary(rows=0, edits=0, warnings=0, danger=1, can_apply=False),
                    information={},
                )
            ) | {"errors": [str(exc)]}

    # 音声を再生する
    def play_audio(self, payload: dict | None = None) -> dict:
        payload = payload or {}
        try:
            voice_dir = payload.get("voice_dir") or ""
            wav_name = payload.get("wav_name") or ""
            start_ms = int(payload.get("start_ms") or 0)
            end_ms = int(payload.get("end_ms") or 0)
            if wav_name:
                result = self._audio.play_range(Path(voice_dir) / wav_name, start_ms=start_ms, end_ms=end_ms)
            else:
                result = self._audio.play_random(voice_dir)
            return asdict(result)
        except Exception as exc:
            return {"ok": False, "message": str(exc), "path": "", "playing": False}

    # 音声を停止する
    def stop_audio(self, _payload: dict | None = None) -> dict:
        return asdict(self._audio.stop())

    # 原音フォルダを開く
    def open_voice_folder(self, payload: dict | None = None) -> bool:
        payload = payload or {}
        voice_dir = payload.get("voice_dir") or (self._information_payload or {}).get("voice_dir") or ""
        if not voice_dir:
            return False
        path = Path(voice_dir)
        if not path.exists():
            return False
        try:
            os.startfile(str(path))
        except OSError:
            return False
        return True

    # フォルダを選択する
    def choose_directory(self, _kind: str = "", *, parent_window=None) -> str:
        return self._choose(_file_dialog("FOLDER", getattr(webview, "FOLDER_DIALOG", None)), parent_window=parent_window)

    # ファイルを選択する
    def choose_file(self, kind: str = "", *, parent_window=None) -> str:
        if kind == "mrq":
            file_types = ("MRQ files (*.mrq)", "All files (*.*)")
        elif kind == "csv":
            file_types = ("CSV files (*.csv)", "All files (*.*)")
        elif kind == "utau_exe":
            file_types = ("UTAU executable (*.exe)", "All files (*.*)")
        else:
            file_types = ("All files (*.*)",)
        return self._choose(_file_dialog("OPEN", getattr(webview, "OPEN_DIALOG", None)), file_types=file_types, parent_window=parent_window)

    # 値を選択する
    def _choose(self, dialog_type, *, file_types=None, parent_window=None) -> str:
        preferred = parent_window or self._main_window
        candidates = [preferred, self._main_window, *webview.windows]
        windows = []
        for window in candidates:
            if window is not None and window not in windows:
                windows.append(window)
        last_error = None
        kwargs = {"file_types": file_types} if file_types else {}
        if not windows:
            return ""
        try:
            result = windows[0].create_file_dialog(dialog_type, **kwargs)
        except Exception as exc:
            result = None
            last_error = exc
            for window in windows[1:]:
                try:
                    result = window.create_file_dialog(dialog_type, **kwargs)
                    last_error = None
                    break
                except Exception as retry_exc:
                    last_error = retry_exc
        if last_error is not None:
            return ""
        if not result:
            return ""
        if isinstance(result, (str, Path)):
            return str(result)
        return str(result[0])

    # ウィンドウを展開する
    def expand_window(self, current_width: int = 0, current_height: int = 0, min_height: int = 640, min_width: int = 940) -> bool:
        return _resize_main(
            current_width,
            current_height,
            min_width=min_width,
            min_height=min_height,
            resize_to_min_height=False,
        )

    # ウィンドウサイズを同期する
    def sync_min_window_size(
        self,
        min_width: int,
        min_height: int,
        current_width: int = 0,
        current_height: int = 0,
        resize_to_min_height: bool = False,
        resize_to_min_width: bool = False,
    ) -> bool:
        return _resize_main(
            current_width,
            current_height,
            min_width=min_width,
            min_height=min_height,
            resize_to_min_height=resize_to_min_height,
            resize_to_min_width=resize_to_min_width,
        )

    # メインウィンドウの最小サイズと画面サイズを同期
    def sync_main_window_geometry(self, payload: dict | None = None) -> bool:
        data = payload or {}
        _window_geometry_debug_log("sync_main_window_geometry_payload", payload=data)
        return _resize_main(
            int(data.get("current_width") or 0),
            int(data.get("current_height") or 0),
            min_width=int(data.get("min_width") or 0),
            min_height=int(data.get("min_height") or 0),
            resize_to_min_height=bool(data.get("resize_to_min_height", False)),
            resize_to_min_width=bool(data.get("resize_to_min_width", False)),
            target_width=int(data.get("target_width") or 0),
            target_height=int(data.get("target_height") or 0),
            resize_to_target_width_if_below=bool(data.get("resize_to_target_width_if_below", False)),
            resize_to_target_width=bool(data.get("resize_to_target_width", False)),
            resize_to_target_height=bool(data.get("resize_to_target_height", False)),
            debug_context=data.get("debug") if isinstance(data.get("debug"), dict) else None,
        )

    # 設定ウィンドウを処理する
    def open_settings_window(self, payload: dict | None = None) -> bool:
        if self._settings_window is not None:
            self._set_main_settings_blocked(True)

            for method_name in ("restore", "show", "focus"):
                method = getattr(self._settings_window, method_name, None)
                if method:
                    try:
                        method()
                    except Exception:
                        pass
            return True

        try:
            width, height, min_size = _scaled_subwindow_geometry(480, 500, (480, 430), self.settings.ui_scale)
            self._settings_window = _create_subwindow(
                title="AliaScale Settings",
                url=(WEB_DIR / "settings.html").resolve().as_uri(),
                js_api=SettingsApi(self),
                width=width,
                height=height,
                min_size=min_size,
            )

            def _on_settings_closed():
                self._settings_window = None
                self._closing_settings_window = False

                if not self._closing_main_window:
                    self._set_main_settings_blocked(False)

            _bind_window_event(self._settings_window, "closed", _on_settings_closed)

            _bind_window_event(
                self._settings_window,
                "shown",
                lambda: set_owned_window_by_title(
                    child_title="AliaScale Settings",
                    owner_title="AliaScale",
                ),
            )

            self._set_main_settings_blocked(True)

        except Exception:
            self._settings_window = None
            self._set_main_settings_blocked(False)
            return False

        return True

    # 設定を処理する
    def open_information_window(self, payload: dict | None = None) -> bool:
        if payload is not None:
            self._information_payload = payload

        if self._information_window is not None:
            for method_name in ("restore", "show", "focus"):
                method = getattr(self._information_window, method_name, None)
                if method:
                    try:
                        method()
                    except Exception:
                        pass
            self.update_information_window()
            return True

        try:
            width, height, min_size = _scaled_subwindow_geometry(
                _INFORMATION_WINDOW_SIZE[0],
                _INFORMATION_WINDOW_SIZE[1],
                _INFORMATION_WINDOW_MIN_SIZE,
                self.settings.ui_scale,
            )
            self._information_window = _create_subwindow(
                title="AliaScale INFORMATION",
                url=(WEB_DIR / "information_detail.html").resolve().as_uri(),
                js_api=InformationApi(self),
                width=width,
                height=height,
                min_size=min_size,
            )

            _bind_window_event(
                self._information_window,
                "closed",
                lambda: setattr(self, "_information_window", None),
            )

            _bind_window_event(
                self._information_window,
                "shown",
                lambda: set_owned_window_by_title(
                    child_title="AliaScale INFORMATION",
                    owner_title="AliaScale",
                ),
            )

        except Exception as exc:
            print("[open_information_window] failed:", exc)
            self._information_window = None
            return False

        return True
    
    # 設定ウィンドウが開かれているときにメインウィンドウの操作をブロックする
    def _set_main_settings_blocked(self, blocked: bool) -> None:
        if self._main_window is None:
            return

        if self._closing_main_window:
            return

        try:
            self._main_window.evaluate_js(
                f"window.setSettingsWindowOpen && window.setSettingsWindowOpen({str(blocked).lower()})"
            )
        except Exception:
            pass

    # INFORMATION詳細ウィンドウを閉じる
    def close_information_window(self) -> bool:
        window = self._information_window
        self._information_window = None
        try:
            if window is not None and hasattr(window, "destroy"):
                window.destroy()
        except Exception:
            return False
        return True

    def _handle_settings_closing(self) -> bool:
        if self._closing_settings_window:
            return True
        try:
            if self._settings_window is not None:
                self._settings_window.evaluate_js("window.requestSettingsCancel && window.requestSettingsCancel()")
        except Exception:
            pass
        return False

    # 設定ウィンドウを閉じる
    def close_settings_window(self) -> bool:
        window = self._settings_window
        self._settings_window = None
        try:
            if window is not None and hasattr(window, "destroy"):
                self._closing_settings_window = True
                window.destroy()
        except Exception:
            if not self._closing_main_window:
                self._set_main_settings_blocked(False)
            return False
        finally:
            self._closing_settings_window = False
            if not self._closing_main_window:
                self._set_main_settings_blocked(False)

        return True

    # 設定を反映する
    def apply_settings(self, settings: dict) -> bool:
        merged_settings = dto_to_dict(self.settings)
        merged_settings.update(settings)
        self.settings = _app_settings(merged_settings)
        try:
            save_app_settings(self.settings)
        except OSError:
            pass
        main_window = self._main_window
        if main_window is not None:
            settings_dict = dto_to_dict(self.settings)
            payload = json.dumps(settings_dict)
            script = (
                "(() => { "
                f"const settings = {payload}; "
                "window.setTimeout(() => { "
                "if (window.applyAliaScaleSettings) window.applyAliaScaleSettings(settings); "
                "else if (window.measureAliaScaleMinimumViewport) window.measureAliaScaleMinimumViewport(settings); "
                "}, 0); "
                "return null; "
                "})()"
            )
            try:
                measured = main_window.evaluate_js(script)
            except Exception:
                measured = None
            if isinstance(measured, str):
                try:
                    measured = json.loads(measured)
                except json.JSONDecodeError:
                    measured = None
            if isinstance(measured, dict):
                _resize_main(
                    int(measured.get("current_width") or 0),
                    int(measured.get("current_height") or 0),
                    min_width=int(measured.get("min_width") or 0),
                    min_height=int(measured.get("min_height") or 0),
                    resize_to_min_width=False,
                    resize_to_min_height=False,
                    target_width=int(measured.get("target_width") or 0),
                    target_height=int(measured.get("target_height") or 0),
                    resize_to_target_width=True,
                    resize_to_target_height=True,
                )
        if self._information_window is not None:
            width, height, min_size = _scaled_subwindow_geometry(
                _INFORMATION_WINDOW_SIZE[0],
                _INFORMATION_WINDOW_SIZE[1],
                _INFORMATION_WINDOW_MIN_SIZE,
                self.settings.ui_scale,
            )
            try:
                self._information_window.min_size = min_size
            except Exception:
                pass
            try:
                self._information_window.resize(width, height)
            except Exception:
                pass
            self.update_information_window()
        return self.close_settings_window()

    # 値を閉じる
    def close_all_windows(self) -> None:
        self.close_settings_window()
        self.close_information_window()

    # 未適用変更の有無を保持する
    def set_unapplied_changes(self, payload: dict | None = None) -> bool:
        self._has_unapplied_changes = bool((payload or {}).get("value"))
        return True

    # メイン終了確認をイベント外で要求する
    def _request_main_close_dialog(self) -> None:
        try:
            if self._main_window is not None:
                self._main_window.evaluate_js("window.requestMainClose && window.requestMainClose()")
        except Exception:
            pass
        finally:
            self._main_close_prompt_active = False

    # メインウィンドウ終了確認
    def handle_main_closing(self) -> bool:
        if self._closing_main_window:
            return True

        if not self._has_unapplied_changes:
            self._closing_main_window = True
            self._destroy_subwindows_for_shutdown()
            return True

        if not self._main_close_prompt_active:
            self._main_close_prompt_active = True
            timer = Timer(0.05, self._request_main_close_dialog)
            timer.daemon = True
            timer.start()

        return False

    # メインウィンドウを強制終了する
    def force_close_main(self, payload: dict | None = None) -> bool:
        self._has_unapplied_changes = False
        self._main_close_prompt_active = False
        self._closing_main_window = True

        self._destroy_subwindows_for_shutdown()

        if self._main_window is not None:
            try:
                self._main_window.destroy()
            except Exception:
                pass
            finally:
                self._main_window = None

        return True

    # サブウィンドウを同期する
    def sync_subwindow_minimized(self, minimized: bool) -> None:
        for window in (self._settings_window, self._information_window):
            if window is None:
                continue
            method = getattr(window, "minimize" if minimized else "restore", None)
            if method:
                try:
                    method()
                except Exception:
                    pass
    
    # サブウィンドウを破棄する
    def _destroy_subwindows_for_shutdown(self) -> None:
        for attr_name in ("_settings_window", "_information_window"):
            window = getattr(self, attr_name, None)
            setattr(self, attr_name, None)

            if window is None:
                continue

            try:
                if hasattr(window, "destroy"):
                    window.destroy()
            except Exception:
                pass


# 値をサイズ調整する
def _resize_main(
    current_width: int,
    current_height: int,
    *,
    min_width: int,
    min_height: int,
    resize_to_min_height: bool,
    resize_to_min_width: bool = False,
    target_width: int = 0,
    target_height: int = 0,
    resize_to_target_width_if_below: bool = False,
    resize_to_target_width: bool = False,
    resize_to_target_height: bool = False,
    debug_context: dict[str, Any] | None = None,
) -> bool:
    global _main_min_size
    try:
        window = webview.windows[0]
        actual_width, actual_height, chrome_width, chrome_height = _main_window_metrics(window, current_width, current_height)
        min_window_width = int(min_width or 0) + chrome_width
        min_window_height = int(min_height or 0) + chrome_height
        _install_main_min_size_hook(window)
        hwnd = _main_hwnd or _find_main_hwnd(window)
        work_width, work_height = _work_area_logical_size(hwnd)
        if work_width:
            min_window_width = min(min_window_width, work_width)
        if work_height:
            min_window_height = min(min_window_height, work_height)
        _main_min_size = _logical_to_physical_size(min_window_width, min_window_height, hwnd)
        if hasattr(window, "min_size"):
            window.min_size = (min_window_width, min_window_height)
        requested_target_width = int(target_width or 0)
        requested_target_height = int(target_height or 0)
        target_window_width = requested_target_width + chrome_width if requested_target_width else min_window_width
        target_window_height = requested_target_height + chrome_height if requested_target_height else min_window_height
        target_window_width = max(target_window_width, min_window_width)
        target_window_height = max(target_window_height, min_window_height)
        if work_width:
            target_window_width = min(target_window_width, work_width)
        if work_height:
            target_window_height = min(target_window_height, work_height)
        initial_resize_to_target_width = resize_to_target_width
        if resize_to_target_width_if_below:
            resize_to_target_width = actual_width < target_window_width
        target_width = target_window_width if resize_to_min_width or resize_to_target_width else max(actual_width, min_window_width)
        target_height = target_window_height if resize_to_min_height or resize_to_target_height else actual_height
        if work_width:
            target_width = min(target_width, work_width)
        if work_height:
            target_height = min(target_height, work_height)
        needs_width = actual_width < min_window_width
        needs_height = actual_height < min_window_height and (resize_to_min_height or resize_to_target_height)
        resize_called = needs_width or needs_height or resize_to_min_height or resize_to_min_width or resize_to_target_width or resize_to_target_height
        _window_geometry_debug_log(
            "resize_main_decision",
            debug=debug_context or {},
            current_width=current_width,
            current_height=current_height,
            actual_width=actual_width,
            actual_height=actual_height,
            chrome_width=chrome_width,
            chrome_height=chrome_height,
            min_width=min_width,
            min_height=min_height,
            min_window_width=min_window_width,
            min_window_height=min_window_height,
            requested_target_width=requested_target_width,
            requested_target_height=requested_target_height,
            target_window_width=target_window_width,
            target_window_height=target_window_height,
            target_width=target_width,
            target_height=target_height,
            work_width=work_width,
            work_height=work_height,
            resize_to_min_width=resize_to_min_width,
            resize_to_min_height=resize_to_min_height,
            resize_to_target_width_if_below=resize_to_target_width_if_below,
            initial_resize_to_target_width=initial_resize_to_target_width,
            resize_to_target_width=resize_to_target_width,
            resize_to_target_height=resize_to_target_height,
            needs_width=needs_width,
            needs_height=needs_height,
            resize_called=resize_called,
        )
        if resize_called:
            window.resize(target_width, target_height)
            native_resize = _native_resize_window(hwnd, target_width, target_height)
            _window_geometry_debug_log(
                "resize_main_resize_called",
                debug=debug_context or {},
                target_width=target_width,
                target_height=target_height,
                native_resize=native_resize,
            )
            post_actual_width, post_actual_height, post_chrome_width, post_chrome_height = _main_window_metrics(
                window,
                current_width,
                current_height,
            )
            _window_geometry_debug_log(
                "resize_main_after_resize",
                debug=debug_context or {},
                actual_width=post_actual_width,
                actual_height=post_actual_height,
                chrome_width=post_chrome_width,
                chrome_height=post_chrome_height,
            )
    except Exception as exc:
        _window_geometry_debug_log("resize_main_error", debug=debug_context or {}, error=repr(exc))
        return False
    return True


# ウィンドウイベントを関連付ける
def _bind_window_event(window, event_name: str, callback) -> None:
    try:
        event = getattr(getattr(window, "events", None), event_name, None)
        if event is not None:
            event += lambda *args, **kwargs: callback()
    except Exception:
        pass


# サブウィンドウ寸法を表示スケールへ追従させる
def _scaled_subwindow_geometry(width: int, height: int, min_size: tuple[int, int], scale: float | int | None) -> tuple[int, int, tuple[int, int]]:
    try:
        factor = float(scale or 1)
    except (TypeError, ValueError):
        factor = 1.0
    if factor <= 0:
        factor = 1.0
    scaled_width = max(1, round(width * factor))
    scaled_height = max(1, round(height * factor))
    scaled_min = (max(1, round(min_size[0] * factor)), max(1, round(min_size[1] * factor)))
    return scaled_width, scaled_height, scaled_min


def _create_subwindow(
    *,
    title: str,
    url: str,
    js_api,
    width: int,
    height: int,
    min_size: tuple[int, int],
):
    return webview.create_window(
        title,
        url,
        js_api=js_api,
        width=width,
        height=height,
        min_size=min_size,
    )


# 一時パスを処理する
def _plugin_temp_path_from_argv(argv: list[str]) -> str | None:
    return argv[0] if argv else None


# アプリを起動する
def main(argv: list[str] | None = None) -> None:
    argv = list(sys.argv[1:] if argv is None else argv)
    api = Api(_plugin_temp_path_from_argv(argv))
    window = webview.create_window(
        "AliaScale",
        (WEB_DIR / "index.html").resolve().as_uri(),
        js_api=api,
        width=1,
        height=640,
        min_size=_MAIN_NATIVE_MIN_SIZE,
    )

    def _on_main_closed():
        api._closing_main_window = True
        api._main_window = None
        api._destroy_subwindows_for_shutdown()

    api.set_main_window(window)
    _bind_window_event(window, "closing", api.handle_main_closing)
    _bind_window_event(window, "closed", _on_main_closed)
    _bind_window_event(window, "minimized", lambda: api.sync_subwindow_minimized(True))
    _bind_window_event(window, "restored", lambda: api.sync_subwindow_minimized(False))
    webview.start()


if __name__ == "__main__":
    main()
