"""Exercise early background editing through later UI/session restoration."""
import argparse
import copy as copy_module
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any
import xml.etree.ElementTree as ET

from background_zorder import inspect
from foreground_events import Observer
from measure_posted_input import prepare
from native_xml import write_xml
from record_startup_menu import WindowCapture, read_rgb  # pyright: ignore[reportMissingImports]
from window_surface import uncloaked
import win32_probe as win

OUT = Path(__file__).resolve().parent


def run(runtime: Path, trace: bool, resume: bool = False, scenario: str = 'main') -> None:
    folder = OUT/'measurements'/('editor-first-'+runtime.name+'-'+time.strftime('%Y%m%d-%H%M%S'))
    folder.mkdir()
    settings = {}
    for directory in ('settings', 'appdata', 'localappdata', 'plugins/Config'):
        for path in (runtime/directory).rglob('*'):
            if path.is_file():
                settings[path.relative_to(runtime)] = path.read_bytes()
    for name in ('config.xml', 'session.xml', 'pluginStartup.ini'):
        settings[Path(name)] = (runtime/name).read_bytes()
    manifest = json.loads((runtime/'session-fixture.json').read_text(encoding='utf-8'))
    for record in manifest['documents']:
        for copy in record['copies']:
            settings[Path(copy['path'])] = (runtime/copy['path']).read_bytes()
    prepare(runtime)
    if scenario == 'secondary':
        for session_path in (runtime/'session.xml', runtime/'settings/session.xml'):
            tree = ET.parse(session_path)
            session = tree.find('./Session')
            assert session is not None
            session.set('activeView', '1')
            secondary = session.find('./subView')
            selected = session.find('./mainView/File[44]')
            assert secondary is not None and selected is not None
            secondary.clear()
            secondary.set('activeIndex', '0')
            secondary.append(copy_module.deepcopy(selected))
            write_xml(tree, session_path)
    expected_documents = 1 if scenario in ('blank', 'commandline') else 44
    expected_index = expected_documents-1
    report: dict[str, Any] = dict(status='incomplete', input='background WM_CHAR; not system keyboard',
                  exe_sha256=hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest(),
                  source=json.loads((runtime/'tab-preparation.json').read_text(encoding='utf-8')),
                  trace=trace, resume_during_completion=resume, scenario=scenario, actions=[])
    observer = Observer()
    capture = WindowCapture(OUT/'skill-capture-build/window_capture.dll', fps=30, frames=60)
    process, hwnd, editor = None, None, None
    print('START', folder.name, flush=True)
    try:
        env = dict(os.environ, APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
                   TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'))
        env.pop('NPP_STARTUP_TRACE', None)
        if trace:
            env['NPP_STARTUP_TRACE'] = str(folder/'startup.tsv')
        start = time.perf_counter()
        report['launch_qpc_seconds'] = start
        command = [str(runtime/'notepad++.exe'), '-multiInst', '-noActivate', '-simple', '-settingsDir=settings']
        if scenario in ('blank', 'commandline'):
            command.append('-nosession')
        if scenario == 'commandline':
            command.append('input-target.txt')
        process = subprocess.Popen(command, cwd=runtime, env=env)
        report['pid'] = process.pid
        while time.perf_counter() < start+5:
            observer.pump()
            assert process.poll() is None
            roots = [w for w in win.windows(process.pid) if w['class']=='Notepad++' and w['visible'] and uncloaked(w['hwnd'])]
            if roots:
                hwnd = roots[0]['hwnd']
                views = [w for w in win.windows(parent=hwnd) if w['class']=='Scintilla' and w['visible']]
                if views:
                    editor = max(views, key=lambda v: win.rect(v['hwnd'])[2]*win.rect(v['hwnd'])[3])['hwnd']
                    break
            time.sleep(.003)
        assert hwnd and editor
        assert not win.U.GetMenu(hwnd), 'First editor must precede menu construction'
        assert win.message(editor, 2006, timeout=300) == 0
        report['editor_hwnd'] = editor
        report['document_before'] = win.message(editor, 2357, timeout=300)
        report['early_editor_rect'] = win.rect(editor, client=True)
        report['window_rect'] = win.rect(hwnd)
        report['early_glyph_point'] = [win.message(editor, 2164, 0, 0), win.message(editor, 2165, 0, 0)]
        report['early_line_height'] = win.message(editor, 2279, 0)
        capture.start(hwnd, process.pid)
        # Keep a blank reference frame before posting; compositor timestamps
        # can lead callback receipt slightly, so retain two frames first.
        while capture.state()['gpu_copies'] < 2:
            observer.pump()
            assert time.perf_counter() < start+5
            time.sleep(.003)
        first = time.perf_counter()
        while time.perf_counter() < first+(3.5 if resume else 2.3):
            observer.pump()
            assert process.poll() is None
            now = time.perf_counter()
            if 'menu_observed_ms' not in report and win.U.GetMenu(hwnd):
                report['menu_observed_ms'] = (now-start)*1000
            if len(report['actions']) < 10 and now-first >= len(report['actions'])*.07:
                report['actions'].append(dict(posted_ms=(now-start)*1000))
                assert win.U.PostMessageW(editor, 0x102, ord('Z'), 1)
            elif resume and 10 <= len(report['actions']) < 20:
                due = (first-start)*1000 + 1200 + (len(report['actions'])-10)*70
                if (now-start)*1000 >= due:
                    report['actions'].append(dict(posted_ms=(now-start)*1000))
                    assert win.U.PostMessageW(editor, 0x102, ord('Z'), 1)
            time.sleep(.003)
        capture.pause()
        assert capture.state()['cpu_readbacks'] == 0
        expected_length = 20 if resume else 10
        assert len(report['actions']) == expected_length
        assert not win.U.GetMenu(hwnd), 'Simple mode must keep the menu hidden after input pause'
        deadline = time.perf_counter()+8
        while True:
            observer.pump()
            assert process.poll() is None
            if (win.message(hwnd, 2031, 0, 1, timeout=1500) == expected_documents and
                    win.message(hwnd, 2047, 0, 0, timeout=1500) == expected_index):
                break
            assert time.perf_counter() < deadline, 'Saved tabs must finish restoring'
            time.sleep(.02)
        assert win.message(hwnd, 2031, 0, 1, timeout=1500) == expected_documents
        assert win.message(hwnd, 2047, 0, 0, timeout=1500) == expected_index
        report['document_after'] = win.message(editor, 2357, timeout=1500)
        report['length_after'] = win.message(editor, 2006, timeout=1500)
        report['caret_after'] = win.message(editor, 2008, timeout=1500)
        report['tab_buffers'] = [win.message(hwnd, 2083, i, 0, timeout=1500) for i in range(expected_documents)]
        report['active_buffer'] = win.message(hwnd, 2084, timeout=1500)
        if scenario == 'secondary':
            assert win.message(hwnd, 2031, 0, 2, timeout=1500) == 1
            assert win.message(hwnd, 2083, 0, 1, timeout=1500) == report['tab_buffers'][-1]
            assert report['active_buffer'] == report['tab_buffers'][-1]
        report['editor_states'] = [dict(hwnd=w['hwnd'], visible=w['visible'], rect=win.rect(w['hwnd']),
            document=win.message(w['hwnd'], 2357, timeout=1000), length=win.message(w['hwnd'], 2006, timeout=1000))
            for w in win.windows(parent=hwnd) if w['class']=='Scintilla']
        assert report['document_after'] == report['document_before']
        assert win.message(editor, 2006, timeout=1500) == expected_length
        assert win.message(editor, 2008, timeout=1500) == expected_length
        assert win.message(editor, 2009, timeout=1500) == expected_length
        assert all(win.message(editor, 2007, i, timeout=1500) == ord('Z') for i in range(expected_length))
        report['final_editor_rect'] = win.rect(editor, client=True)
        report['final_glyph_point'] = [win.message(editor, 2164, 0, 0), win.message(editor, 2165, 0, 0)]
        assert win.message(editor, 2174, timeout=1000), 'Undo must remain available'
        win.message(editor, 2176, timeout=1000)
        # Scintilla may group consecutive characters into one undo action.
        report['after_undo_length'] = win.message(editor, 2006, timeout=1000)
        assert report['after_undo_length'] is not None and report['after_undo_length'] < expected_length
        win.message(editor, 2011, timeout=1000)
        assert win.message(editor, 2006, timeout=1000) == expected_length
        assert inspect(process.pid)['passed']
        required = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))['plugins']
        deadline = time.perf_counter()+5
        while True:
            mapped = {str(Path(p).resolve()).lower() for p in win.modules(process.pid)}
            matches = [p for p in required if str((runtime/'plugins'/p['name']/(p['name']+'.dll')).resolve()).lower() in mapped]
            if len(matches) == 14:
                break
            assert time.perf_counter() < deadline
            observer.pump()
            time.sleep(.01)
        for p in matches:
            assert hashlib.sha256((runtime/'plugins'/p['name']/(p['name']+'.dll')).read_bytes()).hexdigest() == p['sha256']
        report['plugin_count'] = len(matches)
        win.message(editor, 2014, timeout=1000)
        win.U.PostMessageW(hwnd, 0x10, 0, 0)
        assert process.wait(timeout=8) == 0
        observer.pump()
        report['foreground_events'] = [e for e in observer.events if e['pid']==process.pid]
        assert not report['foreground_events']
        saved = capture.save(folder/'frames')
        report['first_frame_ms'] = (saved['frames'][0]['compositor_qpc_seconds']-start)*1000
        ex, ey, _, _ = report['early_editor_rect']
        wx, wy, _, _ = report['window_rect']
        gx, gy = report['early_glyph_point']
        assert gx is not None and gy is not None
        line_height = report['early_line_height']
        assert line_height is not None and line_height > 0
        left, top = ex-wx+gx, ey-wy+gy
        rows = []
        for frame in saved['frames']:
            ms = (frame['compositor_qpc_seconds']-start)*1000
            if ms >= report.get('menu_observed_ms', float('inf')):
                break
            width, _, pixels = read_rgb(folder/'frames'/frame['file'])
            bright = sum(max(pixels[(y*width+x)*3:(y*width+x)*3+3]) > 100
                         for y in range(top, top+line_height)
                         for x in range(left, left+30))
            rows.append(dict(ms=ms, file=frame['file'], bright=bright))
        assert rows and rows[0]['ms'] < report['actions'][0]['posted_ms']
        report['early_glyph_frames'] = rows
        report['first_message_glyph_ms'] = next(row['ms'] for row in rows
            if row['ms'] >= report['actions'][0]['posted_ms'] and row['bright'] > rows[0]['bright']+10)
        report['status'] = 'passed_early_edit_preservation'
        print('PRESERVED', report['first_frame_ms'], 'ms first frame;', scenario, expected_documents,
              'primary documents, 14 plugins, undo/redo and caret', flush=True)
    except Exception as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        capture.pause()
        if process and process.poll() is None:
            if editor:
                win.message(editor, 2014, timeout=1000)
            if hwnd:
                win.U.PostMessageW(hwnd, 0x10, 0, 0)
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
                report['forced_failure_exit'] = True
        if process:
            report['target_exit'] = process.poll()
        if capture.started and capture.state()['gpu_copies'] and not (folder/'frames').exists():
            capture.save(folder/'frames')
        capture.close()
        observer.close()
        (folder/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        for path, data in settings.items():
            (runtime/path).parent.mkdir(parents=True, exist_ok=True)
            destination = runtime/path
            if not destination.exists() or destination.read_bytes() != data:
                destination.write_bytes(data)
        print('RESULT', report['status'], report.get('target_exit'), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', choices=['startup-idle-default44', 'startup-idle-normal44'], default='startup-idle-default44')
    parser.add_argument('--trace', action='store_true')
    parser.add_argument('--resume-during-completion', action='store_true')
    parser.add_argument('--scenario', choices=['main', 'secondary', 'blank', 'commandline'], default='main')
    args = parser.parse_args()
    run((OUT/'runtime'/args.runtime).resolve(), args.trace, args.resume_during_completion, args.scenario)


if __name__ == '__main__':
    main()
