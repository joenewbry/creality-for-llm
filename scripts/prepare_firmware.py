#!/usr/bin/env python3
"""Materialize exact native-test firmware inputs; never build, upload or open USB."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def git(*args, cwd=None):
    return subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', *args], cwd=cwd,
                          check=True, text=True, capture_output=True).stdout.strip()


def prepare(repository, destination, manifest):
    for field in ('commit', 'tree', 'upstream_commit'):
        if not re.fullmatch('[0-9a-f]{40}', manifest.get(field, '')):
            raise ValueError('Invalid pinned ' + field)
    if manifest.get('schema_version') != 1:
        raise ValueError('Unsupported manifest version')
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('Destination already exists; leave it unchanged')
    if not destination.parent.is_dir():
        raise ValueError('Destination parent must exist')
    with tempfile.TemporaryDirectory(prefix='.firmware-input-', dir=destination.parent) as tmp:
        checkout = Path(tmp) / 'checkout'
        git('clone', '--no-checkout', '--no-hardlinks', '--', str(repository), str(checkout))
        git('cat-file', '-e', manifest['commit'] + '^{commit}', cwd=checkout)
        git('merge-base', '--is-ancestor', manifest['upstream_commit'], manifest['commit'], cwd=checkout)
        tree = git('rev-parse', manifest['commit'] + '^{tree}', cwd=checkout)
        if tree != manifest['tree']:
            raise ValueError('Pinned firmware tree mismatch')
        git('checkout', '--detach', manifest['commit'], cwd=checkout)
        if git('status', '--porcelain', cwd=checkout):
            raise ValueError('Prepared firmware is not clean')
        # Reserve the destination exclusively. On failure only remove the empty
        # directory created here; never delete or overwrite an existing checkout.
        destination.mkdir()
        try:
            os.rename(checkout, destination)
        except BaseException:
            destination.rmdir()
            raise
    return {'commit': manifest['commit'], 'tree': tree, 'physical_access': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', required=True,
                        help='Reviewed firmware Git repository URL or local repository')
    parser.add_argument('--destination', type=Path, default=ROOT / 'firmware')
    args = parser.parse_args()
    manifest = json.loads((ROOT / 'firmware-test-source.json').read_text())
    print(json.dumps(prepare(args.repository, args.destination, manifest), sort_keys=True))


if __name__ == '__main__':
    main()
