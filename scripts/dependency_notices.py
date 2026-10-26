#!/usr/bin/env python3
"""Inventory installed metadata for exactly pinned dependencies, not legal advice.

This records upstream declarations; it does not infer compatibility or grant
rights. Dependencies remain separately installed rather than bundled here.
"""
import argparse
import hashlib
from importlib.metadata import distribution
import json
from pathlib import Path
import re


def inventory(lock):
    lock = Path(lock)
    records = []
    for line in lock.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)', line)
        if not match:
            raise ValueError('dependency lock must contain exact simple pins')
        name, version = match.groups()
        item = distribution(name)
        if item.version != version:
            raise ValueError('installed version differs from lock: ' + name)
        metadata = item.metadata
        license_text = metadata.get('License') or ''
        declared_files = []
        for file in item.files or []:
            # Keep original filenames and hashes; never include local paths.
            if '.dist-info' in str(file) and ('/licenses/' in str(file) or
                    file.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE', 'AUTHORS'))):
                data = item.locate_file(file).read_bytes()
                declared_files.append({'path': str(file), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes':len(data)})
        records.append({'name': metadata['Name'], 'version': version,
            'license_expression': metadata.get('License-Expression'),
            'license_declaration': license_text if len(license_text) <= 300 else None,
            'long_license_declaration_sha256': hashlib.sha256(license_text.encode()).hexdigest() if len(license_text) > 300 else None,
            'license_classifiers': [x for x in metadata.get_all('Classifier', []) if x.startswith('License ::')],
            'declared_license_files': metadata.get_all('License-File', []),
            'installed_notice_files': sorted(declared_files, key=lambda x:x['path']),
            'project_urls': metadata.get_all('Project-URL', []),
            'homepage': metadata.get('Home-page'),
            'metadata_sha256': hashlib.sha256(item.read_text('METADATA').encode()).hexdigest()})
    return {'schema_version':1, 'scope':'installed dependency declarations; not a redistribution compliance certification',
            'lock_file':lock.name, 'lock_sha256':hashlib.sha256(lock.read_bytes()).hexdigest(),
            'packages': sorted(records, key=lambda x:x['name'].lower())}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lock', type=Path, default=Path('requirements-print-mcp.lock'))
    parser.add_argument('--output', type=Path, required=True)
    args=parser.parse_args()
    result=inventory(args.lock)
    with args.output.open('x') as output:
        output.write(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'packages':len(result['packages']), 'lock_sha256':result['lock_sha256']}))


if __name__ == '__main__':
    main()
