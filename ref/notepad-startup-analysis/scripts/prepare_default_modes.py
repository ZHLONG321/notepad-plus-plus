"""Configure fresh project runtimes for the default and explicit normal policies."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def main():
    binary = ROOT/'src/notepad-plus-plus/PowerEditor/bin64/notepad++.exe'
    revision = subprocess.check_output(['git', '-C', str(ROOT),
                                        'rev-parse', 'HEAD'], text=True).strip()
    plugins = json.loads((OUT/'audit.json').read_text(encoding='utf-8'))['plugins']
    names = sys.argv[1:] or ['startup-default44', 'startup-explicit-normal44']
    assert len(names) == 2
    for name, mode in zip(names, ['delayed', 'normal'], strict=True):
        runtime = OUT/'runtime'/name
        assert runtime.resolve().is_relative_to(OUT/'runtime')
        assert runtime.is_dir() and not (runtime/'tab-preparation.json').exists()
        shutil.copy2(binary, runtime/'notepad++.exe')
        text = '; Per DLL: normal = immediate, delayed = after first editor paint and input idle.\n'
        text += '; Missing entries default to delayed.\n[Plugins]\n'
        if mode == 'normal':
            text += ''.join(p['name']+'.dll=normal\n' for p in plugins)
        (runtime/'pluginStartup.ini').write_text(text, encoding='utf-8')
        report = dict(source_commit=revision, exe_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                      configured_mode=mode, plugin_count=len(plugins), immediate_crash_exceptions=[])
        (runtime/'tab-preparation.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print('PREPARED', name, json.dumps(report), flush=True)


if __name__ == '__main__':
    main()
