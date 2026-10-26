#!/usr/bin/env python3
"""Materialize an explicit review boundary from a committed sanitized export.

No Git history or later working changes are imported. Does not publish anything.
"""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import os
import tempfile
from export_review import export


def prepare(repository, revision, boundary, destination):
    destination = Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError('destination already exists')
    with tempfile.TemporaryDirectory(prefix='.printer-boundary-', dir=destination.parent) as tmp:
        root = Path(tmp)
        exported = root / 'export'
        manifest = export(repository, revision, exported)
        policy = json.loads((exported / 'host-review-boundaries.json').read_text())
        if policy['schema_version'] != 1:
            raise ValueError('unknown boundary schema')
        files = policy['boundaries'][boundary]['files']
        allowed = {row['path'] for row in manifest['files']}
        staged = root / 'boundary'
        staged.mkdir()
        records = []
        for target, source in sorted(files.items()):
            path = PurePosixPath(target)
            if (str(path) != target or path.is_absolute() or '..' in path.parts
                    or any(part.startswith('.') and part != '.gitignore' for part in path.parts)
                    or source not in allowed):
                raise ValueError('unsafe boundary mapping')
            data = (exported / source).read_bytes()
            output = staged / target
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(data)
            output.chmod((exported / source).stat().st_mode & 0o777)
            records.append(dict(path=target, source_path=source,
                                sha256=hashlib.sha256(data).hexdigest(), bytes=len(data)))
        result = dict(schema_version=1, boundary=boundary, source_commit=manifest['source_commit'],
                      publication_ready=False, files=records)
        (staged / 'BOUNDARY-REVIEW.json').write_text(json.dumps(result, indent=2)+'\n')
        destination.mkdir()
        os.rename(staged, destination)
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repository', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--revision', default='HEAD')
    parser.add_argument('--boundary', required=True)
    parser.add_argument('--destination', required=True, type=Path)
    args = parser.parse_args()
    result = prepare(args.repository, args.revision, args.boundary, args.destination)
    print(json.dumps({k:v for k,v in result.items() if k != 'files'}))
