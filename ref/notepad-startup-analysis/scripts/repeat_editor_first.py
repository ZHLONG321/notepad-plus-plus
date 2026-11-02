"""Repeat the real window/editor preservation check with retained frame evidence."""
import json
from pathlib import Path
import statistics
import subprocess
import time

OUT = Path(__file__).resolve().parent


def main() -> None:
    destination = OUT/'measurements'/('editor-first-repeat-'+time.strftime('%Y%m%d-%H%M%S'))
    destination.mkdir()
    reports = []
    for index in range(3):
        for mode in ('default', 'normal'):
            before = set((OUT/'measurements').glob('editor-first-startup-*'))
            subprocess.run(['uv', 'run', '--offline', '--no-project', 'python', '-X', 'utf8',
                str(OUT/'verify_editor_first.py'), '--runtime', 'startup-idle-'+mode+'44'], check=True)
            created = set((OUT/'measurements').glob('editor-first-startup-*'))-before
            assert len(created) == 1
            folder = created.pop()
            report = json.loads((folder/'result.json').read_text(encoding='utf-8'))
            assert report['status'] == 'passed_early_edit_preservation'
            reports.append(dict(mode=mode, index=index, folder=folder.relative_to(OUT).as_posix(),
                first_frame_ms=report['first_frame_ms'], first_message_glyph_ms=report['first_message_glyph_ms'],
                exe_sha256=report['exe_sha256']))
            (destination/'runs.json').write_text(json.dumps(reports, indent=2), encoding='utf-8')
            print('RUN', json.dumps(reports[-1]), flush=True)
    summary = {}
    for mode in ('default', 'normal'):
        summary[mode] = {}
        for metric in ('first_frame_ms', 'first_message_glyph_ms'):
            values = [r[metric] for r in reports if r['mode']==mode]
            summary[mode][metric] = dict(values=values, median=statistics.median(values),
                mean=statistics.mean(values), sample_stdev=statistics.stdev(values), maximum=max(values))
    (destination/'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print('SUMMARY', json.dumps(summary), flush=True)


if __name__ == '__main__':
    main()
