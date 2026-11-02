"""Record startup menu pixels on the GPU, then inspect them after target exit."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import zlib

from background_zorder import inspect
from foreground_events import Observer
import native_ui_record
import win32_probe as win
from window_surface import uncloaked

OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(OUT / 'windows-window-capture/scripts'))
from window_capture import WindowCapture, save_png  # pyright: ignore[reportMissingImports]


def read_rgb(path: Path) -> tuple[int, int, bytes]:
    data = path.read_bytes()
    position, packed = 8, b''
    width, height = 0, 0
    while position < len(data):
        size = struct.unpack_from('>I', data, position)[0]
        tag = data[position+4:position+8]
        block = data[position+8:position+8+size]
        if tag == b'IHDR':
            width, height = struct.unpack_from('>II', block)
        if tag == b'IDAT':
            packed += block
        position += size+12
    rows = zlib.decompress(packed)
    stride = width*3+1
    assert width > 0 and height > 0
    assert len(rows) == height*stride and all(rows[y*stride] == 0 for y in range(height))
    return width, height, b''.join(rows[y*stride+1:(y+1)*stride] for y in range(height))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', choices=['startup-idle-default44', 'startup-idle-normal44'], default='startup-idle-default44')
    parser.add_argument('--single-file', action='store_true')
    parser.add_argument('--allow-foreground', action='store_true')
    parser.add_argument('--early-capture', action='store_true')
    args = parser.parse_args()
    runtime = OUT / 'runtime' / args.runtime
    folder = OUT / 'measurements' / ('startup-menu-' + args.runtime + '-' + time.strftime('%Y%m%d-%H%M%S') + '-' + str(time.time_ns() % 1000000))
    folder.mkdir()
    settings = {}
    for directory in ('settings', 'appdata', 'localappdata', 'plugins/Config'):
        for path in (runtime / directory).rglob('*'):
            if path.is_file():
                settings[path.relative_to(runtime)] = path.read_bytes()
    for name in ('config.xml', 'session.xml', 'pluginStartup.ini'):
        settings[Path(name)] = (runtime/name).read_bytes()
    report = {'status': 'incomplete', 'runtime': args.runtime, 'input_performed': False,
              'single_file_control': args.single_file, 'foreground_trial': args.allow_foreground,
              'early_capture': args.early_capture,
              'exe_sha256': hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest(),
              'source_commit': json.loads((runtime/'tab-preparation.json').read_text(encoding='utf-8'))['source_commit']}
    observer = Observer()
    recorder = WindowCapture(OUT/'skill-capture-build/window_capture.dll', fps=30, frames=60)
    process, hwnd = None, None
    print('START', folder.name, flush=True)
    try:
        environment = dict(os.environ, APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
            TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'))
        environment.pop('NPP_STARTUP_TRACE', None)
        start = time.perf_counter()
        report['launch_qpc_seconds'] = start
        command = [str(runtime/'notepad++.exe'), '-multiInst', '-settingsDir=settings']
        if not args.allow_foreground:
            command.append('-noActivate')
        if args.single_file:
            document = folder/'startup-benchmark.txt'
            document.write_text('Classic Notepad startup benchmark.\n', encoding='ascii')
            command += ['-nosession', str(document)]
        process = subprocess.Popen(command, cwd=runtime, env=environment)
        report['pid'] = process.pid
        deadline = start+5
        while time.perf_counter() < deadline:
            observer.pump()
            windows = [item for item in win.windows(process.pid) if item['class']=='Notepad++'
                       and ((args.early_capture and all(size > 0 for size in win.rect(item['hwnd'], client=True)[2:]))
                            or (not args.early_capture and item['visible'] and uncloaked(item['hwnd'])))]
            if windows:
                hwnd = windows[0]['hwnd']
                break
            assert process.poll() is None
            time.sleep(.005)
        assert hwnd
        report['capture_target_visible'] = bool(win.U.IsWindowVisible(hwnd))
        report['capture_target_uncloaked'] = uncloaked(hwnd)
        report['capture_target_rect'] = win.rect(hwnd)
        report['capture_begin_ms'] = (time.perf_counter()-start)*1000
        recorder.start(hwnd, process.pid)
        report['capture_ready_ms'] = (time.perf_counter()-start)*1000
        deadline = start+5
        while not (win.U.IsWindowVisible(hwnd) and uncloaked(hwnd)):
            observer.pump()
            assert process.poll() is None
            assert time.perf_counter() < deadline
            time.sleep(.005)
        report['visible_uncloaked_observed_ms'] = (time.perf_counter()-start)*1000
        report['background'] = inspect(process.pid)
        assert args.allow_foreground or report['background']['passed']
        report['menus_before'] = ([item['text'] for item in native_ui_record.record(hwnd)['menus']]
                                  if win.U.GetMenu(hwnd) else [])
        deadline = time.perf_counter()+2.3
        while time.perf_counter() < deadline:
            observer.pump()
            assert process.poll() is None
            time.sleep(.01)
        recorder.pause()
        report['storage_before_exit'] = recorder.state()
        assert report['storage_before_exit']['cpu_readbacks'] == 0
        report['menus_after'] = [item['text'] for item in native_ui_record.record(hwnd)['menus']]
        report['documents'] = win.message(hwnd, 2031, 0, 1, timeout=300)
        assert report['documents'] == (1 if args.single_file else 44)
        expected = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))['plugins']
        mapped = {str(Path(p).resolve()).lower() for p in win.modules(process.pid)}
        report['plugins'] = []
        for plugin in expected:
            path = runtime/'plugins'/plugin['name']/(plugin['name']+'.dll')
            assert hashlib.sha256(path.read_bytes()).hexdigest() == plugin['sha256']
            if str(path.resolve()).lower() in mapped:
                report['plugins'].append(plugin['name'])
        assert len(report['plugins']) == 14
        assert win.U.PostMessageW(hwnd, 0x10, 0, 0)
        deadline = time.perf_counter()+5
        while process.poll() is None and time.perf_counter() < deadline:
            observer.pump()
            time.sleep(.01)
        report['target_exit'] = process.poll()
        assert report['target_exit'] == 0
        print('TARGET_EXITED', recorder.state(), flush=True)
        capture = recorder.save(folder/'frames')
        report['frame_count'] = len(capture['frames'])
        rows = []
        for frame in capture['frames']:
            width, height, rgb = read_rgb(folder/'frames'/frame['file'])
            # Menu bar coordinates are checked against the retained full-size picture.
            top, bottom = 31, 49
            row = {'ms': (frame['compositor_qpc_seconds']-start)*1000, 'file': frame['file'], 'segments': []}
            for left in range(0, min(width, 800), 100):
                right = min(left+100, width)
                pixels = [rgb[(y*width+x)*3:(y*width+x)*3+3]
                          for y in range(top, bottom) for x in range(left, right)]
                row['segments'].append(sum(max(pixel) >= 140 for pixel in pixels))
            rows.append(row)
        report['menu_pixel_rows'] = rows
        report['status'] = 'captured_for_menu_review'
    finally:
        recorder.close()
        if process and process.poll() is None:
            if hwnd:
                win.U.PostMessageW(hwnd, 0x10, 0, 0)
            try:
                process.wait(timeout=5 if hwnd else .1)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
                report['forced_failure_exit'] = True
        if process:
            report['target_exit'] = process.poll()
        observer.pump()
        report['foreground_events'] = [e for e in observer.events if process and e['pid']==process.pid]
        observer.close()
        for path, data in settings.items():
            (runtime/path).write_bytes(data)
        (folder/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('RESULT', report['status'], report.get('frame_count'), report.get('target_exit'), flush=True)
    assert args.allow_foreground or report['foreground_events'] == []


if __name__ == '__main__':
    main()
