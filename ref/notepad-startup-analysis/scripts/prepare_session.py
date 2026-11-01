"""Copy the maintained helpers into a project-local, ignored session folder."""
import argparse
from pathlib import Path
import shutil


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session_id', help='Single folder name under the project tmp directory')
    args = parser.parse_args()
    name = args.session_id
    if not name or name in {'.', '..'} or any(char not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_' for char in name):
        parser.error('Use letters, digits, hyphens and underscores for the session folder name.')
    source = Path(__file__).resolve().parent
    root = source.parents[2]
    destination = root/'tmp'/name
    if not destination.resolve().is_relative_to((root/'tmp').resolve()):
        raise SystemExit('Session folder must remain inside the project tmp directory.')
    files = sorted(path for path in source.iterdir()
                   if path.suffix in {'.py', '.cpp', '.cs'} and path.name != Path(__file__).name)
    conflicts = [path.name for path in files if (destination/path.name).exists()
                 and (destination/path.name).read_bytes() != path.read_bytes()]
    if conflicts:
        raise SystemExit('Existing helpers differ; choose a fresh session folder: ' + ', '.join(conflicts))
    destination.mkdir(parents=True, exist_ok=True)
    (destination/'measurements').mkdir(exist_ok=True)
    for path in files:
        target = destination/path.name
        if not target.exists():
            shutil.copy2(path, target)
            print('Copied', target.relative_to(root).as_posix(), flush=True)
    print('Helpers ready in', destination.relative_to(root).as_posix(), flush=True)


if __name__ == '__main__':
    main()
