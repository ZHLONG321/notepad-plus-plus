"""Compare ordinary background compositor frames with requested native repainting."""
import ctypes as C
import argparse
from array import array
from collections import Counter
from ctypes import wintypes as W
import json
import hashlib
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
from window_snapshot import snapshot
from window_surface import uncloaked
import native_ui_record
from verify_plugin_transforms import command_id

OUT = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument('--runtime', default='startup-idle-default44', choices=['startup-idle-default44', 'startup-idle-normal44'])
parser.add_argument('--force-layout', action='store_true')
parser.add_argument('--modal', action='store_true')
parser.add_argument('--modal-onscreen', action='store_true')
parser.add_argument('--no-trace', action='store_true')
parser.add_argument('--startup-burst', action='store_true')
args = parser.parse_args()
runtime = OUT/'runtime'/args.runtime
evidence = OUT/'measurements'/('background-render-'+time.strftime('%Y%m%d-%H%M%S'))
evidence.mkdir()
report: dict[str, Any] = dict(status='incomplete', runtime=args.runtime,
    exe_sha256=hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest())
report['source_commit'] = json.loads((runtime/'tab-preparation.json').read_text(encoding='utf-8'))['source_commit']
report['trace_enabled'] = not args.no_trace
expected_plugins = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))['plugins']
plugin_paths = [runtime/'plugins'/p['name']/(p['name']+'.dll') for p in expected_plugins]
for path, plugin in zip(plugin_paths, expected_plugins):
    assert hashlib.sha256(path.read_bytes()).hexdigest() == plugin['sha256']
