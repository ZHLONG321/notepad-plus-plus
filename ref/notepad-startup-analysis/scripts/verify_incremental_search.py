"""Exercise the real incremental-search bar and retain native state and images.

Checks first open, selection search, next/previous, case-sensitive counts,
missing text, hide/reopen with retained controls, unchanged document and exit.
"""
import argparse
import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any

from background_zorder import inspect
from foreground_events import Observer
from print_owned_window import capture
import win32_probe as win

OUT = Path(__file__).resolve().parent
win.bind(win.U, 'GetDlgCtrlID', C.c_int, W.HWND)
win.bind(win.U, 'GetParent', W.HWND, W.HWND)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['default', 'normal'])
    parser.add_argument('sha256')
    args = parser.parse_args()
    observer = Observer()
    name = 'incremental-' + args.mode + '-' + args.sha256[:8] + '-' + time.strftime('%Y%m%d-%H%M%S')
    folder, runtime = OUT/'measurements'/name, OUT/'runtime'/name
    folder.mkdir()
    print('COPY', name, flush=True)
    shutil.copytree(OUT/'runtime'/('startup-idle-'+args.mode+'44'), runtime)
    binary = OUT/'artifacts'/args.sha256/'notepad++.exe'
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == args.sha256
    shutil.copy2(binary, runtime/'notepad++.exe')
    contents = b'alpha ALPHA alpha\r\nbeta alpha\r\n'
    (runtime/'search-fixture.txt').write_bytes(contents)
    plugins = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))['plugins']
    for plugin in plugins:
        path = runtime/'plugins'/plugin['name']/(plugin['name']+'.dll')
        assert hashlib.sha256(path.read_bytes()).hexdigest() == plugin['sha256']
    report: dict[str, Any] = dict(status='incomplete', mode=args.mode, sha256=args.sha256,
        source_revision=json.loads((binary.parent/'debug-build.json').read_text(encoding='utf-8'))['source_revision'],
        resource_limits=observer.resource_limits, actions=[], fixture=contents.decode('ascii'),
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    app = hwnd = editor = bar = None

    def pump(seconds=.05):
        end = time.perf_counter()+seconds
        while time.perf_counter()<end:
            observer.pump()
            assert app and app.poll() is None
            assert not any(e['pid']==app.pid for e in observer.events)
            time.sleep(.01)

    def control(identifier):
        assert app and hwnd
        matches = [w['hwnd'] for w in win.windows(app.pid, hwnd)
                   if win.U.GetDlgCtrlID(w['hwnd'])==identifier]
        assert len(matches)==1, (identifier,matches)
        return matches[0]

    def text(hwnd):
        buffer = C.create_unicode_buffer(1024)
        assert win.message(hwnd, 13, len(buffer), C.addressof(buffer), timeout=1000) is not None
        return buffer.value

    def state(label):
        item = dict(label=label, selection=[win.message(editor,2143),win.message(editor,2145)],
                    query=text(control(1682)), status=text(control(1689)))
        report['actions'].append(item)
        print('STATE',json.dumps(item),flush=True)
        return item

    def bar_command(identifier):
        target = control(identifier) if identifier!=2 else 0
        assert win.message(bar,0x111,identifier,target,timeout=2000) is not None
        pump()

    def query(value):
        buffer = C.create_unicode_buffer(value)
        assert win.message(control(1682),12,0,C.addressof(buffer),timeout=2000) is not None
        pump()

    try:
        print('START',name,flush=True)
        app = subprocess.Popen([str(runtime/'notepad++.exe'),'-multiInst','-noActivate','-nosession',
            '-settingsDir=settings','search-fixture.txt'],cwd=runtime,env=dict(os.environ,
            APPDATA=str(runtime/'appdata'),LOCALAPPDATA=str(runtime/'localappdata'),
            TEMP=str(runtime/'temp'),TMP=str(runtime/'temp'),NPP_STARTUP_TRACE=str(folder/'startup.tsv')))
        deadline=time.perf_counter()+7
        while time.perf_counter()<deadline:
            pump(.02)
            mains=[w for w in win.windows(app.pid) if w['class']=='Notepad++' and w['visible']]
            if not mains:
                continue
            hwnd=mains[0]['hwnd']
            views=[w for w in win.windows(app.pid,hwnd) if w['class']=='Scintilla' and w['visible']]
            mapped={str(p.resolve()).casefold() for p in win.modules(app.pid)}
            if views and all(str((runtime/'plugins'/p['name']/(p['name']+'.dll')).resolve()).casefold() in mapped for p in plugins):
                editor=views[0]['hwnd']
                break
        assert editor and hwnd
        pump(1.2)
        report['all_original_plugins_mapped']=len(plugins)
        assert inspect(app.pid)['passed']
        report['bar_exists_at_startup']=any(win.U.GetDlgCtrlID(w['hwnd'])==1682 for w in win.windows(app.pid,hwnd))
        win.message(editor,2160,0,0,timeout=1000)
        started=time.perf_counter()
        assert win.message(hwnd,2072,0,43011,timeout=3000) is not None
        report['first_open_command_ms']=(time.perf_counter()-started)*1000
        pump()
        bar=win.U.GetParent(control(1682))
        assert win.U.IsWindowVisible(bar)
        state('first-open')
        report['first_image']=capture(hwnd,app.pid,folder/'first.png')
        bar_command(2)
        assert not win.U.IsWindowVisible(bar)
        win.message(editor,2160,0,5,timeout=1000)
        assert win.message(hwnd,2072,0,43011,timeout=3000) is not None
        pump()
        assert state('selection-query')['query']=='alpha'
        win.message(control(1685),0xF1,0,timeout=1000)
        bar_command(1685)
        win.message(editor,2160,0,0,timeout=1000)
        query('alpha')
        assert state('insensitive')['selection']==[0,5]
        bar_command(1684)
        assert state('next')['selection']==[6,11]
        bar_command(1683)
        assert state('previous')['selection']==[0,5]
        win.message(control(1685),0xF1,1,timeout=1000)
        bar_command(1685)
        bar_command(1684)
        assert state('case-sensitive-next')['selection']==[12,17]
        query('absent-phrase')
        assert state('missing')['status']=='Phrase not found'
        query('alpha')
        report['search_image']=capture(hwnd,app.pid,folder/'search.png')
        saved=control(1682)
        bar_command(2)
        win.message(editor,2160,0,0,timeout=1000)
        assert win.message(hwnd,2072,0,43011,timeout=3000) is not None
        pump()
        assert control(1682)==saved and state('reopened')['query']=='alpha'
        assert win.message(editor,2159)==0 and win.message(editor,2006)==len(contents)
        assert inspect(app.pid)['passed']
        report['status']='passed'
    except Exception as error:
        report.update(status='failed',error=repr(error))
        print('FAILED',repr(error),flush=True)
    finally:
        if hwnd:
            win.U.PostMessageW(hwnd,0x10,0,0)
        if app:
            report['exit']=app.wait(timeout=7)
        observer.pump()
        report['foreground_events']=[e for e in observer.events if app and e['pid']==app.pid]
        observer.close()
        (folder/'result.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print('RESULT',report['status'],report.get('exit'),report['foreground_events'],name,flush=True)
    assert report['status']=='passed' and report['exit']==0 and not report['foreground_events']


if __name__=='__main__':
    main()
