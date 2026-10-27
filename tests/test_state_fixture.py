import copy
import hashlib
import json
from pathlib import Path
import pytest
from scripts.prepare_state_fixture import entries,prepare,verify


DATA=b'# pinned fixture\n'
MANIFEST=dict(schema_version=1,repository='https://github.com/joenewbry/Marlin',commit='1'*40,
    files=[dict(path='buildroot/tests/example.py',bytes=len(DATA),sha256=hashlib.sha256(DATA).hexdigest())])


def test_prepared_inputs_are_exact_and_never_execute_or_reopen(tmp_path):
    path=tmp_path/'fixture';calls=[]
    def fetch(url,size):calls.append((url,size));return DATA
    result=prepare(path,MANIFEST,fetch=fetch)
    assert result['files']==1 and not result['physical_access']
    assert calls==[('https://raw.githubusercontent.com/joenewbry/Marlin/'+'1'*40+'/buildroot/tests/example.py',len(DATA))]
    verify(path,MANIFEST)
    with pytest.raises(FileExistsError):prepare(path,MANIFEST,fetch=lambda *a:pytest.fail('existing destination fetched'))
    (path/'buildroot/tests/example.py').write_bytes(b'changed')
    with pytest.raises(ValueError):verify(path,MANIFEST)


@pytest.mark.parametrize('payload',[b'',DATA+b'!',b'x'*len(DATA),None])
def test_missing_corrupt_and_excessive_payload_cannot_publish_a_fixture(tmp_path,payload):
    path=tmp_path/'fixture'
    with pytest.raises(ValueError):prepare(path,MANIFEST,fetch=lambda *a:payload)
    assert not path.exists()
    assert list(tmp_path.iterdir())==[]


@pytest.mark.parametrize('path',['../escape.py','/absolute.py','Marlin/src/a/../b.py','Marlin/src/a//b.py',
    'Marlin/src/a.py?x=1','Marlin/src/a.py\n','buildroot/tests/a.sh','unrelated/a.py'])
def test_unsafe_manifest_paths_are_refused_before_fetch(tmp_path,path):
    manifest=copy.deepcopy(MANIFEST);manifest['files'][0]['path']=path
    with pytest.raises(ValueError):prepare(tmp_path/'fixture',manifest,fetch=lambda *a:pytest.fail('bad manifest fetched'))


@pytest.mark.parametrize('bad',['repository','commit','count','duplicate','size','digest'])
def test_manifest_identity_and_bounds_are_not_optional(bad):
    manifest=copy.deepcopy(MANIFEST)
    if bad=='repository':manifest['repository']='https://example.invalid/repo'
    if bad=='commit':manifest['commit']=None
    if bad=='count':manifest['files']=[]
    if bad=='duplicate':manifest['files']*=2
    if bad=='size':manifest['files'][0]['bytes']=True
    if bad=='digest':manifest['files'][0]['sha256']='bad'
    with pytest.raises(ValueError):entries(manifest)


def test_symlinked_inputs_and_existing_destination_are_preserved(tmp_path):
    path=tmp_path/'fixture';prepare(path,MANIFEST,fetch=lambda *a:DATA)
    original=path/'buildroot/tests/example.py';outside=tmp_path/'outside.py';outside.write_bytes(DATA)
    original.unlink();original.symlink_to(outside)
    with pytest.raises(ValueError,match='symlink'):verify(path,MANIFEST)
    assert outside.read_bytes()==DATA
    alias=tmp_path/'alias';alias.symlink_to(path,target_is_directory=True)
    with pytest.raises(FileExistsError):prepare(alias,MANIFEST)


def test_committed_source_manifest_pins_required_native_sources():
    manifest=json.loads((Path(__file__).resolve().parents[1]/'state-fixture-source.json').read_text())
    files={row['path'] for row in entries(manifest)}
    assert manifest['commit']=='0f8d1ca1055b9030cabf6ff8e3a6762fdb4dfee7'
    assert {'LICENSE','Marlin/src/feature/host_machine_state.cpp',
        'buildroot/tests/llm_machine_state_native.py'}<=files
