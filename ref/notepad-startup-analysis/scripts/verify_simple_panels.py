"""Restore a saved original plugin panel while switching editor-only presentation."""
import argparse
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
from print_owned_window import capture as print_window
from verify_saved_panels import prepare
from verify_simple_mode import wait_for  # pyright: ignore[reportMissingImports]
from window_snapshot import snapshot
from window_surface import uncloaked
import win32_probe as win

OUT = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', choices=('startup-idle-default44', 'startup-idle-normal44'), required=True)
    parser.add_argument('--floating', action='store_true')
    args = parser.parse_args()
    runtime = (OUT/'runtime'/args.runtime).resolve()
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
                  exe_sha256=hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest())
    observer = Observer()
    process, hwnd = None, None
    try:
        prepare(runtime, args.floating, folder)
        required = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))['plugins']
        for index in range(2):
            env = dict(os.environ, APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
                       TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'), NPP_STARTUP_TRACE=str(folder/f'run-{index}.tsv'))
            command = [str(runtime/'notepad++.exe'), '-multiInst', '-noActivate', '-settingsDir=settings']
            if index == 0:
                command.append('-simple')
            print('START panel recovery', index, args.floating, flush=True)
            process = subprocess.Popen(command, cwd=runtime, env=env)
            pid = process.pid
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
            assert win.U.PostMessageW(hwnd, 0x111, 44110, 0)
            def visible_panels():
                return [w for w in snapshot(pid) if w['class']=='#32770' and w['title']=='Python Script' and w['visible']]
            shown = wait_for(visible_panels, observer, process)
            panel = shown[0]
            assert (panel['root'] != hwnd) == args.floating
            panel_rect = win.rect(panel['root']) if args.floating else panel['rect']
            if args.floating:
                assert panel_rect[:2] == (220, 180)
                assert panel_rect[2:] == (560, 300)
            assert inspect(process.pid)['passed']
            # A requested native render documents panel content, without timing claims.
            image = print_window(panel['root'] if args.floating else hwnd, process.pid, folder/f'panel-restored-{index}.png')
            assert win.U.PostMessageW(hwnd, 0x111, 44110, 0)
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
        observer.close()
        (folder/'result.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        for relative, content in retained.items():
            path = runtime/relative
            if not path.exists() or path.read_bytes() != content:
                path.write_bytes(content)
        print('RESULT', report['status'], folder.name, flush=True)


if __name__ == '__main__':
    main()
