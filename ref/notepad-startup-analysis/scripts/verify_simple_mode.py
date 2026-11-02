"""Check mode persistence, startup flags and configurable keys in owned copies."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable
import xml.etree.ElementTree as ET

from background_zorder import inspect
from foreground_events import Observer
from native_xml import write_xml
from record_startup_menu import WindowCapture  # pyright: ignore[reportMissingImports]
from window_surface import uncloaked
import win32_probe as win

OUT = Path(__file__).resolve().parent
MODE = 44110


def set_mode(runtime: Path, enabled: bool) -> None:
    for path in (runtime/'config.xml', runtime/'settings/config.xml'):
        tree = ET.parse(path)
        configs = tree.find('./GUIConfigs')
        assert configs is not None
        mode = configs.find('./GUIConfig[@name="SimpleMode"]')
        if mode is None:
            mode = ET.SubElement(configs, 'GUIConfig', name='SimpleMode')
        mode.set('enabled', 'yes' if enabled else 'no')
        write_xml(tree, path)


def saved_mode(runtime: Path) -> bool:
    mode = ET.parse(runtime/'settings/config.xml').find('./GUIConfigs/GUIConfig[@name="SimpleMode"]')
    assert mode is not None
    return mode.get('enabled') == 'yes'


def set_key(runtime: Path, key: int) -> None:
    for path in (runtime/'shortcuts.xml', runtime/'settings/shortcuts.xml'):
        tree = ET.parse(path)
        commands = tree.find('./InternalCommands')
        assert commands is not None
        command = commands.find(f'./Shortcut[@id="{MODE}"]')
        if command is None:
            command = ET.SubElement(commands, 'Shortcut', id=str(MODE))
        command.attrib.update(Ctrl='no', Alt='no', Shift='no', Key=str(key))
        write_xml(tree, path)


def wait_for(predicate: Callable[[], Any], observer: Observer, process: subprocess.Popen[Any], seconds: float = 10) -> Any:
    deadline = time.perf_counter()+seconds
    while True:
        observer.pump()
        assert process.poll() is None, process.returncode
        value = predicate()
        if value:
            return value
        assert time.perf_counter() < deadline, 'Timed out waiting for mode or restored session'
        time.sleep(.005)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', default='startup-idle-default44', choices=['startup-idle-default44', 'startup-idle-normal44'])
    parser.add_argument('--case', type=int, choices=range(4))
    parser.add_argument('--keep-state', action='store_true')
    parser.add_argument('--result-file', type=Path)
    args = parser.parse_args()
    runtime = (OUT/'runtime'/args.runtime).resolve()
    suffix = '' if args.case is None else f'-case{args.case}'
    folder = OUT/'measurements'/('simple-mode-'+runtime.name+'-'+time.strftime('%Y%m%d-%H%M%S')+suffix)
    folder.mkdir()
    snapshot = {}
    for directory in ('settings', 'appdata', 'localappdata', 'plugins/Config'):
        for path in (runtime/directory).rglob('*'):
            if path.is_file():
                snapshot[path.relative_to(runtime)] = path.read_bytes()
    for name in ('config.xml', 'session.xml', 'shortcuts.xml', 'pluginStartup.ini'):
        snapshot[Path(name)] = (runtime/name).read_bytes()
    manifest = json.loads((runtime/'session-fixture.json').read_text(encoding='utf-8'))
    for document in manifest['documents']:
        for item in document['copies']:
            snapshot[Path(item['path'])] = (runtime/item['path']).read_bytes()
    report: dict[str, Any] = dict(status='incomplete', runs=[], input='No text; posted function keys',
        exe_sha256=hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest(),
        source=json.loads((runtime/'tab-preparation.json').read_text(encoding='utf-8')))
    observer = Observer()
    process, hwnd, recorder = None, None, None
    required = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))['plugins']
    try:
        scenarios: list[dict[str, Any]] = [dict(name='full-default', simple=False, flag=False, key=0x7B, early=False),
                     dict(name='saved-simple', simple=True, flag=False, key=0x7B, early=False),
                     dict(name='flag-simple-early-switch', simple=True, flag=True, key=0x7B, early=True),
                     dict(name='remapped-simple-early-switch', simple=True, flag=False, key=0x78, early=True)]
        if args.case is None:
            # A fresh capture process per HWND avoids reusing native global capture state.
            for index in range(len(scenarios)):
                result_path = folder/f'worker-{index}.json'
                command = ['uv', 'run', '--offline', '--no-project', 'python', '-X', 'utf8', str(Path(__file__).resolve()),
                           '--runtime', args.runtime, '--case', str(index), '--keep-state',
                           '--result-file', str(result_path)]
                worker = subprocess.run(command, check=False)
                result = json.loads(result_path.read_text(encoding='utf-8'))
                report['runs'].extend(result['runs'])
                assert worker.returncode == 0 and result['status'] == 'passed', result
            report['status'] = 'passed'
            return
        scenarios = [scenarios[args.case]]
        if args.case == 0:
            set_mode(runtime, False)
        for scenario in scenarios:
            if scenario['name'] == 'remapped-simple-early-switch':
                set_mode(runtime, True)
                set_key(runtime, scenario['key'])
            recorder = WindowCapture(OUT/'skill-capture-build/window_capture.dll', fps=2, frames=16)
            run: dict[str, Any] = dict(scenario=scenario, actions=[])
            report['runs'].append(run)
            env = dict(os.environ, APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
                TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'))
            env['NPP_STARTUP_TRACE'] = str(folder/(scenario['name']+'.tsv'))
            command = [str(runtime/'notepad++.exe'), '-multiInst', '-noActivate', '-settingsDir=settings']
            if scenario['flag']:
                assert not saved_mode(runtime)
                command.append('-simple')
            print('START', scenario['name'], flush=True)
            start = time.perf_counter()
            process = subprocess.Popen(command, cwd=runtime, env=env)
            run.update(pid=process.pid, launch_qpc_seconds=start)
            def find_window(target=process):
                return next((w['hwnd'] for w in win.windows(target.pid) if w['class']=='Notepad++' and
                    w['visible'] and uncloaked(w['hwnd'])), None)
            hwnd = wait_for(find_window, observer, process)
            run['visible_observed_ms'] = (time.perf_counter()-start)*1000
            assert bool(win.U.GetMenu(hwnd)) == (not scenario['simple'])
            run['menu_at_first_observation'] = bool(win.U.GetMenu(hwnd))
            views = [w for w in win.windows(parent=hwnd) if w['class']=='Scintilla' and w['visible']]
            assert views
            editor = max(views, key=lambda w: win.rect(w['hwnd'])[2]*win.rect(w['hwnd'])[3])['hwnd']
            original_document = win.message(editor, 2357)
            original_position = win.message(editor, 2008)
            def loaded(target=process):
                mapped = {str(Path(p).resolve()).lower() for p in win.modules(target.pid)}
                return all(str((runtime/'plugins'/p['name']/(p['name']+'.dll')).resolve()).lower() in mapped for p in required)
            if not scenario['simple']:
                assert loaded(), 'Full mode must load all plugins before its first visible window'
                assert win.message(hwnd, 2031, 0, 1) == 44
            recorder.start(hwnd, process.pid)
            capture_state = recorder.state
            wait_for(lambda: capture_state()['gpu_copies'] > 0, observer, process)
            run['pre_toggle_gpu_frames'] = recorder.state()['gpu_copies']
            if not scenario['early']:
                wait_for(loaded, observer, process)
                wait_for(lambda: win.message(hwnd, 2031, 0, 1) == 44, observer, process)
            before = time.perf_counter()
            run['key_posted_ms'] = (before-start)*1000
            assert win.U.PostMessageW(editor, 0x100, scenario['key'], 1)
            assert win.U.PostMessageW(editor, 0x101, scenario['key'], 0xC0000001)
            expected_simple = not scenario['simple']
            wait_for(lambda: bool(win.U.GetMenu(hwnd)) == (not expected_simple), observer, process)
            run['actions'].append(dict(key=scenario['key'], to_simple=expected_simple,
                menu_change_observed_ms=(time.perf_counter()-before)*1000))
            wait_for(loaded, observer, process)
            wait_for(lambda: win.message(hwnd, 2031, 0, 1) == 44, observer, process)
            assert saved_mode(runtime) == expected_simple
            assert win.message(hwnd, 2084) != 0
            assert win.message(editor, 2357) == original_document
            assert win.message(editor, 2008) == original_position
            assert win.message(hwnd, 2076) == expected_simple  # NPPM_ISTABBARHIDDEN
            assert win.message(hwnd, 2095) == expected_simple  # NPPM_ISTOOLBARHIDDEN
            assert win.message(hwnd, 2099) == expected_simple  # NPPM_ISSTATUSBARHIDDEN
            run['controls'] = [w for w in win.windows(parent=hwnd) if w['class'] in ('SysTabControl32', 'ReBarWindow32', 'msctls_statusbar32')]
            if expected_simple:
                assert not any(w['visible'] for w in run['controls'])
                assert not any(w['visible'] for w in win.windows(process.pid) if w['class']=='#32770')
                run['simple_editor_rect'] = win.rect(editor)
                run['simple_client_rect'] = win.rect(hwnd, client=True)
                assert run['simple_editor_rect'] == run['simple_client_rect']
            for plugin in required:
                path = runtime/'plugins'/plugin['name']/(plugin['name']+'.dll')
                assert hashlib.sha256(path.read_bytes()).hexdigest() == plugin['sha256']
            assert inspect(process.pid)['passed']
            until = time.perf_counter()+.6
            while time.perf_counter() < until:
                observer.pump()
                time.sleep(.01)
            recorder.pause()
            assert recorder.state()['cpu_readbacks'] == 0
            assert win.U.PostMessageW(hwnd, 0x10, 0, 0)
            assert process.wait(timeout=10) == 0
            observer.pump()
            run['foreground_events'] = [event for event in observer.events if event['pid']==process.pid]
            assert not run['foreground_events']
            capture = recorder.save(folder/scenario['name'])
            run.update(target_exit=process.returncode, frames=len(capture['frames']),
                first_captured_frame_ms=(capture['frames'][0]['compositor_qpc_seconds']-start)*1000)
            assert saved_mode(runtime) == expected_simple
            recorder.close()
            recorder = None
            print('PASS', scenario['name'], run['actions'], flush=True)
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        if recorder:
            recorder.pause()
        if process and process.poll() is None:
            if hwnd:
                win.U.PostMessageW(hwnd, 0x10, 0, 0)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
                report['forced_failure_exit'] = True
        if recorder:
            if recorder.started and recorder.state()['gpu_copies']:
                recorder.save(folder/'failed-frames')
            recorder.close()
        observer.close()
        (folder/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        if args.result_file:
            args.result_file.write_text(json.dumps(report, indent=2), encoding='utf-8')
        if not args.keep_state:
            for relative, content in snapshot.items():
                path = runtime/relative
                if not path.exists() or path.read_bytes() != content:
                    path.write_bytes(content)
        print('RESULT', report['status'], folder.name, flush=True)


if __name__ == '__main__':
    main()
