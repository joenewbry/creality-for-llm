#!/usr/bin/env python3
"""Export an explicit committed file list for review, without Git history.

This is preparation only, not a secret detector or publication authorization.
Reject flagged contents rather than silently redacting executable source.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile
import os

BLOCKED_PARTS = {'.git', '.env', 'evidence', 'upstream', 'firmware', '__pycache__'}
PATTERNS = {
    'local_user_path': re.compile(r'/(?:Users|home|Volumes)/[^\s]+'),
    'private_ipv4': re.compile(r'\b(?:192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+)\b'),
    'physical_serial_path': re.compile(r'/dev/(?:cu\.|tty\.|ttyUSB|ttyACM)[\w.-]+'),
    'private_key': re.compile(r'-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----'),
    'credential_token': re.compile(r'\b(?:gh[pousr]_[A-Za-z0-9]{20,}|sk-[A-Za-z0-9_-]{20,})\b'),
}


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args])


def export(root, revision, destination):
    root, destination = Path(root).resolve(), Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination already exists')
    commit = git(root, 'rev-parse', '--verify', revision + '^{commit}').decode().strip()
    policy = json.loads(git(root, 'show', f'{commit}:publication-files.json'))
    if set(policy) != {'schema_version', 'files'} or policy['schema_version'] != 1:
        raise ValueError('invalid export policy')
    files = policy['files']
    if not isinstance(files, list) or not files or len(files) != len(set(files)):
        raise ValueError('invalid export file list')
    tree = {}
    for record in git(root, 'ls-tree', '-rz', commit).split(b'\0'):
        if not record:
            continue
        meta, name = record.split(b'\t', 1)
        tree[name.decode()] = meta.decode().split()
    payloads, findings = {}, []
    for name in files:
        if type(name) is not str:
            raise ValueError('invalid export path')
        path = PurePosixPath(name)
        if (str(path) != name or path.is_absolute() or '..' in path.parts or
                set(path.parts) & BLOCKED_PARTS or any(p.startswith('.venv') for p in path.parts)):
            raise ValueError('unsafe export path')
        meta = tree.get(name)
        if not meta or meta[0] not in ('100644', '100755') or meta[1] != 'blob':
            raise ValueError('export requires a committed regular file: ' + name)
        data = git(root, 'cat-file', 'blob', meta[2])
        text = data.decode('utf-8')  # source-only; reject binary artifacts
        for category, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append({'path': name, 'category': category})
        payloads[name] = (data, meta[0] == '100755')
    if findings:
        # Never print matched secrets or private values in reports.
        raise ValueError('review flags: ' + json.dumps(findings, sort_keys=True))
    manifest = {'schema_version': 1, 'source_commit': commit, 'git_history_included': False,
        'publication_ready': False, 'required_review': [
            'license_and_attribution', 'manual_privacy_review', 'clean_export_tests',
            'firmware_publication_and_stack_boundaries'],
        'files': [{'path': name, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                  for name, (data, _) in sorted(payloads.items())]}
    # Stage on the same filesystem, reserve the destination exclusively, and
    # rename. No existing directory, symlink or working checkout is replaced.
    with tempfile.TemporaryDirectory(prefix='.printer-review-', dir=destination.parent) as temporary:
        staged = Path(temporary) / 'source'
        staged.mkdir()
        for name, (data, executable) in payloads.items():
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if executable else 0o644)
        (staged / 'EXPORT-REVIEW.json').write_text(json.dumps(manifest, indent=2) + '\n')
        destination.mkdir()  # fails if another actor claimed this path
        os.rename(staged, destination)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--revision', default='HEAD')
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    result = export(args.repository, args.revision, args.destination)
    print(json.dumps({'source_commit': result['source_commit'], 'file_count': len(result['files']),
                      'publication_ready': False}))


if __name__ == '__main__':
    main()
