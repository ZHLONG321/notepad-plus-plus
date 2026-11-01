"""E2E: native scrollbar state and viewport survive real document activation."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any

from background_zorder import inspect
from foreground_events import Observer
from print_owned_window import capture
import win32_probe as win
from window_frames import WindowFrames

OUT = Path(__file__).resolve().parent


class ScrollInfo(C.Structure):
    _fields_ = [('cbSize', W.UINT), ('fMask', W.UINT), ('nMin', C.c_int),
        ('nMax', C.c_int), ('nPage', W.UINT), ('nPos', C.c_int), ('nTrackPos', C.c_int)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('runtime', choices=['startup-idle-default44', 'startup-idle-normal44'])
    parser.add_argument('--native-stills', action='store_true')
    args = parser.parse_args()
    runtime = OUT/'runtime'/args.runtime
    folder = OUT/'measurements'/('scroll-state-'+args.runtime+'-'+time.strftime('%Y%m%d-%H%M%S'))
    folder.mkdir()
    metadata = json.loads((runtime/'tab-preparation.json').read_text(encoding='utf-8'))
    assert hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest() == metadata['exe_sha256']
    plugins = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))['plugins']
    for item in plugins:
        dll = runtime/'plugins'/item['name']/(item['name']+'.dll')
        assert hashlib.sha256(dll.read_bytes()).hexdigest() == item['sha256']
    report: dict[str, Any] = dict(status='incomplete', source_commit=metadata['source_commit'],
        exe_sha256=metadata['exe_sha256'], runtime=args.runtime, actions=[])
    get_scroll = win.bind(win.U, 'GetScrollInfo', W.BOOL, W.HWND, C.c_int, C.POINTER(ScrollInfo))
    observer = Observer()
    report['test_resource_limits'] = observer.resource_limits
    report['capture_method'] = 'Native PrintWindow stills' if args.native_stills else 'WGC window frames'
    app = recorder = hwnd = None
    if not args.native_stills:
        WindowFrames.prepare()
    print('START', folder.name, flush=True)
    try:
        app = subprocess.Popen([str(runtime/'notepad++.exe'), '-multiInst', '-noActivate', '-settingsDir=settings'],
            cwd=runtime, env=dict(os.environ, APPDATA=str(runtime/'appdata'),
                LOCALAPPDATA=str(runtime/'localappdata'), TEMP=str(runtime/'temp'), TMP=str(runtime/'temp')))
        deadline = time.perf_counter()+6
        while time.perf_counter() < deadline:
            observer.pump()
            assert app.poll() is None
            mains = [w for w in win.windows(app.pid) if w['class']=='Notepad++' and w['visible']]
            if mains:
                hwnd = mains[0]['hwnd']
                mapped = {str(p.resolve()).lower() for p in win.modules(app.pid)}
                if all(str((runtime/'plugins'/p['name']/(p['name']+'.dll')).resolve()).lower() in mapped for p in plugins):
                    break
            time.sleep(.02)
        else:
            raise RuntimeError('Fixture launch deadline')
        assert hwnd and win.message(hwnd, 2031, 0, 1, timeout=1000)==44
        assert inspect(app.pid)['passed']
        report['mapped_plugin_count'] = 14
        if not args.native_stills:
            recorder = WindowFrames(hwnd)
        editor = next(w['hwnd'] for w in win.windows(parent=hwnd) if w['class']=='Scintilla' and w['visible'])
        selected = None
        for index in range(44):
            assert win.message(hwnd, 2052, 0, index, timeout=1000) is not None
            lines = win.message(editor, 2154, timeout=1000)
            assert lines is not None
            if lines > 200:
                selected = index
                break
        assert selected is not None, 'No sufficiently long fixture document'
        other = (selected+1) % 44
        for target in (20, 60, 100):
            assert win.message(editor, 2613, target, timeout=1000) is not None
            deadline = time.perf_counter()+.15
            latest = None
            while time.perf_counter() < deadline:
                observer.pump()
                if recorder:
                    latest = recorder.next() or latest
                time.sleep(.01)
            first = win.message(editor, 2152, timeout=1000)
            assert first == target, (target, first)
            before = ScrollInfo(C.sizeof(ScrollInfo), 0x17)
            assert get_scroll(editor, 1, C.byref(before))
            assert before.nPos == target
            assert win.message(hwnd, 2052, 0, other, timeout=1000) is not None
            assert win.message(hwnd, 2052, 0, selected, timeout=1000) is not None
            after = ScrollInfo(C.sizeof(ScrollInfo), 0x17)
            assert get_scroll(editor, 1, C.byref(after))
            restored = win.message(editor, 2152, timeout=1000)
            observed = dict(target=target, restored=restored,
                native_before=dict(position=before.nPos, maximum=before.nMax, page=before.nPage),
                native_after=dict(position=after.nPos, maximum=after.nMax, page=after.nPage))
            report['actions'].append(observed)
            assert restored == target and after.nPos == target, observed
            assert (after.nMax, after.nPage) == (before.nMax, before.nPage), observed
            if latest:
                win.png(folder/f'viewport-{target}.png', latest[3], *latest[2][2:])
            if args.native_stills:
                capture(hwnd, app.pid, folder/f'viewport-{target}.png')
            print('PASS', selected, target, after.nPos, flush=True)
        # Horizontal scrolling and wrap transitions exercise the other native
        # page clamp; short/empty documents must keep an in-range page as well.
        original_wrap = win.message(editor, 2269, timeout=1000)
        original_width = win.message(editor, 2275, timeout=1000)
        assert original_wrap is not None and original_width is not None
        try:
            win.message(editor, 2268, 0, timeout=1000)
            win.message(editor, 2274, 4000, timeout=1000)
            win.message(editor, 2397, 120, timeout=1000)
            horizontal = ScrollInfo(C.sizeof(ScrollInfo), 0x17)
            assert get_scroll(editor, 0, C.byref(horizontal))
            assert win.message(editor, 2398, timeout=1000)==horizontal.nPos==120
            win.message(editor, 2268, 1, timeout=1000)
            deadline = time.perf_counter()+.2
            while time.perf_counter() < deadline:
                observer.pump()
                if recorder:
                    recorder.next()
                time.sleep(.01)
            wrapped = ScrollInfo(C.sizeof(ScrollInfo), 0x17)
            assert get_scroll(editor, 0, C.byref(wrapped))
            report['horizontal'] = dict(unwrapped_position=horizontal.nPos,
                wrapped_position=wrapped.nPos, wrapped_page=wrapped.nPage, maximum=wrapped.nMax,
                editor_offset=win.message(editor, 2398, timeout=1000))
            assert wrapped.nPage == wrapped.nMax-wrapped.nMin+1, report['horizontal']
            assert report['horizontal']['editor_offset']==wrapped.nPos==0, report['horizontal']
            print('PASS horizontal and wrap', flush=True)
        finally:
            win.message(editor, 2274, original_width, timeout=1000)
            win.message(editor, 2268, original_wrap, timeout=1000)
        assert win.message(hwnd, 2052, 0, 43, timeout=1000) is not None
        assert win.message(editor, 2006, timeout=1000)==0
        empty = ScrollInfo(C.sizeof(ScrollInfo), 0x17)
        assert get_scroll(editor, 1, C.byref(empty))
        assert empty.nPos==0 and 0 <= empty.nPage <= empty.nMax-empty.nMin+1
        report['empty'] = dict(position=empty.nPos, page=empty.nPage, maximum=empty.nMax)
        print('PASS empty document', flush=True)
        report['background_after'] = inspect(app.pid)
        assert report['background_after']['passed']
        report['status'] = 'passed'
    except Exception as exc:
        report['status'], report['error'] = 'failed', repr(exc)
        raise
    finally:
        if recorder:
            recorder.close()
        if hwnd:
            win.U.PostMessageW(hwnd, 0x10, 0, 0)
        if app:
            report['exit'] = app.wait(timeout=5)
        observer.pump()
        report['foreground_events'] = [e for e in observer.events if app and e['pid']==app.pid]
        observer.close()
        (folder/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('RESULT', report['status'], report.get('exit'), report['foreground_events'], flush=True)
    assert report['status']=='passed' and report['exit']==0 and report['foreground_events']==[]


if __name__ == '__main__':
    main()
