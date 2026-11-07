"""One explicitly authorized real-key trial in an isolated runtime per invocation."""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import subprocess
import stat
import time
import traceback
from typing import Any

from foreground_events import Observer
from measure_posted_input import prepare
from record_startup_menu import WindowCapture, read_rgb  # pyright: ignore[reportMissingImports]
from window_surface import uncloaked
import win32_probe as win

OUT = Path(__file__).resolve().parent


class GUIINFO(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('flags', W.DWORD),
                ('hwndActive', W.HWND), ('hwndFocus', W.HWND),
                ('hwndCapture', W.HWND), ('hwndMenuOwner', W.HWND),
                ('hwndMoveSize', W.HWND), ('hwndCaret', W.HWND), ('rcCaret', W.RECT)]


win.bind(win.U, 'GetGUIThreadInfo', W.BOOL, W.DWORD, C.POINTER(GUIINFO))
win.bind(win.U, 'GetAsyncKeyState', W.SHORT, C.c_int)
DWM = C.WinDLL('dwmapi')
win.bind(DWM, 'DwmGetWindowAttribute', C.c_long, W.HWND, W.DWORD, C.c_void_p, W.DWORD)


def focused_editor(hwnd, pid):
    owner = W.DWORD()
    tid = win.U.GetWindowThreadProcessId(hwnd, C.byref(owner))
    assert owner.value == pid
    info = GUIINFO(cbSize=C.sizeof(GUIINFO))
    assert win.U.GetGUIThreadInfo(tid, C.byref(info))
    editor = info.hwndFocus
    assert editor and win.text(editor, True) == 'Scintilla'
    win.U.GetWindowThreadProcessId(editor, C.byref(owner))
    assert owner.value == pid
    return editor


def rgb_patch(rgb, width, height, box):
    x, y, w, h = box
    assert 0 <= x < x+w <= width and 0 <= y < y+h <= height
    return b''.join(rgb[((y+i)*width+x)*3:((y+i)*width+x+w)*3] for i in range(h))


