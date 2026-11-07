"""Restore a saved original plugin panel while switching editor-only presentation."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import time
from typing import Any
import xml.etree.ElementTree as ET

from background_zorder import inspect
from foreground_events import Observer
from print_owned_window import capture as print_window
from verify_saved_panels import prepare
from verify_simple_mode import wait_for  # pyright: ignore[reportMissingImports]
from window_snapshot import snapshot
from window_surface import uncloaked
import win32_probe as win

OUT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', required=True)
    parser.add_argument('--floating', action='store_true')
    args = parser.parse_args()
    runtime_root = (OUT/'runtime').resolve()
    runtime = (runtime_root/args.runtime).resolve()
    if runtime == runtime_root or not runtime.is_relative_to(runtime_root):
        parser.error('--runtime must select an isolated directory under the session runtime directory')
    folder = OUT/'measurements'/('simple-panels-'+runtime.name+('-floating-' if args.floating else '-docked-')+time.strftime('%Y%m%d-%H%M%S'))
    folder.mkdir()
    retained = {}
    for directory in ('settings', 'appdata', 'localappdata', 'plugins/Config'):
        for path in (runtime/directory).rglob('*'):
            if path.is_file():
                retained[path.relative_to(runtime)] = path.read_bytes()
    for name in ('config.xml', 'session.xml', 'pluginStartup.ini'):
        retained[Path(name)] = (runtime/name).read_bytes()
    manifest = json.loads((runtime/'session-fixture.json').read_text(encoding='utf-8'))
    for document in manifest['documents']:
        for item in document['copies']:
            retained[Path(item['path'])] = (runtime/item['path']).read_bytes()
    report: dict[str, Any] = dict(status='incomplete', runs=[], floating=args.floating,
                  exe_sha256=hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest(),
                  runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    observer = Observer()
    process, hwnd = None, None
    target_pids: set[int] = set()
    try:
        prepare(runtime, args.floating, folder)
        required = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))['plugins']
        for plugin in required:
            dll = runtime/'plugins'/plugin['name']/(plugin['name']+'.dll')
            assert hashlib.sha256(dll.read_bytes()).hexdigest() == plugin['sha256'], plugin['name']
        report['original_plugin_count'] = len(required)
        for index in range(2):
            env = dict(os.environ, APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
                       TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'), NPP_STARTUP_TRACE=str(folder/f'run-{index}.tsv'))
            env.pop('NPP_STARTUP_TSF', None)
            command = [str(runtime/'notepad++.exe'), '-multiInst', '-noActivate', '-settingsDir=settings']
            if index == 0:
                command.append('-simple')
            print('START panel recovery', index, args.floating, flush=True)
            process = subprocess.Popen(command, cwd=runtime, env=env)
            pid = process.pid
            target_pids.add(pid)
            hwnd = wait_for(lambda: next((w['hwnd'] for w in win.windows(pid)
                if w['class']=='Notepad++' and w['visible'] and uncloaked(w['hwnd'])), None), observer, process)
            assert not win.U.GetMenu(hwnd), 'Saved editor-only mode must survive restarting'
            def ready():
                mapped = {str(Path(p).resolve()).lower() for p in win.modules(pid)}
                panels = [w for w in snapshot(pid) if w['class']=='#32770' and w['title']=='Python Script']
                loaded = all(str((runtime/'plugins'/p['name']/(p['name']+'.dll')).resolve()).lower() in mapped for p in required)
                return panels if loaded and panels else None
            panels = wait_for(ready, observer, process)
            assert not any(w['visible'] for w in panels)
            assert not any(w['visible'] for w in snapshot(process.pid) if w['class']=='#32770')
            # Complete the host command before reading the final panel layout.
            assert win.message(hwnd, 2072, 0, 44110, timeout=3000) is not None
            def visible_panels():
                return [w for w in snapshot(pid) if w['class']=='#32770' and w['title']=='Python Script' and w['visible']]
            shown = wait_for(visible_panels, observer, process)
            panel = shown[0]
            assert win.U.GetMenu(hwnd)
            assert (panel['root'] != hwnd) == args.floating
            panel_rect = win.rect(panel['root']) if args.floating else panel['rect']
            if args.floating:
                assert panel_rect[:2] == (220, 180)
                assert panel_rect[2:] == (560, 300)
            if index:
                assert tuple(panel_rect) == tuple(report['runs'][0]['panel_rect'])
            assert inspect(process.pid)['passed']
            # A requested native render documents panel content, without timing claims.
            image = print_window(panel['root'] if args.floating else hwnd, process.pid, folder/f'panel-restored-{index}.png')
            assert win.message(hwnd, 2072, 0, 44110, timeout=3000) is not None
            wait_for(lambda: not visible_panels(), observer, process)
            assert not win.U.GetMenu(hwnd)
            assert win.U.PostMessageW(hwnd, 0x10, 0, 0)
            assert process.wait(timeout=10) == 0
            observer.pump()
            assert not [event for event in observer.events if event['pid']==process.pid]
            config = ET.parse(runtime/'settings/config.xml')
            mode = config.find('./GUIConfigs/GUIConfig[@name="SimpleMode"]')
            assert mode is not None and mode.get('enabled') == 'yes'
            saved = config.findall('./GUIConfigs/GUIConfig[@name="DockingManager"]/PluginDlg')
            assert any(p.get('pluginName')=='Python Script' and p.get('isVisible')=='yes' for p in saved)
            report['runs'].append(dict(index=index, pid=process.pid, panel=panel, panel_rect=panel_rect,
                                       native_render=image, target_exit=process.returncode))
            print('PASS panel hidden, restored, saved', index, panel_rect, flush=True)
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=repr(error))
        raise
    finally:
        if process and process.poll() is None:
            if hwnd:
                win.U.PostMessageW(hwnd, 0x10, 0, 0)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=3)
                report['forced_failure_exit'] = True
        observer.pump()
        report['foreground_events'] = [event for event in observer.events if event['pid'] in target_pids]
        if report['foreground_events'] or report.get('forced_failure_exit'):
            report['status'] = 'failed'
        observer.close()
        (folder/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        for relative, content in retained.items():
            path = runtime/relative
            if not path.exists() or path.read_bytes() != content:
                mode = path.stat().st_mode if path.exists() else None
                if mode is not None:
                    path.chmod(mode | stat.S_IWRITE)
                try:
                    path.write_bytes(content)
                finally:
                    if mode is not None:
                        path.chmod(mode)
        print('RESULT', report['status'], folder.name, flush=True)
    if report['status'] != 'passed':
        raise SystemExit(1)


if __name__ == '__main__':
    main()
