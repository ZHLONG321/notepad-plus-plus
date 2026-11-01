"""Launch-only native screen capture. Never sends keyboard or mouse input."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import zlib
import ctypes as C
from ctypes import wintypes as W
from typing import Any
import win32_probe as win
import wait_chain
from foreground_events import Observer
import native_ui_record
from frame_review import compare

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
win.bind(win.U, 'GetWindowLongPtrW', C.c_ssize_t, W.HWND, C.c_int)
win.bind(win.U, 'GetWindow', W.HWND, W.HWND, W.UINT)
win.bind(win.U, 'IsIconic', W.BOOL, W.HWND)


def covering_windows(hwnd, area):
    x,y,width,height=area
    covered=[]
    other=win.U.GetWindow(hwnd,3)  # GW_HWNDPREV: preceding window in Z order.
    while other:
        if win.U.IsWindowVisible(other) and not win.U.IsIconic(other):
            left,top,w,h=win.rect(other)
            if max(x,left)<min(x+width,left+w) and max(y,top)<min(y+height,top+h):
                covered.append({'hwnd':other,'class':win.text(other,True)})
        other=win.U.GetWindow(other,3)
    return covered


def run(runtime: Path, trace: bool, restore: bool, index: int, waitchain: bool) -> dict[str, Any]:
    assert runtime.is_relative_to(OUT / 'runtime')
    evidence = OUT / 'measurements' / f'launch-{runtime.name}-{time.strftime("%Y%m%d-%H%M%S")}-{index:02d}'
    evidence.mkdir(parents=True)
    env = dict(os.environ, APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'), TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'))
    if trace:
        env['NPP_STARTUP_TRACE'] = str(evidence/'startup.csv')
    else:
        env.pop('NPP_STARTUP_TRACE', None)
    command = [str(runtime/'notepad++.exe'), '-multiInst', '-noActivate', *([] if restore else ['-nosession']), '-settingsDir='+str(runtime/'settings')]
    audit = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))
    expected = {str((runtime/'plugins'/item['name']/(item['name']+'.dll')).resolve()).lower(): item for item in audit['plugins']}
    plugin_hashes = {item['name']: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path, item in expected.items()}
    if any(plugin_hashes[item['name']] != item['sha256'] for item in audit['plugins']):
        raise RuntimeError('A plugin entry DLL differs from the original installed release')
    report: dict[str, Any] = {'runtime': runtime.relative_to(ROOT).as_posix(), 'restore': restore, 'trace': trace, 'input_test': 'omitted_by_user_instruction', 'exe_sha256': hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest(), 'frames': [], 'status': 'incomplete', 'runner_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(), 'cache': 'unflushed; no preloaded editor'}
    frames = []
    report['capture_conditions']={'topmost_requested':False,'covered_frames':[]}
    report['original_plugin_sha256'] = plugin_hashes
    report['settings_sha256_before_launch'] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                                               for p in sorted((runtime/'settings').glob('*.xml'))}
    report['command'] = [os.path.relpath(command[0], ROOT), *command[1:-1], '-settingsDir='+os.path.relpath(runtime/'settings', ROOT)]
    report['plugin_startup_ini'] = (runtime/'pluginStartup.ini').read_text(encoding='utf-8') if (runtime/'pluginStartup.ini').exists() else 'absent: all normal'
    last_modules_check = 0
    process = None
    main = None
    sampler = None
    observer = Observer()
    started = time.perf_counter()
    print('START', runtime.name, index, evidence.relative_to(ROOT), flush=True)
    try:
        report['foreground_before'] = win.U.GetForegroundWindow()
        process = subprocess.Popen(command, cwd=runtime, env=env)
        report['pid'] = process.pid
        report['start_qpc_seconds'] = started
        while time.perf_counter()-started < 6:
            observer.pump()
            if process.poll() is not None:
                raise RuntimeError('Process exited before capture')
            if main is None:
                found = [w for w in win.windows(process.pid) if w['class']=='Notepad++']
                if found:
                    main = found[0]['hwnd']
                    if waitchain:
                        report['wait_chains'] = []
                        sampler = wait_chain.start(win.U.GetWindowThreadProcessId(main, None), started, report['wait_chains'])
            if main and win.U.IsWindowVisible(main):
                area = win.rect(main)
                if area[2] > 200 and area[3] > 100:
                    covering=covering_windows(main,area)
                    if covering:
                        report['capture_conditions']['covered_frames'].append({'frame':len(frames),'windows':covering})
                    data = win.capture(area)
                    frames.append(((time.perf_counter()-started)*1000, area, data))
                    now = time.perf_counter()
                    if now-last_modules_check > .1:
                        loaded = {str(Path(path).resolve()).lower() for path in win.modules(process.pid)}
                        if expected.keys() <= loaded and 'all_plugins_mapped_ms' not in report:
                            report['all_plugins_mapped_ms'] = (now-started)*1000
                        last_modules_check = now
                    # Original jN start.js starts its includes after a 1000-ms timer.
                    # Keep both modes open equally long before comparing menu state.
                    if ('all_plugins_mapped_ms' in report and frames[-1][0]-frames[0][0] > 1550
                            and frames[-1][0]-report['all_plugins_mapped_ms'] > 550):
                        break
            time.sleep(.008)
        if not frames:
            raise RuntimeError('No visible screen frame within 4 seconds')
        report['modules'] = [os.path.relpath(p, ROOT).replace('\\', '/') for p in win.modules(process.pid)]
        report['plugin_entry_dlls'] = [p for p in report['modules'] if str((ROOT/p).resolve()).lower() in expected]
        if len(report['plugin_entry_dlls']) != len(expected):
            raise RuntimeError('Not all original plugins completed mapping before capture deadline')
        report['windows'] = win.windows(parent=main)
        report['visible_scintilla_rects'] = [win.rect(w['hwnd'], client=True) for w in report['windows']
                                            if w['class']=='Scintilla' and w['visible']]
        report['foreground_after'] = win.U.GetForegroundWindow()
        observer.pump()
        report['foreground_events'] = [event for event in observer.events if event['pid']==process.pid]
        handle = win.K.OpenProcess(0x410, False, process.pid)
        try:
            for event in report['foreground_events']:
                title = C.create_unicode_buffer(256)
                win.U.GetWindowTextW(event['hwnd'], title, len(title))
                event['title'] = title.value
                instance = win.U.GetWindowLongPtrW(event['hwnd'], -6)
                module = C.create_unicode_buffer(32768)
                if win.K.K32GetModuleFileNameExW(handle, instance, module, len(module)):
                    event['module'] = os.path.relpath(module.value, ROOT).replace('\\', '/')
        finally:
            if handle:
                win.K.CloseHandle(handle)
        report['top_level_windows'] = win.windows(process.pid)
        report['native_ui'] = native_ui_record.record(main)
        # Public native query messages, with no pointer arguments or UI commands.
        report['open_files_count'] = win.message(main, 1024+1000+7, 0, 0)
        report['primary_open_files_count'] = win.message(main, 1024+1000+7, 0, 1)
        report['secondary_open_files_count'] = win.message(main, 1024+1000+7, 0, 2)
        report['main_document_index'] = win.message(main, 1024+1000+23, 0, 0)
        report['visible_document_lengths'] = [win.message(w['hwnd'], 2006) for w in report['windows']
                                               if w['class']=='Scintilla' and w['visible']]
        fixture = runtime/'session-fixture.json'
        if fixture.exists() and restore:
            expected_session = json.loads(fixture.read_text(encoding='utf-8'))
            # ALL_OPEN_FILES includes the hidden second view's empty placeholder.
            assert report['primary_open_files_count'] == expected_session['expected_open_files'], 'Session document count mismatch'
            assert report['main_document_index'] == expected_session['expected_main_index'], 'Session selection mismatch'
        report['took_foreground_at_end'] = report['foreground_after']==main
        if report['took_foreground_at_end'] or report['foreground_events']:
            raise RuntimeError('The launch-only test window took foreground focus')
        report['status'] = 'captured_requires_visual_review'
    except Exception as exc:
        report['status'] = 'failed'
        report['error'] = repr(exc)
        print('FAILED', repr(exc), flush=True)
    finally:
        observer.close()
        if sampler:
            sampler[0].set()
            sampler[1].join(timeout=1)
        if main:
            win.U.PostMessageW(main, 0x10, 0, 0)
        if process:
            try:
                report['process_exit'] = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                report['process_exit'] = 'failed_to_close_within_5_seconds'
                process.terminate()
                report['forced_process_exit'] = process.wait(timeout=5)
                report['status'] = 'failed'
        print('CLOSED', report.get('process_exit'), 'frames', len(frames), flush=True)
    if frames:
        _, final_area, final = frames[-1]
        width, height = final_area[2:]
        win.png(evidence/'settled.png', final, width, height)
        ready, ready_data, editor_ready = None, None, None
        editor_region = None
        if report.get('visible_scintilla_rects'):
            ex, ey, ew, eh = max(report['visible_scintilla_rects'], key=lambda r: r[2]*r[3])
            editor_region = (max(0, ex-final_area[0]), max(0, ey-final_area[1]),
                             min(width, ex-final_area[0]+ew), min(height, ey-final_area[1]+eh))
        report['frame_comparison_method'] = '8x6 tiles; 3px sampling; RGB tolerance 12; each tile <0.001 changed; continuous match to final capture'
        report['editor_region_in_capture'] = editor_region
        for stamp, area, data in frames:
            difference, white, editor_difference = 1., None, None
            if area == final_area:
                difference, white = compare(data, final, width, height)
                if editor_region:
                    editor_difference, _ = compare(data, final, width, height, editor_region)
            if difference >= .001:
                ready, ready_data = None, None
            elif ready is None:
                ready, ready_data = stamp, data
            if editor_difference is None or editor_difference >= .001:
                editor_ready = None
            elif editor_ready is None:
                editor_ready = stamp
            report['frames'].append({'ms': stamp, 'area': area, 'max_tile_difference': difference,
                                     'max_tile_near_white_fraction': white, 'editor_tile_difference': editor_difference})
        report['first_complete_frame_ms'] = ready
        report['first_editor_settled_ms'] = editor_ready
        if report['capture_conditions']['covered_frames']:
            report['screen_comparison_ms_invalid_due_to_occlusion']={
                'complete':report['first_complete_frame_ms'],'editor':report['first_editor_settled_ms']}
            report['first_complete_frame_ms']=None
            report['first_editor_settled_ms']=None
        if ready_data:
            win.png(evidence/'first-complete-frame.png', ready_data, width, height)
        with (evidence/'frames.bgra.zlib').open('wb') as stream:
            compressor = zlib.compressobj(1)
            for _, _, data in frames:
                stream.write(compressor.compress(data))
            stream.write(compressor.flush())
    (evidence/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print('RESULT', report['status'], 'complete_ms', report.get('first_complete_frame_ms'), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('runtime')
    parser.add_argument('--runs', type=int, default=1)
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--restore', action='store_true')
    parser.add_argument('--waitchain', action='store_true')
    args = parser.parse_args()
    win.U.SetProcessDPIAware()
    reports = [run((ROOT/args.runtime).resolve(), args.trace, args.restore, i, args.waitchain) for i in range(args.runs)]
    raise SystemExit(0 if all(r['status']=='captured_requires_visual_review' and r.get('process_exit')==0 for r in reports) else 1)


if __name__ == '__main__':
    main()
