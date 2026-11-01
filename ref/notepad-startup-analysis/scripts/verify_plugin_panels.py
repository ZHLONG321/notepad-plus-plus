"""Open original plugin panels through real menu commands and retain their saved state."""
import argparse
import shutil
import json
import time
from pathlib import Path
from verify_plugin_transforms import run
from window_frames import WindowFrames
from window_snapshot import snapshot
import win32_probe as win

OUT = Path(__file__).resolve().parent


def observe(group, label, main, editor, pid, evidence):
    windows = snapshot(pid)
    visible = [w for w in windows if w['visible']]
    print('Visible panel captions', [(w['class'], w['title']) for w in visible if w['class']=='#32770'], flush=True)
    (evidence/'panel-windows.json').write_text(json.dumps(windows, ensure_ascii=False, indent=2), encoding='utf-8')
    print('Visible text', [(w['class'], w['title'], w['root']) for w in visible if w['class']=='Static'], flush=True)
    assert any((w['title']=='Python Script' if group=='Python Script' else
                w['title']=='QueryWindow' and w['class'].startswith('WindowsForms10.Window'))
               for w in visible), 'Requested plugin panel is not visibly named'
    recorder = WindowFrames(main)
    try:
        deadline = time.perf_counter()+1
        frame = None
        while frame is None and time.perf_counter()<deadline:
            frame = recorder.next()
            time.sleep(.01)
        assert frame is not None
        win.png(evidence/'opened-panel.png', frame[3], frame[2][2], frame[2][3])
    finally:
        recorder.close()
    return dict(windows=windows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('runtime')
    parser.add_argument('plugin', choices=('python', 'csv'))
    args = parser.parse_args()
    runtime = OUT/'runtime'/args.runtime
    group, label = ('Python Script', 'Show Console') if args.plugin=='python' else ('CsvQuery', 'Toggle query window')
    original = 'Owned panel fixture.\n' if args.plugin=='python' else 'id,name\n1,Alice\n2,Bob\n'
    WindowFrames.prepare()
    result = run(runtime, 'panel-'+args.plugin, [(group, label, original)], original, observe=observe)
    destination = OUT/('panel-saved-'+args.runtime+'-'+args.plugin+'-'+time.strftime('%Y%m%d-%H%M%S')+'.xml')
    shutil.copy2(runtime/'settings/config.xml', destination)
    print('Saved configuration', destination.relative_to(OUT), flush=True)
    raise SystemExit(0 if result['status']=='passed' and result.get('process_exit')==0 else 1)


if __name__=='__main__':
    main()
