"""Save original NppExport HTML and RTF output through its actual native dialog."""
import ctypes as C
from ctypes import wintypes as W
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
import time

from background_zorder import inspect
from print_owned_window import capture
from verify_plugin_transforms import run
from window_snapshot import snapshot
import win32_probe as win

OUT = Path(__file__).resolve().parent
ORIGINAL = 'int answer = 42;\r\n// export fixture <&>\r\n'
win.bind(win.U, 'GetDlgCtrlID', C.c_int, W.HWND)
win.bind(win.U, 'GetParent', W.HWND, W.HWND)


class ExportText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data):
        self.parts.append(data)


def save_export(_group, label, main, _editor, pid, evidence):
    deadline = time.perf_counter() + 3
    dialogs = []
    while time.perf_counter() < deadline:
        dialogs = [view for view in win.windows(pid) if view['class'] == '#32770' and view['visible']]
        if dialogs:
            break
        time.sleep(.01)
    assert len(dialogs) == 1, snapshot(pid)
    dialog = dialogs[0]['hwnd']
    destination = evidence/('export.html' if label.endswith('HTML') else 'export.rtf')
    assert not destination.exists()
    try:
        background = inspect(pid)
        assert background['passed'], background
        children = win.windows(pid, dialog)
        edits = []
        for view in children:
            if view['class'] != 'Edit' or not view['visible']:
                continue
            parent = win.U.GetParent(view['hwnd'])
            if win.U.GetDlgCtrlID(view['hwnd']) == 1152 or (parent and (
                    win.U.GetDlgCtrlID(parent) == 1148 or (
                        win.U.GetDlgCtrlID(view['hwnd']) == 1001 and win.text(parent, True) == 'ComboBox'))):
                edits.append(view['hwnd'])
        assert len(edits) == 1, [(view['class'], win.U.GetDlgCtrlID(view['hwnd'])) for view in children]
        path = C.create_unicode_buffer(str(destination.resolve()))
        pointer = C.cast(path, C.c_void_p).value
        assert pointer is not None
        assert win.message(edits[0], 0x0c, 0, pointer, timeout=1000) is not None
        readback = C.create_unicode_buffer(2048)
        read_pointer = C.cast(readback, C.c_void_p).value
        assert read_pointer is not None
        length = win.message(edits[0], 0x0d, len(readback), read_pointer, timeout=1000)
        assert length == len(path.value) and readback.value == path.value, (length, readback.value)
        render = capture(dialog, pid, evidence/(destination.suffix[1:]+'-save-dialog.png'))
        assert win.U.PostMessageW(dialog, 0x111, 1, 0)
        deadline = time.perf_counter() + 3
        while time.perf_counter() < deadline and (win.U.IsWindowVisible(dialog) or not destination.exists()):
            time.sleep(.01)
        assert not win.U.IsWindowVisible(dialog) and destination.is_file()
        data = destination.read_bytes()
        if destination.suffix == '.html':
            parser = ExportText()
            parser.feed(data.decode('utf-8-sig'))
            text = ''.join(parser.parts).replace('\xa0', ' ')
            assert 'int answer = 42;' in text and '// export fixture <&>' in text, text
        else:
            assert data.startswith(b'{\\rtf') and b'\\colortbl' in data
            assert b'answer' in data and b'42' in data and b'export fixture <&>' in data
        return {'file': destination.name, 'bytes': len(data), 'background': background, 'save_dialog': render}
    finally:
        if win.U.IsWindowVisible(dialog):
            win.U.PostMessageW(dialog, 0x10, 0, 0)


def main():
    mode = sys.argv[1]
    assert mode in ('default', 'normal')
    runtime = OUT/'runtime'/('startup-idle-'+mode+'44')
    result = run(runtime, 'export-html-rtf', [
        ('NppExport', 'Export to HTML', ORIGINAL),
        ('NppExport', 'Export to RTF', ORIGINAL)], ORIGINAL,
        observe=save_export, asynchronous=True)
    (OUT/('export-plugin-'+mode+'-summary.json')).write_text(json.dumps(result, indent=2), encoding='utf-8')
    raise SystemExit(0 if result['status'] == 'passed' and result.get('process_exit') == 0 else 1)


if __name__ == '__main__':
    main()
