"""Measure queued WM_CHAR to real visible glyphs; this is not system key input."""
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
import xml.etree.ElementTree as ET
import zlib

from foreground_events import Observer
from launch_capture import covering_windows
import win32_probe as win
from window_frames import WindowFrames
from native_xml import write_xml

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def prepare(runtime):
    (runtime/'input-target.txt').write_bytes(b'')
    manifest = json.loads((runtime/'session-fixture.json').read_text(encoding='utf-8'))
    tree = ET.parse(OUT/'configuration-source/local/session.xml')
    entries = tree.findall('.//File')
    for entry, record in zip(entries, manifest['documents'], strict=True):
        for copy in record['copies']:
            assert hashlib.sha256((runtime/copy['path']).read_bytes()).hexdigest()==copy['sha256']
            entry.set('filename' if copy['kind']=='original' else 'backupFilePath', copy['path'])
    target = entries[43]
    target.attrib.update(filename='input-target.txt', backupFilePath='', lang='Normal Text',
        encoding='-1', firstVisibleLine='0', xOffset='0', startPos='0', endPos='0',
        selMode='0', userReadOnly='no')
    for child in list(target):
        target.remove(child)
    for destination in [runtime/'session.xml', runtime/'settings/session.xml']:
        write_xml(tree, destination)


def patch(data, area, box):
    left, top, width, height = box
    x, y = left-area[0], top-area[1]
    assert 0 <= x < area[2] and 0 <= y < area[3]
    assert x+width <= area[2] and y+height <= area[3]
    return b''.join(data[((y+row)*area[2]+x)*4:((y+row)*area[2]+x+width)*4]
                    for row in range(height))