report['plugin_hashes'] = {p['name']: p['sha256'] for p in expected_plugins}
observer = Observer()
WindowFrames.prepare()
report['test_resource_limits'] = observer.resource_limits
report['capture_resource_limits'] = WindowFrames.resource_limits()
win.U.SetProcessDPIAware()
process, recorder, hwnd = None, None, None
original_rect = None
early_frames = []
burst_done = False
try:
    print('START', evidence.name, flush=True)
    environment = dict(os.environ, APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
        TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'))
    environment.pop('NPP_STARTUP_TRACE', None)
    if not args.no_trace:
        environment['NPP_STARTUP_TRACE'] = str(evidence/'startup.tsv')
    report['launch_qpc_seconds'] = time.perf_counter()
    process = subprocess.Popen([str(runtime/'notepad++.exe'), '-multiInst', '-noActivate', '-settingsDir=settings'],
        cwd=runtime, env=environment)
    report['pid'] = process.pid
    deadline = time.perf_counter()+6
    shown = None
    first = None
    report['first_frames'] = []
    while time.perf_counter() < deadline:
        observer.pump()
        assert process.poll() is None
        if hwnd is None:
            mains = [w for w in win.windows(process.pid) if w['class']=='Notepad++' and w['visible'] and uncloaked(w['hwnd'])]
            if mains:
                hwnd = mains[0]['hwnd']
                original_rect = win.rect(hwnd)
                if original_rect[0] < -10000 and original_rect[1] < -10000:
                    # Repair geometry saved by the previous offscreen-only run.
                    assert win.U.SetWindowPos(hwnd, 1, 80, 80, 0, 0, 0x1|0x10)
                    original_rect = win.rect(hwnd)
                report['capture_target_visible'] = bool(win.U.IsWindowVisible(hwnd))
                report['capture_begin_ms'] = (time.perf_counter()-report['launch_qpc_seconds'])*1000
                report['initial_background'] = inspect(process.pid)
                if report['capture_target_visible']:
                    assert report['initial_background']['passed']
                try:
                    recorder = WindowFrames(hwnd, deferred=args.startup_burst)
                except RuntimeError as error:
                    report['capture_start_error'] = str(error)
                    raise
                report['capture_ready_ms'] = (time.perf_counter()-report['launch_qpc_seconds'])*1000
        if hwnd and shown is None and win.U.IsWindowVisible(hwnd):
            shown = time.perf_counter()
            report['visible_observed_ms'] = (shown-report['launch_qpc_seconds'])*1000
            report['shown_background'] = inspect(process.pid)
            assert report['shown_background']['passed']
        if recorder and not burst_done:
            frame = None if args.startup_burst else recorder.next()
            if frame:
                if len(report['first_frames']) < 5:
                    number = len(report['first_frames'])
                    filename = f'early-frame-{number}.png'
                    # Encode and inspect pixels after target shutdown. PNG work
                    # during startup can otherwise delay the process under study.
                    early_frames.append(frame)
                    report['first_frames'].append(dict(file=filename,
                        compositor_ms=(frame[0]-report['launch_qpc_seconds'])*1000))
                if first is None:
                    first = frame
                    report['first_frame_compositor_ms'] = (frame[0]-report['launch_qpc_seconds'])*1000
                if shown is not None and time.perf_counter()-shown > 2.5:
                    win.png(evidence/'ordinary-settled.png', frame[3], *frame[2][2:])
                    break
            if args.startup_burst and shown is not None and time.perf_counter()-shown > .55:
                recorder.pause()
                burst_done = True
                report['capture_stopped_ms'] = (time.perf_counter()-report['launch_qpc_seconds'])*1000
                report['capture_storage_before_target_exit'] = recorder.storage_state()
                assert report['capture_storage_before_target_exit']['cpu_readbacks']==0
                assert 1<=report['capture_storage_before_target_exit']['retained_frames']<=5
        if burst_done and shown is not None and time.perf_counter()-shown > 2.5:
            break
        time.sleep(.01)
    assert hwnd and recorder and (first or burst_done)
    loaded = {str(Path(p).resolve()).lower() for p in win.modules(process.pid)}
    report['mapped_plugin_count'] = sum(str(p.resolve()).lower() in loaded for p in plugin_paths)
    assert report['mapped_plugin_count'] == len(plugin_paths) == 14
    report['windows'] = snapshot(process.pid)
    if args.force_layout:
        client = win.rect(hwnd)
        win.message(hwnd, 5, 0, (client[3] << 16) | client[2])
        report['forced_layout_windows'] = snapshot(process.pid)
        print('FORCED_LAYOUT', flush=True)
    report['native_requested'] = capture(hwnd, process.pid, evidence/'native-requested.png')
    if recorder and not burst_done:
        redraw = win.bind(win.U, 'RedrawWindow', W.BOOL, W.HWND, C.c_void_p, W.HANDLE, W.UINT)
        assert redraw(hwnd, None, None, 0x1|0x4|0x80|0x100|0x400)
        end = time.perf_counter()+.6
        latest = None
        while time.perf_counter() < end:
            observer.pump()
            latest = recorder.next() or latest
            time.sleep(.01)
        assert latest
        win.png(evidence/'after-native-repaint.png', latest[3], *latest[2][2:])
    report['background_after'] = inspect(process.pid)
    assert report['background_after']['passed']
    if args.modal:
        # Keep trial dialogs and their owner outside the physical display while
        # checking display-time z-order, before repeating normal geometry.
        if not args.modal_onscreen:
            assert win.U.SetWindowPos(hwnd, 1, -20000, -20000, 0, 0, 0x1|0x10)
        report['modal_trial_owner_rect'] = win.rect(hwnd)
        menus = native_ui_record.record(hwnd)
        report['modal'] = []
        for plugin in ('HEX-Editor', 'XML Tools'):
            identifier = command_id(menus['menus'], plugin, 'Options...')
            assert identifier is not None
            before = {window['hwnd'] for window in snapshot(process.pid) if window['visible']}
            assert win.U.PostMessageW(hwnd, 2072, 0, identifier)
            deadline = time.perf_counter()+3
            dialogs = []
            while time.perf_counter() < deadline:
                observer.pump()
                dialogs = [window for window in snapshot(process.pid) if window['visible']
                    and window['root']==window['hwnd'] and window['hwnd'] not in before
                    and window['class']=='#32770']
                if dialogs:
                    break
                time.sleep(.01)
            assert len(dialogs)==1, plugin
            dialog = dialogs[0]
            item: dict[str, Any] = dict(plugin=plugin, dialog=dialog, background=inspect(process.pid))
            report['modal'].append(item)
            try:
                assert item['background']['passed'], plugin
                item['capture'] = capture(dialog['hwnd'], process.pid, evidence/(plugin.replace(' ', '-')+'.png'))
                item['settled_background'] = []
                deadline = time.perf_counter()+.5
                while time.perf_counter() < deadline:
                    observer.pump()
                    state = inspect(process.pid)
                    item['settled_background'].append(state)
                    assert state['passed'], plugin
                    time.sleep(.025)
            finally:
                win.U.PostMessageW(dialog['hwnd'], 0x10, 0, 0)
                deadline = time.perf_counter()+2
                while win.U.IsWindowVisible(dialog['hwnd']) and time.perf_counter()<deadline:
                    observer.pump()
                    time.sleep(.01)
                assert not win.U.IsWindowVisible(dialog['hwnd'])
            print('MODAL_BACKGROUND', plugin, item['background']['passed'], flush=True)
    report['status'] = 'captured_for_visual_review'
finally:
    if recorder and args.startup_burst and not burst_done:
        recorder.pause()
    observer.pump()
    report['foreground_events'] = [event for event in observer.events if process and event['pid']==process.pid]
    observer.close()
    if hwnd:
        # Initialization can discard an early close; let the owned window finish
        # appearing before requesting its ordinary shutdown after a failed probe.
        deadline = time.perf_counter()+3
        while process and process.poll() is None and not win.U.IsWindowVisible(hwnd) and time.perf_counter()<deadline:
            time.sleep(.02)
        if original_rect and args.modal:
            assert win.U.SetWindowPos(hwnd, 1, original_rect[0], original_rect[1],
                original_rect[2], original_rect[3], 0x10)
        win.U.PostMessageW(hwnd, 0x10, 0, 0)
    if process:
        try:
            report['exit'] = process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            report['exit_error'] = 'Owned process did not exit within five seconds.'
    if recorder and args.startup_burst:
        while True:
            frame = recorder.next()
            if frame is None:
                break
            number = len(early_frames)
            early_frames.append(frame)
            report['first_frames'].append(dict(file=f'early-frame-{number}.png',
                compositor_ms=(frame[0]-report['launch_qpc_seconds'])*1000))
        report['capture_storage_after_readback'] = recorder.storage_state()
        report['capture_storage'] = 'GPU textures until target exit; CPU readback and PNG encoding afterwards'
        if early_frames:
            report['first_frame_compositor_ms'] = report['first_frames'][0]['compositor_ms']
    if recorder:
        recorder.close()
    for item, frame in zip(report.get('first_frames', []), early_frames):
        width, height = frame[2][2:]
        win.png(evidence/item['file'], frame[3], width, height)
        pixels = array('I')
        pixels.frombytes(frame[3])
        # BGRA on this little-endian Windows host. Keep raw color counts;
        # white text is legitimate, so these are measurements, not a pass rule.
        counts = Counter(pixels)
        near_white = sum(count for color, count in counts.items()
            if (color & 255)>=245 and ((color >> 8) & 255)>=245 and ((color >> 16) & 255)>=245)
        item['near_white_pixels'] = near_white
        item['near_white_fraction'] = near_white/(width*height)
        item['dominant_rgb'] = [dict(rgb=[(color >> 16)&255, (color >> 8)&255, color&255], pixels=count)
            for color, count in counts.most_common(4)]
    if early_frames:
        frame = early_frames[0]
        win.png(evidence/'first-frame.png', frame[3], *frame[2][2:])
    (evidence/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print('RESULT', report['status'], report.get('exit'), report['foreground_events'], flush=True)
