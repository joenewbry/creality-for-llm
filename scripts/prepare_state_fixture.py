#!/usr/bin/env python3
"""Fetch hash-pinned native fixture sources only. No build, USB or firmware upload."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tempfile
from urllib.request import urlopen

ROOT=Path(__file__).resolve().parents[1]


def entries(manifest):
    if (type(manifest) is not dict or manifest.get('schema_version')!=1
            or manifest.get('repository')!='https://github.com/joenewbry/Marlin'
            or type(manifest.get('commit')) is not str
            or not re.fullmatch('[0-9a-f]{40}',manifest['commit'])):
        raise ValueError('invalid native fixture manifest')
    files=manifest.get('files')
    if type(files) is not list or not 1<=len(files)<=32:
        raise ValueError('bounded fixture file list required')
    names=set();total=0
    for row in files:
        if type(row) is not dict or set(row)!={'path','bytes','sha256'}:
            raise ValueError('invalid fixture file entry')
        name=row['path']
        if type(name) is not str or not re.fullmatch('[A-Za-z0-9_./-]{1,160}',name):
            raise ValueError('invalid fixture path')
        path=PurePosixPath(name)
        if (str(path)!=name or path.is_absolute() or '..' in path.parts or name in names
                or not (name=='LICENSE' or name.startswith(('Marlin/src/','buildroot/tests/')))
                or (name!='LICENSE' and path.suffix not in ('.py','.cpp','.h'))):
            raise ValueError('invalid fixture path')
        if (type(row['bytes']) is not int or not 0<row['bytes']<=262144
                or type(row['sha256']) is not str or not re.fullmatch('[0-9a-f]{64}',row['sha256'])):
            raise ValueError('invalid fixture digest or size')
        names.add(name);total+=row['bytes']
    if total>1048576:
        raise ValueError('fixture input exceeds bound')
    return files


def checked(data,row):
    if (type(data) is not bytes or len(data)!=row['bytes']
            or hashlib.sha256(data).hexdigest()!=row['sha256']):
        raise ValueError('fixture input size or hash mismatch')
    return data


def verify(directory,manifest):
    directory=Path(directory)
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('real fixture directory required')
    for row in entries(manifest):
        path=directory/row['path']
        parts=PurePosixPath(row['path']).parts
        if any(directory.joinpath(*parts[:i]).is_symlink() for i in range(1,len(parts)+1)):
            raise ValueError('fixture symlinks are unsupported')
        if not path.is_file() or path.stat().st_size!=row['bytes']:
            raise ValueError('fixture input missing or changed')
        with path.open('rb') as stream:checked(stream.read(row['bytes']+1),row)
    return directory


def _fetch(url,size):
    with urlopen(url,timeout=5) as response:
        return response.read(size+1)


def prepare(destination,manifest,*,fetch=_fetch):
    files=entries(manifest)
    destination=Path(destination).absolute()
    if destination.exists() or destination.is_symlink():
        raise FileExistsError('fixture destination exists; preserve it')
    if not destination.parent.is_dir():
        raise ValueError('fixture parent must exist')
    with tempfile.TemporaryDirectory(prefix='.state-fixture-',dir=destination.parent) as temporary:
        staged=Path(temporary)/'source';staged.mkdir()
        for row in files:
            url='https://raw.githubusercontent.com/joenewbry/Marlin/'+manifest['commit']+'/'+row['path']
            data=checked(fetch(url,row['bytes']),row)
            path=staged/row['path'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
        verify(staged,manifest)
        destination.mkdir()  # Reserve exclusively before the rename.
        try:os.rename(staged,destination)
        except BaseException:
            destination.rmdir();raise
    return dict(commit=manifest['commit'],files=len(files),physical_access=False)


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--destination',type=Path,default=ROOT/'state-firmware')
    args=cli.parse_args()
    manifest=json.loads((ROOT/'state-fixture-source.json').read_text())
    print(json.dumps(prepare(args.destination,manifest)))
