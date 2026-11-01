"""Exercise original plugin menu commands on own disk fixtures in the background."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import traceback
from typing import Any
from urllib.parse import unquote

from foreground_events import Observer
import native_ui_record
import win32_probe as win
from window_snapshot import snapshot
from background_zorder import inspect as inspect_background

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def wait(observer, seconds):
    end = time.perf_counter()+seconds
    while time.perf_counter() < end:
        observer.pump()
        time.sleep(.01)


def read_editor(editor):
    length = win.message(editor, 2006, timeout=1000)
    assert length is not None and 0 <= length < 16384
    values = [win.message(editor, 2007, i, timeout=1000) for i in range(length)]
    checked = []
    for value in values:
        assert value is not None
        checked.append(value & 255)
    return bytes(checked).decode('utf-8')


def command_item(items, group, label, parents=()):
    for item in items:
        path = (*parents, item['text'].split('\t')[0].replace('&', ''))
        if item.get('children'):
            found = command_item(item['children'], group, label, path)
            if found is not None:
                return found
        elif group in path[:-1] and path[-1] == label:
            return item
    return None


def command_id(items, group, label, parents=()):
    item = command_item(items, group, label, parents)
    return item['id'] if item else None


def run(runtime, case, operations, original, validator=None, observe=None, native_menu=False,
        additional_documents=(), asynchronous=False):
    evidence = OUT/'measurements'/('plugins-'+runtime.name+'-'+case+'-'+time.strftime('%Y%m%d-%H%M%S'))
    evidence.mkdir(parents=True)
    document = runtime/('plugin-check-'+case+'.txt')
    document.write_bytes(original.encode('utf-8'))
    report: dict[str, Any] = dict(status='incomplete', commands=[],
        runtime=runtime.relative_to(ROOT).as_posix(),
        exe_sha256=hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest(),
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        original=original, scenarios=[{'group':g, 'command':c, 'expected':e} for g,c,e in operations])
    audit = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))
    required = []
    for plugin in audit['plugins']:
        path = runtime/'plugins'/plugin['name']/(plugin['name']+'.dll')
        assert hashlib.sha256(path.read_bytes()).hexdigest()==plugin['sha256']
        required.append(str(path.resolve()).lower())
    observer, process, main_hwnd = Observer(), None, None
    try:
        print('START', runtime.name, case, flush=True)
        for extra in additional_documents:
            assert extra.resolve().parent == runtime.resolve() and extra.is_file()
        command = [str(runtime/'notepad++.exe'), '-multiInst', '-noActivate', '-nosession',
                   '-settingsDir='+str(runtime/'settings'),
                   *[extra.name for extra in additional_documents], document.name]
        report['command'] = ['notepad++.exe', *command[1:4], '-settingsDir=settings',
            *[extra.name for extra in additional_documents], document.name]
        process = subprocess.Popen(command, cwd=runtime, env=dict(os.environ,
            APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
            TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'), NPP_STARTUP_TRACE=str(evidence/'startup.csv')))
        deadline = time.perf_counter()+6
        editor = None
        while time.perf_counter() < deadline:
            observer.pump()
            assert process.poll() is None
            windows = [w for w in win.windows(process.pid) if w['class']=='Notepad++']
            if windows:
                main_hwnd = windows[0]['hwnd']
                views = [w for w in win.windows(parent=main_hwnd) if w['class']=='Scintilla' and w['visible']]
                loaded = {str(Path(p).resolve()).lower() for p in win.modules(process.pid)}
                if views and set(required) <= loaded:
                    editor = views[0]['hwnd']
                    break
            time.sleep(.02)
        assert main_hwnd and editor, 'Startup deadline'
        report['background_after_loading'] = inspect_background(process.pid)
        assert report['background_after_loading']['passed'], 'Fixture is ahead of another application'
        wait(observer, 1.2)
        assert read_editor(editor)==original
        menus = native_ui_record.record(main_hwnd)
        for group, label, expected in operations:
            identifier = command_id(menus['menus'], group, label)
            assert identifier is not None, (group, label)
            # Select all through the actual Scintilla scalar API; execute the
            # plugin through Notepad++'s public NPPM_MENUCOMMAND dispatch.
            assert win.message(editor, 2013, timeout=1000) is not None
            started = time.perf_counter()
            dispatch = 'NPPM_MENUCOMMAND'
            if asynchronous:
                assert not native_menu
                returned = win.U.PostMessageW(main_hwnd, 2072, 0, identifier)
                assert returned
                dispatch = 'posted NPPM_MENUCOMMAND'
            elif native_menu:
                item = command_item(menus['menus'], group, label)
                assert item is not None
                # Respect the menu's actual Windows notification contract.
                if item['menu_style'] & 0x08000000:  # MNS_NOTIFYBYPOS
                    dispatch = 'WM_MENUCOMMAND'
                    returned = win.message(main_hwnd, 0x126, item['position'], item['menu_handle'], timeout=2000)
                else:
                    dispatch = 'WM_COMMAND'
                    returned = win.message(main_hwnd, 0x111, identifier, 0, timeout=2000)
            else:
                returned = win.message(main_hwnd, 2072, 0, identifier, timeout=2000)
            elapsed = (time.perf_counter()-started)*1000
            assert returned is not None, 'Plugin command timed out'
            wait(observer, .15)
            value = read_editor(editor)
            report['commands'].append(dict(group=group, label=label, id=identifier,
                                            dispatch=dispatch,
                                            elapsed_ms=None if asynchronous else elapsed,
                                            post_ms=elapsed if asynchronous else None, output=value))
            if observe is not None:
                report['commands'][-1]['observation'] = observe(group, label, main_hwnd, editor, process.pid, evidence)
            if validator is not None:
                validator(label, value, original)
            elif expected is None:
                assert value != original and unquote(value)==original, (label, value)
            else:
                assert value==expected, (label, value, expected)
            assert not [e for e in observer.events if e['pid']==process.pid], 'Command took foreground'
            print('PASS', runtime.name, group, label,
                  'posted; result inspected' if asynchronous else f'{elapsed:.3f} ms', flush=True)
        report['status'] = 'passed'
    except Exception as exc:
        report['status'], report['error'] = 'failed', repr(exc)
        report['traceback'] = traceback.format_exc()
        print('FAILED', repr(exc), flush=True)
    finally:
        observer.pump()
        report['foreground_events'] = [e for e in observer.events if process and e['pid']==process.pid]
        observer.close()
        if main_hwnd:
            # Some commands create result documents. All buffers in this
            # -nosession fixture belong to this run, including those results.
            count = win.message(main_hwnd, 2031, 0, 1, timeout=1000)
            if count is not None:
                assert 0 < count <= len(operations)+1
                report['fixture_document_count'] = count
                for index in range(count):
                    win.message(main_hwnd, 2052, 0, index, timeout=1000)
                    for view in win.windows(parent=main_hwnd):
                        if view['class']=='Scintilla' and view['visible']:
                            win.message(view['hwnd'], 2014, timeout=1000)
            win.U.PostMessageW(main_hwnd, 0x10, 0, 0)
        if process:
            try:
                report['process_exit'] = process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                report['unclosed_windows'] = snapshot(process.pid)
                report['unclosed_main_response'] = win.message(main_hwnd, 0, timeout=50) if main_hwnd else None
                process.terminate()
                report['process_exit'] = process.wait(timeout=5)
                report['status'] = 'failed'
                report['error'] = 'Fixture process failed to close normally'
        (evidence/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('RESULT', report['status'], evidence.relative_to(ROOT), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('runtime')
    args = parser.parse_args()
    runtime = (ROOT/args.runtime).resolve()
    assert runtime.is_relative_to(OUT/'runtime')
    report = run(runtime, 'mime', [
        ('MIME Tools', 'Base64 Encode with padding', 'SGVsbG8sIE5vdGVwYWQrKyE='),
        ('MIME Tools', 'Base64 Decode', 'Hello, Notepad++!'),
        ('MIME Tools', 'Full URL Encode', None),
        ('MIME Tools', 'URL Decode', 'Hello, Notepad++!')], 'Hello, Notepad++!')
    raise SystemExit(0 if report['status']=='passed' and report.get('process_exit')==0 else 1)


if __name__=='__main__':
    main()
