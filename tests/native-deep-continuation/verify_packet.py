"""Verify exact source hashes and reversible patches without touching installed files."""
import hashlib
from pathlib import Path
import shutil
import subprocess
import tempfile


PACKET = Path(__file__).resolve().parents[2] / 'runtime/repairs/20261119-native-deep-continuation'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    rows = [line.split('\t') for line in (PACKET/'manifest.tsv').read_text().splitlines()]
    with tempfile.TemporaryDirectory() as directory:
        shadow = Path(directory)
        for target, before, after, candidate in rows:
            installed, source = Path(target), PACKET/candidate
            assert digest(installed) == before, f'Installed source changed: {target}'
            assert digest(source) == after, f'Candidate hash mismatch: {candidate}'
            relative = Path(candidate).relative_to('candidate')
            copied = shadow/relative
            copied.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(installed, copied)
            patch = PACKET/'patches'/f'{relative.name}.patch'
            command = ['patch', '--fuzz=0', '-p1', '-i', str(patch)]
            subprocess.run(command, cwd=shadow, check=True)
            assert digest(copied) == after, f'Forward mismatch: {relative}'
            subprocess.run(command + ['-R'], cwd=shadow, check=True)
            assert digest(copied) == before, f'Rollback mismatch: {relative}'
            print(f'ROUNDTRIP-OK {relative}; installed unchanged')


if __name__ == '__main__':
    main()
