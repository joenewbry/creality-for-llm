import json
from pathlib import Path
import subprocess

import pytest

from scripts.export_review import export


def command(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.DEVNULL)


def repository(tmp_path, files=None):
    root = tmp_path / 'repository'
    root.mkdir()
    command(root, 'init', '-q')
    command(root, 'config', 'user.name', 'Fixture')
    command(root, 'config', 'user.email', 'fixture@example.invalid')
    (root/'code.py').write_text('value = 1\n')
    (root/'private.log').write_text('excluded fixture log\n')
    (root/'publication-files.json').write_text(json.dumps({'schema_version':1, 'files':files or ['code.py']}))
    command(root, 'add', '.')
    command(root, 'commit', '-qm', 'fixture')
    return root


def test_export_uses_committed_allowlist_not_working_files_or_history(tmp_path):
    root = repository(tmp_path)
    (root/'code.py').write_text('uncommitted change\n')
    destination = tmp_path/'export'
    result = export(root, 'HEAD', destination)
    assert (destination/'code.py').read_text() == 'value = 1\n'
    assert sorted(p.name for p in destination.iterdir()) == ['EXPORT-REVIEW.json', 'code.py']
    assert result['publication_ready'] is False
    assert result['git_history_included'] is False
    assert result['source_commit'] == command(root, 'rev-parse', 'HEAD').decode().strip()
    assert result['files'][0]['bytes'] == 10


def test_export_refuses_existing_destination_without_changing_it(tmp_path):
    root = repository(tmp_path)
    destination = tmp_path/'export'
    destination.mkdir()
    (destination/'keep').write_text('retained')
    with pytest.raises(ValueError, match='already exists'):
        export(root, 'HEAD', destination)
    assert (destination/'keep').read_text() == 'retained'


def test_flagged_contents_abort_without_printing_value_or_partial_export(tmp_path):
    root = repository(tmp_path)
    sensitive = '/' + 'Users' + '/fixture/private'
    (root/'code.py').write_text(sensitive)
    command(root, 'add', 'code.py')
    command(root, 'commit', '-qm', 'flag fixture')
    destination = tmp_path/'export'
    with pytest.raises(ValueError, match='local_user_path') as caught:
        export(root, 'HEAD', destination)
    assert sensitive not in str(caught.value)
    assert not destination.exists()


@pytest.mark.parametrize('path', ['../outside', 'evidence/log.txt', 'firmware/file.py', '/absolute'])
def test_forbidden_paths_are_not_exported(tmp_path, path):
    root = repository(tmp_path, [path])
    with pytest.raises(ValueError, match='unsafe export path'):
        export(root, 'HEAD', tmp_path/'export')


def test_committed_symlink_cannot_import_unlisted_contents(tmp_path):
    root = repository(tmp_path)
    (root/'code.py').unlink()
    (root/'code.py').symlink_to('private.log')
    command(root, 'add', 'code.py')
    command(root, 'commit', '-qm', 'symlink fixture')
    with pytest.raises(ValueError, match='regular file'):
        export(root, 'HEAD', tmp_path/'export')


def test_source_archive_manifest_tracks_publication_allowlist():
    root = Path(__file__).resolve().parents[1]
    policy = json.loads((root / 'publication-files.json').read_text())
    directives = [line for line in (root / 'MANIFEST.in').read_text().splitlines()
                  if line and not line.startswith('#')]
    assert directives[0] == 'global-exclude *'
    assert directives[1:] == ['include ' + name for name in sorted(policy['files'])]
