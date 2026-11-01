"""Restore an owned Python Script console through saved startup commands in both modes."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import traceback
import argparse
from typing import Any
import xml.etree.ElementTree as ET
from foreground_events import Observer
from window_frames import WindowFrames
from window_snapshot import snapshot
from print_owned_window import capture as print_window
from native_xml import write_xml
import win32_probe as win

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def prepare(runtime, floating, evidence, reserved=False, docked_container=3):
    path = runtime/'settings/config.xml'
    shutil.copy2(path, evidence/'config-before.xml')
    # Rebuild this synthetic fixture from the retained effective settings.
    # Reusing earlier UTF-8 helper output repeatedly re-encoded non-ASCII paths.
    tree = ET.parse(OUT/'configuration-source/effective/config.xml')
    for entry in tree.iter('GUIConfig'):
        if entry.get('name') == 'stylerTheme':
            entry.set('path', '')
        elif entry.get('name') == 'Backup':
            entry.set('dir', 'backups')
        elif entry.get('name') == 'AppPosition':
            entry.attrib.update(x='80', y='80', width='1100', height='700', isMaximized='no')
    docking = tree.find('.//GUIConfig[@name="DockingManager"]')
    assert docking is not None
    for entry in docking.findall('PluginDlg'):
        entry.set('isVisible', 'no')
    container = '4' if floating else str(docked_container)
    for entry in list(docking):
        if entry.tag == 'FloatingWindow' and entry.get('cont') == '4':
            docking.remove(entry)
        if entry.tag == 'PluginDlg' and entry.get('pluginName') in ('Python Script', 'PythonScript.dll', 'ComparePlugin.dll'):
            docking.remove(entry)
    ET.SubElement(docking, 'FloatingWindow', cont='4', x='220', y='180', width='780', height='480')
    # The original DLL actually registers its panel as Python Script:-1;
    # this separate entry supplies the observed docking identity.
    ET.SubElement(docking, 'PluginDlg', pluginName='Python Script', id='-1', curr=container,
                  prev='3' if floating else '-1', isVisible='yes')
    # FuncItem index 1 is the observed native Show Console menu command.
    ET.SubElement(docking, 'PluginDlg', pluginName='PythonScript.dll', id='1', curr=container,
                  prev='3' if floating else '-1', isVisible='yes')
    for entry in docking.findall('ActiveTabs'):
        if entry.get('cont') == container:
            entry.set('activeTab', '0')
    if reserved:
        # Restore a real docked workspace first. Its previous floating slot
        # is then reused by the original Python console after READY.
        workspace = next((e for e in docking.findall('PluginDlg')
                          if e.get('pluginName')=='Notepad++::InternalFunction' and e.get('id')=='44085'), None)
        if workspace is None:
            workspace = ET.SubElement(docking, 'PluginDlg', pluginName='Notepad++::InternalFunction', id='44085')
        workspace.attrib.update(curr='1', prev='4', isVisible='yes')
    write_xml(tree, path)
    shutil.copy2(path, evidence/'config-input.xml')
    (runtime/'panel-fixture.txt').write_bytes(b'Owned saved-panel fixture.\n')
    session = ET.Element('NotepadPlus')
    body = ET.SubElement(session, 'Session', activeView='0')
    primary = ET.SubElement(body, 'mainView', activeIndex='0')
    ET.SubElement(primary, 'File', filename='panel-fixture.txt', lang='Normal Text', encoding='-1',
                  firstVisibleLine='0', xOffset='0', startPos='0', endPos='0')
    ET.SubElement(body, 'subView', activeIndex='0')
    write_xml(ET.ElementTree(session), runtime/'settings/session.xml')


def run(name, floating, reserved=False, native_still=False, docked_container=3):
    runtime = OUT/'runtime'/name
    evidence = OUT/'measurements'/('saved-panels-'+name+('-floating-' if floating else '-docked-')+time.strftime('%Y%m%d-%H%M%S'))
    evidence.mkdir()
    prepare(runtime, floating, evidence, reserved, docked_container)
    audit = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))
    required = set()
    for plugin in audit['plugins']:
        dll = runtime/'plugins'/plugin['name']/(plugin['name']+'.dll')
        assert hashlib.sha256(dll.read_bytes()).hexdigest() == plugin['sha256']
        required.add(str(dll.resolve()).lower())
    report: dict[str, Any] = dict(status='incomplete', floating=floating, reserved_container=reserved, runtime=name,
        docked_container=docked_container, native_still=native_still,
        exe_sha256=hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest(),
        runner_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        command=['notepad++.exe', '-multiInst', '-noActivate', '-settingsDir=settings'])
    observer, process, main = Observer(), None, None
    print('START', evidence.relative_to(ROOT), flush=True)
    try:
        process = subprocess.Popen([str(runtime/'notepad++.exe'), '-multiInst', '-noActivate',
            '-settingsDir='+str(runtime/'settings')], cwd=runtime,
            env=dict(os.environ, APPDATA=str(runtime/'appdata'), LOCALAPPDATA=str(runtime/'localappdata'),
                     TEMP=str(runtime/'temp'), TMP=str(runtime/'temp'), NPP_STARTUP_TRACE=str(evidence/'startup.tsv')))
        deadline = time.perf_counter()+8
        ready = None
        panels = []
        while time.perf_counter() < deadline:
            observer.pump()
            assert process.poll() is None
            windows = snapshot(process.pid)
            mains = [w for w in windows if w['class'] == 'Notepad++' and w['visible']]
            if mains:
                main = mains[0]['hwnd']
            panels = [w for w in windows if w['visible'] and w['class'] == '#32770' and w['title']=='Python Script']
            if panels and required <= {str(p.resolve()).lower() for p in win.modules(process.pid)}:
                ready = ready or time.perf_counter()
                if time.perf_counter()-ready > 1.2:
                    break
            time.sleep(.02)
        report['windows'] = snapshot(process.pid)
        assert main and panels, 'Saved Show Console command did not restore a visible panel'
        report['panels'] = panels
        if reserved:
            trace = (evidence/'startup.tsv').read_text(encoding='utf-8')
            assert 'dock.floating.reserved' in trace and 'dock.floating.materialized' in trace
            assert trace.index('dock.floating.reserved') < trace.index('dock.floating.materialized')
        selected = panels[0]
        assert (selected['root'] != main) == floating
        root = selected['root'] if floating else main
        report['root_rect'] = win.rect(root)
        assert selected['rect'][2] > 100 and selected['rect'][3] > 50
        if floating:
            assert report['root_rect'][:2] == (220, 180)
        report['native_restore_status'] = 'passed'
        recorder = None
        try:
            if native_still:
                report['requested_render'] = print_window(root, process.pid, evidence/'restored-panel-requested-render.png')
                report['capture_method'] = 'Native requested rendering; not compositor timing'
            else:
                recorder = WindowFrames(root)
                deadline = time.perf_counter()+1
                frame = None
                while frame is None and time.perf_counter() < deadline:
                    observer.pump()
                    frame = recorder.next()
                    time.sleep(.01)
                assert frame is not None
                win.png(evidence/'restored-panel.png', frame[3], frame[2][2], frame[2][3])
                report['capture_method'] = 'Native HWND compositor frame'
        except RuntimeError as error:
            if not floating:
                raise
            report['compositor_capture_error'] = repr(error)
            report['requested_render'] = print_window(root, process.pid, evidence/'restored-panel-requested-render.png')
            report['capture_method'] = 'Native requested rendering; compositor timing unavailable'
        finally:
            if recorder:
                recorder.close()
        assert not [e for e in observer.events if e['pid'] == process.pid]
        report.update(status='passed', plugin_count=14)
        print('PASS visible Python console', 'floating' if floating else 'docked', report['root_rect'], flush=True)
    except Exception as error:
        report.update(status='failed', error=repr(error), traceback=traceback.format_exc())
        print('FAILED', report['traceback'], flush=True)
    finally:
        observer.pump()
        report['foreground_events'] = [e for e in observer.events if process and e['pid'] == process.pid]
        observer.close()
        if main:
            win.U.PostMessageW(main, 0x10, 0, 0)
        if process:
            try:
                closing = time.perf_counter()
                print('WAIT normal fixture shutdown', flush=True)
                report['process_exit'] = process.wait(timeout=20)
                report['shutdown_ms'] = (time.perf_counter()-closing)*1000
            except subprocess.TimeoutExpired:
                report['unclosed_windows'] = snapshot(process.pid)
                process.terminate()
                report.update(process_exit=process.wait(timeout=5), status='failed', error='Own fixture did not close')
        shutil.copy2(runtime/'settings/config.xml', evidence/'config-after.xml')
        before = ET.parse(evidence/'config-input.xml')
        after = ET.parse(evidence/'config-after.xml')
        unicode_before = {(e.tag, k, v) for e in before.iter() for k, v in e.attrib.items()
                          if any(ord(c) > 127 for c in v)}
        unicode_after = {(e.tag, k, v) for e in after.iter() for k, v in e.attrib.items()
                         if any(ord(c) > 127 for c in v)}
        report['config_bytes_before'] = (evidence/'config-input.xml').stat().st_size
        report['config_bytes_after'] = (evidence/'config-after.xml').stat().st_size
        report['unicode_attribute_count_before'] = len(unicode_before)
        report['unicode_attributes_preserved'] = unicode_before <= unicode_after
        if not report['unicode_attributes_preserved']:
            report.update(status='failed', error='Native save changed non-ASCII fixture attributes')
        (evidence/'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('RESULT', report['status'], report.get('process_exit'), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', choices=('panels-normal', 'panels-default'))
    parser.add_argument('--docked-only', action='store_true')
    parser.add_argument('--floating-only', action='store_true')
    parser.add_argument('--reserved-container', action='store_true')
    parser.add_argument('--native-still', action='store_true')
    parser.add_argument('--dock-container', type=int, choices=range(4), default=3)
    args = parser.parse_args()
    win.U.SetProcessDPIAware()
    if not args.native_still:
        WindowFrames.prepare()
    assert not (args.docked_only and args.floating_only)
    states = (False,) if args.docked_only else ((True,) if args.floating_only else (False, True))
    assert not args.reserved_container or args.floating_only
    results = [run(name, floating, args.reserved_container, args.native_still, args.dock_container) for floating in states
               for name in ((args.runtime,) if args.runtime else ('panels-normal', 'panels-default'))]
    (OUT/'saved-panels-summary.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    raise SystemExit(0 if all(r['status'] == 'passed' and r['process_exit'] == 0 for r in results) else 1)


if __name__ == '__main__':
    main()
