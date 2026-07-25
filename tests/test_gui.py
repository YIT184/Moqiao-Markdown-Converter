"""Desktop bridge regression tests."""

from __future__ import annotations

import json
import queue

from pdf2mdx.gui import DesktopApi, _conversion_process


class FakeWindow:
    def __init__(self, selected: str | None = None) -> None:
        self.selected = selected
        self.scripts: list[str] = []

    def create_file_dialog(self, *_args, **_kwargs):
        return (self.selected,) if self.selected else None

    def run_js(self, script: str) -> None:
        self.scripts.append(script)


def test_output_directory_persists_between_api_instances(tmp_path) -> None:
    settings = tmp_path / "settings.json"
    output = tmp_path / "chosen"
    first = DesktopApi(settings)
    first.window = FakeWindow(str(output))

    assert first.select_output() == str(output)
    assert json.loads(settings.read_text(encoding="utf-8"))["output_dir"] == str(output)

    second = DesktopApi(settings)
    assert second.default_output() == str(output)


def test_native_drop_forwards_full_paths_without_opening_picker(tmp_path) -> None:
    source = tmp_path / "dragged.csv"
    source.write_text("A,B\n1,2", encoding="utf-8")
    api = DesktopApi(tmp_path / "settings.json")
    window = FakeWindow()
    api.window = window

    api._handle_native_drop({
        "dataTransfer": {"files": [{"pywebviewFullPath": str(source)}]}
    })

    assert len(window.scripts) == 1
    assert "moqiaoAcceptDroppedFiles" in window.scripts[0]
    assert "dragged.csv" in window.scripts[0]


def test_conversion_process_writes_output_and_reports_completion(tmp_path) -> None:
    source = tmp_path / "data.csv"
    source.write_text("Name,Count\nMoqiao,1", encoding="utf-8")
    events: queue.Queue = queue.Queue()

    _conversion_process([str(source)], str(tmp_path), {}, events)
    received = []
    while not events.empty():
        received.append(events.get_nowait())

    assert (tmp_path / "data.md").exists()
    assert any(event["type"] == "completed" for event in received)
    assert received[-1]["type"] == "done"
