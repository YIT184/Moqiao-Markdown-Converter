"""WebView-based desktop interface for Moqiao."""

from __future__ import annotations

import ctypes
import json
import multiprocessing
import os
import pathlib
import queue
import subprocess
import sys
import threading
import time
from importlib import resources
from typing import Any

from .converters import convert_file, supported_extensions
from .markdown_utils import safe_stem

WINDOW_WIDTH = 1240
WINDOW_HEIGHT = 820


def _human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def _default_settings_path() -> pathlib.Path:
    base = pathlib.Path(os.environ.get("APPDATA", pathlib.Path.home() / ".config"))
    return base / "Moqiao" / "settings.json"


def _centered_position(width: int, height: int) -> tuple[int | None, int | None]:
    """Center on the work area of the monitor containing the mouse cursor."""
    if sys.platform != "win32":
        return None, None

    class Point(ctypes.Structure):
        _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

    class Rect(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    class MonitorInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", ctypes.c_ulong),
            ("rcMonitor", Rect),
            ("rcWork", Rect),
            ("dwFlags", ctypes.c_ulong),
        ]

    try:
        user32 = ctypes.windll.user32
        user32.GetCursorPos.argtypes = [ctypes.POINTER(Point)]
        user32.GetCursorPos.restype = ctypes.c_bool
        user32.MonitorFromPoint.argtypes = [Point, ctypes.c_uint]
        user32.MonitorFromPoint.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(MonitorInfo),
        ]
        user32.GetMonitorInfoW.restype = ctypes.c_bool
        point = Point()
        if not user32.GetCursorPos(ctypes.byref(point)):
            raise OSError("GetCursorPos failed")
        monitor = user32.MonitorFromPoint(point, 2)
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(MonitorInfo)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            raise OSError("GetMonitorInfoW failed")
        work = info.rcWork
        x = work.left + max(0, (work.right - work.left - width) // 2)
        y = work.top + max(0, (work.bottom - work.top - height) // 2)
        return int(x), int(y)
    except (AttributeError, OSError):
        try:
            user32 = ctypes.windll.user32
            return (
                max(0, (user32.GetSystemMetrics(0) - width) // 2),
                max(0, (user32.GetSystemMetrics(1) - height) // 2),
            )
        except AttributeError:
            return None, None


def _conversion_process(
    paths: list[str],
    output_dir: str,
    options: dict[str, Any],
    events: Any,
) -> None:
    """Convert a complete batch outside the WebView process."""
    for path in paths:
        source = pathlib.Path(path)
        events.put({
            "type": "item",
            "path": path,
            "values": {"status": "active", "progress": 18},
            "log": {"time": time.strftime("%H:%M:%S"), "level": "active",
                    "message": f"开始转换：{source.name}"},
        })
        try:
            markdown = convert_file(path, output_dir, options)
            output_path = pathlib.Path(output_dir) / f"{safe_stem(source.stem)}.md"
            output_path.write_text(markdown, encoding="utf-8")
            events.put({
                "type": "item",
                "path": path,
                "values": {"status": "done", "progress": 100, "output": str(output_path)},
                "log": {"time": time.strftime("%H:%M:%S"), "level": "done",
                        "message": f"已完成：{source.name} → {output_path.name}"},
            })
        except Exception as exc:
            events.put({
                "type": "item",
                "path": path,
                "values": {"status": "error", "progress": 100, "error": str(exc)},
                "log": {"time": time.strftime("%H:%M:%S"), "level": "error",
                        "message": f"{source.name}：{exc}"},
            })
        finally:
            events.put({"type": "completed"})
    events.put({"type": "done"})


class DesktopApi:
    """Thread-safe bridge exposed only to the bundled local frontend."""

    def __init__(self, settings_path: pathlib.Path | None = None) -> None:
        self.window: Any = None
        self._settings_path = settings_path or _default_settings_path()
        self._lock = threading.Lock()
        self._running = False
        self._process: multiprocessing.Process | None = None
        self._event_queue: Any = None
        self._drop_element: Any = None
        self._state: dict[str, Any] = {
            "running": False, "completed": 0, "total": 0, "items": {}, "logs": []
        }

    def select_files(self) -> list[dict[str, Any]]:
        result = self.window.create_file_dialog(
            10,
            allow_multiple=True,
            file_types=(
                "支持的文件 (*.pdf;*.xmind;*.docx;*.pptx;*.xlsx;*.html;*.htm;*.txt;*.csv;*.json;*.xml)",
                "所有文件 (*.*)",
            ),
        )
        return self._describe_paths(result or [])

    def add_paths(self, paths: list[str]) -> list[dict[str, Any]]:
        return self._describe_paths(paths)

    def select_output(self) -> str:
        result = self.window.create_file_dialog(20)
        if not result:
            return ""
        selected = str(result[0] if isinstance(result, (tuple, list)) else result)
        self._save_output_dir(selected)
        return selected

    def default_output(self) -> str:
        try:
            data = json.loads(self._settings_path.read_text(encoding="utf-8"))
            output_dir = data.get("output_dir")
            if isinstance(output_dir, str) and output_dir.strip():
                return output_dir
        except (OSError, ValueError, TypeError):
            pass
        return str(pathlib.Path.home() / "Documents" / "墨桥输出")

    def open_output(self, path: str) -> bool:
        target = pathlib.Path(path).expanduser()
        target.mkdir(parents=True, exist_ok=True)
        self._save_output_dir(str(target.resolve()))
        try:
            if sys.platform == "win32":
                os.startfile(str(target))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(target)])
            else:
                subprocess.Popen(["xdg-open", str(target)])
            return True
        except OSError:
            return False

    def start_conversion(
        self, paths: list[str], output_dir: str, options: dict[str, Any]
    ) -> dict[str, Any]:
        with self._lock:
            if self._running:
                return {"ok": False, "message": "已有转换任务正在进行"}
            extensions = set(supported_extensions())
            valid = [
                str(pathlib.Path(path).resolve())
                for path in paths
                if pathlib.Path(path).is_file()
                and pathlib.Path(path).suffix.lower() in extensions
            ]
            if not valid:
                return {"ok": False, "message": "请先添加受支持的文件"}
            out = pathlib.Path(output_dir).expanduser()
            out.mkdir(parents=True, exist_ok=True)
            resolved_output = str(out.resolve())
            self._save_output_dir(resolved_output)
            self._running = True
            self._state = {
                "running": True,
                "completed": 0,
                "total": len(valid),
                "items": {path: {"status": "waiting", "progress": 0} for path in valid},
                "logs": [],
            }

        context = multiprocessing.get_context("spawn")
        self._event_queue = context.Queue()
        self._process = context.Process(
            target=_conversion_process,
            args=(valid, resolved_output, dict(options), self._event_queue),
            daemon=True,
        )
        try:
            self._process.start()
        except Exception as exc:
            with self._lock:
                self._running = False
                self._state["running"] = False
            return {"ok": False, "message": f"无法启动转换进程：{exc}"}

        threading.Thread(target=self._monitor_process, daemon=True).start()
        return {"ok": True}

    def get_state(self) -> dict[str, Any]:
        with self._lock:
            return {
                "running": self._state["running"],
                "completed": self._state["completed"],
                "total": self._state["total"],
                "items": {key: dict(value) for key, value in self._state["items"].items()},
                "logs": list(self._state["logs"]),
            }

    def bind_drop_target(self, *_: Any) -> None:
        """Use pywebview's native DOM bridge to recover full dropped paths."""
        try:
            from webview.dom import DOMEventHandler

            self._drop_element = self.window.dom.get_element("#dropZone")
            if self._drop_element is not None:
                self._drop_element.on(
                    "drop",
                    DOMEventHandler(
                        self._handle_native_drop,
                        prevent_default=True,
                        stop_propagation=True,
                    ),
                )
        except Exception:
            self._drop_element = None

    def shutdown(self, *_: Any) -> None:
        process = self._process
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=2)

    def _handle_native_drop(self, event: dict[str, Any]) -> None:
        files = event.get("dataTransfer", {}).get("files", [])
        paths = [
            str(file.get("pywebviewFullPath") or file.get("path") or "")
            for file in files
        ]
        described = self._describe_paths(path for path in paths if path)
        if described:
            payload = json.dumps(described, ensure_ascii=False)
            self.window.run_js(f"window.moqiaoAcceptDroppedFiles({payload});")
        else:
            self.window.run_js(
                "window.moqiaoShowDropMessage('未发现受支持的文件');"
            )

    def _describe_paths(self, paths: Any) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        extensions = set(supported_extensions())
        for raw in paths:
            path = pathlib.Path(str(raw))
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            resolved = str(path.resolve())
            if resolved in seen:
                continue
            seen.add(resolved)
            result.append({
                "path": resolved,
                "name": path.name,
                "type": path.suffix[1:].upper(),
                "size": _human_size(path.stat().st_size),
            })
        return result

    def _save_output_dir(self, path: str) -> None:
        try:
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._settings_path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps({"output_dir": path}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary.replace(self._settings_path)
        except OSError:
            pass

    def _monitor_process(self) -> None:
        process = self._process
        event_queue = self._event_queue
        saw_done = False
        if process is None or event_queue is None:
            return

        while not saw_done:
            try:
                event = event_queue.get(timeout=0.25)
            except queue.Empty:
                if not process.is_alive():
                    break
                continue

            event_type = event.get("type")
            with self._lock:
                if event_type == "item":
                    path = event["path"]
                    self._state["items"].setdefault(path, {}).update(event["values"])
                    if event.get("log"):
                        self._state["logs"].append(event["log"])
                elif event_type == "completed":
                    self._state["completed"] += 1
                elif event_type == "done":
                    saw_done = True

        process.join(timeout=2)
        with self._lock:
            if process.exitcode not in (0, None) and not saw_done:
                self._state["logs"].append({
                    "time": time.strftime("%H:%M:%S"),
                    "level": "error",
                    "message": "转换进程意外退出，请重试该批次",
                })
                for values in self._state["items"].values():
                    if values.get("status") in ("waiting", "active"):
                        values.update(status="error", progress=100)
            self._running = False
            self._state["running"] = False

        event_queue.close()
        self._process = None
        self._event_queue = None


def _frontend_uri() -> str:
    return resources.files("pdf2mdx").joinpath("frontend", "index.html").as_uri()


def main() -> None:
    multiprocessing.freeze_support()
    try:
        import webview
    except ImportError as exc:
        raise SystemExit("缺少 pywebview。请执行：pip install pywebview") from exc

    x, y = _centered_position(WINDOW_WIDTH, WINDOW_HEIGHT)
    api = DesktopApi()
    window = webview.create_window(
        "墨桥",
        _frontend_uri(),
        js_api=api,
        width=WINDOW_WIDTH,
        height=WINDOW_HEIGHT,
        x=x,
        y=y,
        min_size=(960, 680),
        background_color="#FFFFFF",
        text_select=False,
    )
    api.window = window
    window.events.loaded += api.bind_drop_target
    window.events.closing += api.shutdown
    webview.start(debug=False)


if __name__ == "__main__":
    main()
