"""Compare real tab labels and host operations without video capture or typing."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any
import xml.etree.ElementTree as ET

from background_zorder import inspect
from foreground_events import Observer
from print_owned_window import capture
import win32_probe as win

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['default', 'normal'])
    parser.add_argument('sha256')
    args = parser.parse_args()
    observer = Observer()
    stamp = time.strftime('%Y%m%d-%H%M%S')
    name = 'complete-tabs-' + args.mode + '-' + args.sha256[:8] + '-' + stamp
    folder, runtime = OUT/'measurements'/name, OUT/'runtime'/name
    folder.mkdir()
    print('COPY', name, flush=True)
    source = OUT/'runtime'/('startup-idle-default44' if args.mode == 'default' else 'startup-idle-normal44')
    shutil.copytree(source, runtime)
    binary = OUT/'artifacts'/args.sha256/'notepad++.exe'
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == args.sha256
    shutil.copy2(binary, runtime/'notepad++.exe')
    names = ['one & two.txt', 'pair && triple &&&.txt', 'last.txt']
    for index, filename in enumerate(names):
        (runtime/filename).write_bytes(('DOCUMENT ' + str(index) + '\nSecond line.\n').encode('ascii'))
    plugins = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))['plugins']
    for plugin in plugins:
        dll = runtime/'plugins'/plugin['name']/(plugin['name']+'.dll')
        assert hashlib.sha256(dll.read_bytes()).hexdigest() == plugin['sha256']
    report: dict[str, Any] = dict(status='incomplete', mode=args.mode, sha256=args.sha256,
        source_revision=json.loads((binary.parent/'debug-build.json').read_text(encoding='utf-8'))['source_revision'],
        resource_limits=observer.resource_limits, actions=[], filenames=names,
        expected='Three named documents; readonly toggles; native EOL conversion and undo; new/close; final empty replacement.',
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    app = hwnd = editor = tab = None

    def pump(seconds):
        end = time.perf_counter() + seconds
        while time.perf_counter() < end:
            observer.pump()
            assert app and app.poll() is None
            assert not any(e['pid'] == app.pid for e in observer.events)
            time.sleep(.01)

    def command(identifier):
        assert win.message(hwnd, 2072, 0, identifier, timeout=2000) is not None
        pump(.05)

    def state(label, count):
        assert app and tab and editor
        assert win.message(tab, 0x1304, timeout=1000) == count
        assert win.message(hwnd, 2031, 0, 1, timeout=1000) == count
        path = folder/(label+'.xml')
        with path.open('wb') as stream:
            subprocess.run([str(OUT/'read_owned_accessibility.exe'), str(tab), str(app.pid)],
                stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=5)
        tree = ET.fromstring(path.read_text(encoding='utf-8-sig'))
        labels = [node.get('name') for node in tree.iter('control') if node.get('type') == 'ControlType.TabItem']
        assert len(labels) == count, (label, labels)
        item: dict[str, Any] = dict(label=label, labels=labels, count=count,
            readonly=win.message(editor, 2140, timeout=1000), modified=win.message(editor, 2159, timeout=1000),
            buffer=win.message(hwnd, 2084, timeout=1000))
        report['actions'].append(item)
        print('STATE', label, json.dumps(item), flush=True)
        return item

    print('START', folder.name, flush=True)
    try:
        command_line = [str(runtime/'notepad++.exe'), '-multiInst', '-noActivate', '-nosession',
            '-settingsDir=settings', *names]
        app = subprocess.Popen(command_line, cwd=runtime, env=dict(os.environ,
            APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
            TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'), NPP_STARTUP_TRACE=str(folder/'startup.tsv')))
        deadline = time.perf_counter()+7
        while time.perf_counter() < deadline:
            observer.pump()
            assert app.poll() is None
            mains = [w for w in win.windows(app.pid) if w['class']=='Notepad++' and w['visible']]
            if mains:
                hwnd = mains[0]['hwnd']
                children = win.windows(app.pid, hwnd)
                tabs = [w for w in children if w['class']=='SysTabControl32' and w['visible'] and win.message(w['hwnd'],0x1304,timeout=500)==3]
                views = [w for w in children if w['class']=='Scintilla' and w['visible']]
                mapped = {str(p.resolve()).casefold() for p in win.modules(app.pid)}
                if tabs and views and all(str((runtime/'plugins'/p['name']/(p['name']+'.dll')).resolve()).casefold() in mapped for p in plugins):
                    tab, editor = tabs[0]['hwnd'], views[0]['hwnd']
                    break
            time.sleep(.02)
        assert hwnd and tab and editor, 'Startup deadline'
        pump(1.2)
        report['all_original_plugins_mapped'] = len(plugins)
        assert inspect(app.pid)['passed']
        initial = state('initial', 3)
        assert all(initial['labels'])
        report['initial_image'] = capture(hwnd, app.pid, folder/'initial.png')
        assert win.message(hwnd, 2052, 0, 0, timeout=1000) is not None
        command(42028)
        assert state('readonly', 3)['readonly'] == 1
        report['readonly_image'] = capture(hwnd, app.pid, folder/'readonly.png')
        command(42028)
        assert state('writable', 3)['readonly'] == 0
        # Convert this owned LF fixture through the real host command. This
        # exercises dirty-label updates without keyboard or text messages.
        command(45001)
        assert state('converted', 3)['modified'] == 1
        report['modified_image'] = capture(hwnd, app.pid, folder/'modified.png')
        command(42003)
        assert state('undone', 3)['modified'] == 0
        command(41001)
        state('new', 4)
        command(41003)
        state('closed-new', 3)
        command(41004)
        assert state('replaced-empty', 1)['modified'] == 0
        assert win.message(editor, 2006, timeout=1000) == 0
        assert inspect(app.pid)['passed']
        report['status'] = 'passed'
    except Exception as error:
        report.update(status='failed', error=repr(error))
        print('FAILED', repr(error), flush=True)
    finally:
        if hwnd:
            count = win.message(hwnd, 2031, 0, 1, timeout=1000)
            if count is not None and 0 < count <= 4:
                for index in range(count):
                    win.message(hwnd, 2052, 0, index, timeout=1000)
                    if editor:
                        win.message(editor, 2014, timeout=1000)
            win.U.PostMessageW(hwnd, 0x10, 0, 0)
        if app:
            try:
                report['exit'] = app.wait(timeout=5)
            except subprocess.TimeoutExpired:
                report.update(status='failed', error='Owned fixture did not close normally')
                raise
        observer.pump()
        report['foreground_events'] = [e for e in observer.events if app and e['pid']==app.pid]
        observer.close()
        (folder/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('RESULT', report['status'], report.get('exit'), report['foreground_events'], folder.name, flush=True)
    assert report['status']=='passed' and report['exit']==0 and not report['foreground_events']

if __name__ == '__main__':
    main()