def difference(a, b):
    assert len(a)==len(b) and len(a)>0
    return sum(max(abs(a[i+j]-b[i+j]) for j in range(3)) > 12
               for i in range(0, len(a), 4))/(len(a)//4)


def intersects(a, b):
    return a[0] < b[0]+b[2] and b[0] < a[0]+a[2] and a[1] < b[1]+b[3] and b[1] < a[1]+a[3]


def occluders(hwnd, area):
    result = covering_windows(hwnd, area)
    for item in result:
        pid = W.DWORD()
        win.U.GetWindowThreadProcessId(item['hwnd'], C.byref(pid))
        item.update(pid=pid.value, rect=win.rect(item['hwnd']))
    return result


def run(runtime, index, character, trace, capture):
    prepare(runtime)
    evidence = OUT/'measurements'/('posted-input-'+time.strftime('%Y%m%d-%H%M%S')+f'-{index:02d}')
    evidence.mkdir(parents=True)
    audit = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))
    required = []
    for item in audit['plugins']:
        path = runtime/'plugins'/item['name']/(item['name']+'.dll')
        assert hashlib.sha256(path.read_bytes()).hexdigest()==item['sha256']
        required.append(str(path.resolve()).lower())
    preparation = json.loads((runtime/'tab-preparation.json').read_text(encoding='utf-8'))
    exe_hash = hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest()
    assert preparation['exe_sha256'] == exe_hash
    report: dict[str, Any] = dict(status='incomplete', input_method='PostMessageW WM_CHAR; not SendInput or physical key',
        source_revision=preparation['source_commit'],
        exe_sha256=exe_hash,
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        fixture='43 retained session copies plus one own empty text target replacing selected index 43',
        cache='unflushed; new process', character=character, actions=[], frames=[], covered_frames=[])
    report['capture'] = capture
    report['recording_device_prepared_before_launch'] = capture == 'window'
    frames = []
    observer, process, main_hwnd, editor = Observer(), None, None, None
    recorder = None
    env = dict(os.environ, APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
               TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'))
    env.pop('NPP_STARTUP_TRACE', None)
    if trace:
        env['NPP_STARTUP_TRACE'] = str(evidence/'startup.tsv')
    report['trace_enabled'] = trace
    started = time.perf_counter()
    print('START', evidence.relative_to(ROOT), flush=True)
    try:
        command = [str(runtime/'notepad++.exe'), '-multiInst', '-noActivate', '-settingsDir='+str(runtime/'settings')]
        report['command'] = ['notepad++.exe', '-multiInst', '-noActivate', '-settingsDir=settings']
        process = subprocess.Popen(command, cwd=runtime, env=env)
        first_post = None
        while time.perf_counter()-started < 5:
            observer.pump()
            assert process.poll() is None
            if not main_hwnd:
                windows = [w for w in win.windows(process.pid) if w['class']=='Notepad++']
                if windows:
                    main_hwnd = windows[0]['hwnd']
            if main_hwnd and win.U.IsWindowVisible(main_hwnd):
                if capture=='window' and recorder is None:
                    recorder = WindowFrames(main_hwnd)
                    report['capture_ready_ms'] = (time.perf_counter()-started)*1000
                if not editor:
                    views = [w for w in win.windows(parent=main_hwnd) if w['class']=='Scintilla' and w['visible']]
                    report['last_editor_candidates'] = [dict(v, rect=win.rect(v['hwnd'])) for v in views]
                    if views:
                        editor = max(views, key=lambda v:win.rect(v['hwnd'])[2]*win.rect(v['hwnd'])[3])['hwnd']
                area = win.rect(main_hwnd)
                if area[2] > 200 and area[3] > 100:
                    if recorder:
                        captured = recorder.next()
                        if captured is None:
                            time.sleep(.002)
                            continue
                        compositor, received, area, data = captured
                        report.setdefault('frame_received_ms', []).append((received-started)*1000)
                        report.setdefault('frame_delivery_lag_ms', []).append((received-compositor)*1000)
                        assert -.025 <= received-compositor < .25, 'Unexpected compositor timestamp domain'
                        frames.append(((max(compositor, received)-started)*1000, area, data))
                    else:
                        covered = occluders(main_hwnd, area)
                        if covered:
                            report['covered_frames'].append(dict(frame=len(frames), windows=covered))
                        capture_started = (time.perf_counter()-started)*1000
                        data = win.capture(area)
                        frames.append(((time.perf_counter()-started)*1000, area, data))
                        report.setdefault('capture_started_ms', []).append(capture_started)
                    now = time.perf_counter()
                    if editor and len(report['actions']) < 15 and (
                            first_post is None or now-first_post >= len(report['actions'])*.1):
                        if first_post is None:
                            first_post = now
                            report['before_input_frame'] = len(frames)-1
                        action = dict(index=len(report['actions']), posted_ms=(now-started)*1000)
                        assert win.U.PostMessageW(editor, 0x102, ord(character), 1)
                        report['actions'].append(action)
                    if first_post is not None and now-first_post > 2.3:
                        break
            time.sleep(.002 if recorder else .008)
        assert main_hwnd and editor and len(report['actions'])==15
        assert win.message(main_hwnd, 2031, 0, 1, timeout=1000)==44
        assert win.message(main_hwnd, 2047, 0, 0, timeout=1000)==43
        assert win.message(editor, 2006, timeout=1000)==15
        assert all(win.message(editor, 2007, i, timeout=1000)==ord(character) for i in range(15))
        # Input can postpone initialization beyond the capture interval.
        # Wait separately for completion without changing the captured timings.
        deadline = time.perf_counter()+3
        while True:
            loaded = {str(Path(p).resolve()).lower() for p in win.modules(process.pid)}
            if set(required) <= loaded or time.perf_counter() >= deadline:
                break
            observer.pump()
            assert process.poll() is None
            time.sleep(.01)
        assert set(required) <= loaded
        report['plugin_count'] = len(required)
        report['editor_rect'] = win.rect(editor, client=True)
        ex, ey, _, _ = report['editor_rect']
        height = win.message(editor, 2279, 0, timeout=1000)
        assert height is not None and height > 3
        final_stamp, final_area, final_data = frames[-1]
        covered_by_frame = {entry['frame']: entry['windows'] for entry in report['covered_frames']}
        before = frames[report['before_input_frame']]
        for action in report['actions']:
            position = action['index']
            x = win.message(editor, 2164, 0, position, timeout=1000)
            right = win.message(editor, 2164, 0, position+1, timeout=1000)
            y = win.message(editor, 2165, 0, position, timeout=1000)
            assert x is not None and y is not None and right is not None and right > x+2
            box = (ex+x, ey+y, right-x-2, height)
            hidden_frames = [i for i, covers in covered_by_frame.items()
                             if any(intersects(box, cover['rect']) for cover in covers)]
            assert len(frames)-1 not in hidden_frames, 'Final glyph reference occluded'
            assert report['before_input_frame'] not in hidden_frames, 'Empty glyph reference occluded'
            final_patch = patch(final_data, final_area, box)
            assert sum(max(final_patch[i:i+3]) > 80 for i in range(0, len(final_patch), 4)) > 3
            assert difference(patch(before[2], before[1], box), final_patch) > .025
            first_match = next((stamp for i, (stamp, area, data) in enumerate(frames)
                if stamp >= action['posted_ms'] and area==final_area
                and i not in hidden_frames
                and difference(patch(data, area, box), final_patch) <= .025), None)
            action.update(glyph_screen_rect=box, visible_ms=first_match,
                          queued_to_visible_ms=first_match-action['posted_ms'] if first_match is not None else None,
                          occluded_glyph_frames=hidden_frames)
            assert first_match is not None
        observer.pump()
        report['foreground_events'] = [e for e in observer.events if e['pid']==process.pid]
        assert not report['foreground_events']
        assert not any(action['occluded_glyph_frames'] for action in report['actions']), 'Glyph cells occluded'
        report['first_visible_character_ms'] = report['actions'][0]['visible_ms']
        report['max_queued_to_visible_ms'] = max(a['queued_to_visible_ms'] for a in report['actions'])
        report['status'] = 'passed_posted_character_measurement'
        print('GLYPHS', round(report['first_visible_character_ms'], 3), 'ms from launch; maximum queue-to-glyph',
              round(report['max_queued_to_visible_ms'], 3), 'ms', flush=True)
        win.png(evidence/'before-input.png', before[2], *before[1][2:])
        win.png(evidence/'visible-input.png', final_data, *final_area[2:])
    except Exception as exc:
        report['status'], report['error'] = 'failed', repr(exc)
        print('FAILED', repr(exc), flush=True)
    finally:
        if recorder:
            recorder.close()
        observer.pump()
        if process:
            report['foreground_events'] = [e for e in observer.events if e['pid']==process.pid]
        observer.close()
        if editor:
            win.message(editor, 2014, timeout=1000)
        if main_hwnd:
            win.U.PostMessageW(main_hwnd, 0x10, 0, 0)
        if process:
            try:
                report['process_exit'] = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()
                report['process_exit'] = process.wait(timeout=5)
                report['status'], report['error'] = 'failed', 'Fixture failed to close normally'
        with (evidence/'frames.bgra.zlib').open('wb') as stream:
            compressor = zlib.compressobj(1)
            for stamp, area, data in frames:
                report['frames'].append(dict(ms=stamp, area=area))
                stream.write(compressor.compress(data))
            stream.write(compressor.flush())
        (evidence/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('RESULT', report['status'], 'exit', report.get('process_exit'), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', type=int, default=1)
    parser.add_argument('--character', choices=['X', '#'], default='X')
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--capture', choices=['window', 'screen'], default='window')
    parser.add_argument('--runtime', default='startup-input44')
    args = parser.parse_args()
    win.U.SetProcessDPIAware()
    if args.capture == 'window':
        WindowFrames.prepare()
    runtime = (OUT/'runtime'/args.runtime).resolve()
    assert runtime.is_relative_to(OUT/'runtime')
    reports = [run(runtime, index, args.character, args.trace, args.capture)
               for index in range(args.runs)]
    raise SystemExit(0 if all(r['status']=='passed_posted_character_measurement' and r['process_exit']==0
                             for r in reports) else 1)


if __name__=='__main__':
    main()
