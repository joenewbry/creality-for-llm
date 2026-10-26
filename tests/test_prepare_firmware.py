import importlib.util
from pathlib import Path
import subprocess
import pytest

spec = importlib.util.spec_from_file_location('prepare_firmware', Path(__file__).resolve().parents[1] / 'scripts/prepare_firmware.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def repository(tmp_path):
    repo = tmp_path / 'source'
    repo.mkdir()
    module.git('init', cwd=repo)
    (repo / 'source.txt').write_text('pinned firmware fixture\n')
    module.git('add', 'source.txt', cwd=repo)
    module.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
               '-c', 'commit.gpgsign=false', 'commit', '-m', 'Fixture', cwd=repo)
    commit = module.git('rev-parse', 'HEAD', cwd=repo)
    return repo, dict(schema_version=1, commit=commit, upstream_commit=commit,
                     tree=module.git('rev-parse', 'HEAD^{tree}', cwd=repo))


def test_pinned_checkout_ignores_source_working_changes(repository, tmp_path):
    repo, manifest = repository
    (repo / 'source.txt').write_text('uncommitted change')
    target = tmp_path / 'firmware'
    result = module.prepare(repo, target, manifest)
    assert result['commit'] == manifest['commit']
    assert (target / 'source.txt').read_text() == 'pinned firmware fixture\n'
    assert module.git('status', '--porcelain', cwd=target) == ''
    assert not (target / '.git/objects/info/alternates').exists()


@pytest.mark.parametrize('field', ['commit', 'tree', 'upstream_commit'])
def test_wrong_pin_never_publishes_destination(repository, tmp_path, field):
    repo, manifest = repository
    manifest[field] = '0' * 40
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        module.prepare(repo, tmp_path / 'firmware', manifest)
    assert not (tmp_path / 'firmware').exists()
    assert not list(tmp_path.glob('.firmware-input-*'))


def test_existing_destination_preserved(repository, tmp_path):
    repo, manifest = repository
    target = tmp_path / 'firmware'
    target.mkdir()
    (target / 'keep').write_text('existing work')
    with pytest.raises(FileExistsError):
        module.prepare(repo, target, manifest)
    assert (target / 'keep').read_text() == 'existing work'
