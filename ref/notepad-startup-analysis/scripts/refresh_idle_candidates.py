"""Refresh the two owned input-idle candidates, retaining their previous provenance."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]


def main():
    repo = ROOT/'src/notepad-plus-plus'
    assert not subprocess.check_output(['git', '-C', str(repo), 'status', '--porcelain'], text=True).strip()
    revision = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    binary = repo/'PowerEditor/bin64/notepad++.exe'
    for name in sys.argv[1:] or ['startup-idle-default44', 'startup-idle-normal44']:
        runtime = OUT/'runtime'/name
        previous = json.loads((runtime/'tab-preparation.json').read_text(encoding='utf-8'))
        assert hashlib.sha256((runtime/'notepad++.exe').read_bytes()).hexdigest() == previous['exe_sha256']
        retained = OUT/'artifacts'/previous['exe_sha256']
        retained.mkdir(exist_ok=True)
        saved_binary = retained/'notepad++.exe'
        if saved_binary.exists():
            assert hashlib.sha256(saved_binary.read_bytes()).hexdigest() == previous['exe_sha256']
        else:
            shutil.copy2(runtime/'notepad++.exe', saved_binary)
            shutil.copy2(runtime/'tab-preparation.json', retained/'runtime-provenance.json')
        archive = runtime/('preparation-'+previous['source_commit'][:12]+'.json')
        if not archive.exists():
            shutil.copy2(runtime/'tab-preparation.json', archive)
        shutil.copy2(binary, runtime/'notepad++.exe')
        previous.update(source_commit=revision, exe_sha256=hashlib.sha256(binary.read_bytes()).hexdigest())
        (runtime/'tab-preparation.json').write_text(json.dumps(previous, indent=2), encoding='utf-8')
        print('REFRESHED', name, json.dumps(previous), flush=True)


if __name__ == '__main__':
    main()