def different(a, b):
    assert len(a) == len(b) and len(a) > 0
    return sum(max(abs(a[i+j]-b[i+j]) for j in range(3)) > 12
               for i in range(0, len(a), 3)) / (len(a)//3)


def restore_foreground(original, target, observer):
    started = time.perf_counter()
    current = win.U.GetForegroundWindow()
    attempted = bool(original and current == target)
    accepted = bool(win.U.SetForegroundWindow(original)) if attempted else None
    while attempted and current in (None, target) and time.perf_counter()-started < .5:
        observer.pump()
        time.sleep(.003)
        current = win.U.GetForegroundWindow()
    return dict(attempted=attempted, accepted=accepted, observed=current,
                original_restored=current == original,
                observation_ms=(time.perf_counter()-started)*1000)


def run(runtime, index, simple=False, trace=False, early_capture=False):
    folder = OUT/'measurements'/('system-key-'+time.strftime('%Y%m%d-%H%M%S')+f'-{index}')
    folder.mkdir()
    print('START', folder.name, flush=True)
    snapshot = {}
    for directory in ('settings', 'appdata', 'localappdata', 'plugins/Config'):
        for path in (runtime/directory).rglob('*'):
            if path.is_file():
                snapshot[path.relative_to(runtime)] = path.read_bytes()
    for name in ('config.xml', 'session.xml', 'pluginStartup.ini', 'input-target.txt'):
        if (runtime/name).is_file():
            snapshot[Path(name)] = (runtime/name).read_bytes()
    for document in json.loads((runtime/'session-fixture.json').read_text(encoding='utf-8'))['documents']:
        for item in document['copies']:
            snapshot[Path(item['path'])] = (runtime/item['path']).read_bytes()
    prepare(runtime)
    observer = Observer()
    recorder = WindowCapture(OUT/'skill-capture-build/window_capture.dll', fps=30, frames=60)
    original_foreground = win.U.GetForegroundWindow()
    metadata = json.loads((runtime/'tab-preparation.json').read_text(encoding='utf-8'))
    report: dict[str, Any] = dict(status='incomplete', input_method='SendInput virtual-key 1 down/up; Windows input queue',
        character='1', runtime=runtime.name, cache='unflushed; new process',
        fixture='43 retained session documents plus one own empty selected document',
        exe_sha256=hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest(),
        source_commit=metadata['source_commit'],
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        input_injected=False, resource_limits=observer.resource_limits, simple_mode=simple,
        early_capture=early_capture)
    report['original_foreground'] = original_foreground
    report['late_foreground_restorations'] = []
    process, hwnd, editor = None, None, None
    foreground_released = False

    def observe_after_input():
        observer.pump()
        current = win.U.GetForegroundWindow()
        owner = W.DWORD()
        win.U.GetWindowThreadProcessId(current, C.byref(owner))
        if foreground_released and process and current and owner.value == process.pid:
            result = restore_foreground(original_foreground, current, observer)
            result['since_launch_ms'] = (time.perf_counter()-started)*1000
            report['late_foreground_restorations'].append(result)

    def wait_for_owned_exit(timeout):
        assert process is not None
        deadline = time.perf_counter()+timeout
        while process.poll() is None:
            observe_after_input()
            if time.perf_counter() >= deadline:
                raise subprocess.TimeoutExpired(process.args, timeout)
            time.sleep(.01)
        return process.returncode

    try:
        assert report['exe_sha256'] == metadata['exe_sha256'], 'Runtime binary differs from source metadata'
        environment = dict(os.environ, APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
                           TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'))
        environment.pop('NPP_STARTUP_TRACE', None)
        environment.pop('NPP_STARTUP_TSF', None)
        if trace:
            environment['NPP_STARTUP_TRACE'] = str(folder/'startup.tsv')
        report['native_trace'] = trace
        started = time.perf_counter()
        report['launch_qpc_seconds'] = started
        command = [str(runtime/'notepad++.exe'), '-multiInst', '-settingsDir=settings']
        if simple:
            command.append('-simple')
        process = subprocess.Popen(command, cwd=runtime, env=environment)
        report['pid'] = process.pid
        while time.perf_counter()-started < 5:
            observer.pump()
            assert process.poll() is None
            matches = [w for w in win.windows(process.pid) if w['class']=='Notepad++'
                       and w['visible'] and (early_capture or uncloaked(w['hwnd']))]
            if matches:
                hwnd = matches[0]['hwnd']
                break
            time.sleep(.002)
        assert hwnd
        report['capture_attachment_ms'] = (time.perf_counter()-started)*1000
        report['cloaked_at_capture_attachment'] = not uncloaked(hwnd)
        recorder.start(hwnd, process.pid)
        report['capture_ready_ms'] = (time.perf_counter()-started)*1000
        baseline_frames = 1 if early_capture else 2
        while (recorder.state()['retained_frames'] < baseline_frames or not uncloaked(hwnd)) \
                and time.perf_counter()-started < 5:
            observer.pump()
            time.sleep(.002)
        report['empty_frame_count_before_input'] = recorder.state()['retained_frames']
        report['first_frame_observed_ms'] = (time.perf_counter()-started)*1000
        assert report['empty_frame_count_before_input'] >= 1
        assert uncloaked(hwnd), 'A retained cloaked surface is not a visible editor'
        assert win.U.GetForegroundWindow() == hwnd, 'Test window did not receive foreground'
        editor = focused_editor(hwnd, process.pid)
        bounds = W.RECT()
        assert DWM.DwmGetWindowAttribute(hwnd, 9, C.byref(bounds), C.sizeof(bounds)) == 0
        report['capture_screen_bounds'] = [bounds.left, bounds.top, bounds.right, bounds.bottom]
        assert not any(win.U.GetAsyncKeyState(key) & 0x8000 for key in (0x10, 0x11, 0x12, 0x5b, 0x5c))
        events = (win.INPUT*2)(
            win.INPUT(type=1, ki=win.KEYBDINPUT(wVk=0x31, wScan=0x02)),
            win.INPUT(type=1, ki=win.KEYBDINPUT(wVk=0x31, wScan=0x02, dwFlags=2)))
        assert win.U.GetForegroundWindow() == hwnd and focused_editor(hwnd, process.pid) == editor
        report['key_sent_ms'] = (time.perf_counter()-started)*1000
        report['input_injected'] = True
        report['sendinput_event_count'] = win.U.SendInput(2, events, C.sizeof(win.INPUT))
        assert report['sendinput_event_count'] == 2
        while time.perf_counter()-started < 5:
            observer.pump()
            if win.message(editor, 2006, timeout=30) == 1:
                break
            time.sleep(.002)
        assert win.message(editor, 2006, timeout=500) == 1
        assert win.message(editor, 2007, 0, timeout=500) == ord('1')
        report['document_contains_character_ms'] = (time.perf_counter()-started)*1000
        # Restore after the character reached the owned editor, not while its input was queued.
        report['foreground_restoration'] = restore_foreground(original_foreground, hwnd, observer)
        report['foreground_after_restore'] = report['foreground_restoration']['observed']
        assert report['foreground_after_restore'] != hwnd, 'The test window must release the foreground'
        foreground_released = True
        deadline = time.perf_counter()+1.2
        while time.perf_counter() < deadline:
            observe_after_input()
            assert process.poll() is None
            time.sleep(.01)
        recorder.pause()
        report['storage_before_exit'] = recorder.state()
        assert report['storage_before_exit']['cpu_readbacks'] == 0
        deadline = time.perf_counter()+10
        while (win.message(hwnd, 2031, 0, 1, timeout=500) != 44 or
               win.message(hwnd, 2047, 0, 0, timeout=500) != 43):
            assert time.perf_counter() < deadline
            observe_after_input()
            time.sleep(.01)
        assert win.message(hwnd, 2031, 0, 1, timeout=500) == 44
        assert win.message(hwnd, 2047, 0, 0, timeout=500) == 43
        ex, ey, _, _ = win.rect(editor, client=True)
        x = win.message(editor, 2164, 0, 0, timeout=500)
        y = win.message(editor, 2165, 0, 0, timeout=500)
        height = win.message(editor, 2279, 0, timeout=500)
        right = win.message(editor, 2164, 0, 1, timeout=500)
        assert x is not None and y is not None and height is not None and right is not None and right > x+2
        report['glyph_capture_rect'] = [ex+x-bounds.left, ey+y-bounds.top, right-x-2, height]
        expected = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))['plugins']
        deadline = time.perf_counter()+10
        while True:
            mapped = {str(p.resolve()).lower() for p in win.modules(process.pid)}
            if all(str((runtime/'plugins'/p['name']/(p['name']+'.dll')).resolve()).lower() in mapped for p in expected):
                break
            assert time.perf_counter() < deadline
            observe_after_input()
            time.sleep(.01)
        report['plugins'] = []
        for plugin in expected:
            path = runtime/'plugins'/plugin['name']/(plugin['name']+'.dll')
            assert hashlib.sha256(path.read_bytes()).hexdigest() == plugin['sha256']
            assert str(path.resolve()).lower() in mapped
            report['plugins'].append(plugin['name'])
        assert len(report['plugins']) == len(expected)
        win.message(editor, 2014, timeout=500)
        win.U.PostMessageW(hwnd, 0x10, 0, 0)
        report['target_exit'] = wait_for_owned_exit(5)
        assert report['target_exit'] == 0
        print('TARGET_EXITED', flush=True)
        capture = recorder.save(folder/'frames')
        rows = []
        for frame in capture['frames']:
            width, frame_height, rgb = read_rgb(folder/'frames'/frame['file'])
            assert width == bounds.right-bounds.left and frame_height == bounds.bottom-bounds.top
            rows.append(dict(file=frame['file'], ms=(frame['compositor_qpc_seconds']-started)*1000,
                             pixels=rgb_patch(rgb, width, frame_height, report['glyph_capture_rect'])))
        # These textures were retained before SendInput, independently of the
        # compositor timestamp's small lead over capture callback receipt.
        empty = rows[:report['empty_frame_count_before_input']]
        final = rows[-1]
        assert empty and different(empty[-1]['pixels'], final['pixels']) > .025
        visible = next(r for r in rows if r['ms'] >= report['key_sent_ms']
                       and different(r['pixels'], final['pixels']) <= .025)
        report['first_retained_frame_ms'] = rows[0]['ms']
        report['first_visible_character_ms'] = visible['ms']
        report['key_to_visible_ms'] = visible['ms']-report['key_sent_ms']
        report['empty_reference_file'] = empty[-1]['file']
        report['visible_character_file'] = visible['file']
        report['frame_count'] = len(rows)
        report['status'] = 'passed_system_key_measurement'
        print('VISIBLE_CHARACTER_MS', report['first_visible_character_ms'], 'KEY_TO_VISIBLE_MS', report['key_to_visible_ms'], flush=True)
    except Exception as exc:
        report['status'], report['error'] = 'failed', repr(exc)
        report['traceback'] = traceback.format_exc()
        print('FAILED', repr(exc), flush=True)
    finally:
        if original_foreground and hwnd and win.U.GetForegroundWindow() == hwnd:
            report['final_foreground_restoration'] = restore_foreground(original_foreground, hwnd, observer)
        if process and process.poll() is None:
            if editor:
                win.message(editor, 2014, timeout=500)
            if hwnd:
                win.U.PostMessageW(hwnd, 0x10, 0, 0)
            try:
                report['target_exit'] = wait_for_owned_exit(5)
            except subprocess.TimeoutExpired:
                process.terminate()
                report['target_exit'] = process.wait(timeout=5)
        # Keep the actual composed pictures even if a later compatibility check fails.
        if not (folder/'frames/capture.json').is_file():
            try:
                recorder.pause()
                if recorder.state()['retained_frames']:
                    capture = recorder.save(folder/'frames')
                    report['retained_failure_frame_count'] = len(capture['frames'])
            except Exception as exc:
                report['capture_save_error'] = repr(exc)
        recorder.close()
        observer.pump()
        report['foreground_events'] = [e for e in observer.events if process and e['pid']==process.pid]
        report['foreground_protection_passed'] = bool(foreground_released and
            report.get('foreground_restoration', {}).get('original_restored') and
            len(report['foreground_events']) == 1 and not report['late_foreground_restorations'])
        if report['status'] == 'passed_system_key_measurement' and not report['foreground_protection_passed']:
            report['status'] = 'failed_foreground_protection'
        observer.close()
        for path, data in snapshot.items():
            destination = runtime/path
            if not destination.exists() or destination.read_bytes() != data:
                if destination.exists():
                    mode = destination.stat().st_mode
                    destination.chmod(mode | stat.S_IWRITE)
                else:
                    mode = None
                try:
                    destination.write_bytes(data)
                finally:
                    if mode is not None:
                        destination.chmod(mode)
        (folder/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('RESULT', report['status'], 'INPUT', report['input_injected'], flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', required=True, help='Isolated directory name under the session runtime folder')
    parser.add_argument('--simple', action='store_true')
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--early-capture', action='store_true',
        help='Attach when WS_VISIBLE, but require uncloaking and a blank frame before the real key')
    args = parser.parse_args()
    runtime = (OUT/'runtime'/args.runtime).resolve()
    assert runtime.is_relative_to((OUT/'runtime').resolve())
    assert runtime != (OUT/'runtime').resolve()
    assert runtime.is_dir()
    win.U.SetProcessDPIAware()
    report = run(runtime, 'single', simple=args.simple, trace=args.trace, early_capture=args.early_capture)
    raise SystemExit(0 if report['status'] == 'passed_system_key_measurement' else 1)
