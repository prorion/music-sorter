import importlib.util
from pathlib import Path

import pytest


spec = importlib.util.spec_from_file_location('build_windows', Path(__file__).resolve().parents[1] / 'scripts/build_windows.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def make_bundle(path, contents):
    path.mkdir(parents=True)
    (path / 'main.exe').write_bytes(contents)
    (path / 'DEPENDENCIES.json').write_text('[]', 'utf-8')
    return path


def test_publish_replaces_whole_bundle_and_preserves_old_runtime(tmp_path, monkeypatch):
    previous = make_bundle(tmp_path / 'dist', b'old')
    (previous / 'obsolete.dll').write_bytes(b'obsolete')
    staged = make_bundle(tmp_path / 'build/staged/music-sorter.dist', b'new')
    (staged / 'required.dll').write_bytes(b'required')
    shortcuts = []
    monkeypatch.setattr(builder, 'ensure_not_running', lambda path: None)
    monkeypatch.setattr(builder, 'write_shortcut', lambda root, path: shortcuts.append(path))

    builder.publish_bundle(tmp_path, staged, previous)

    assert (previous / 'main.exe').read_bytes() == b'new'
    assert (previous / 'required.dll').read_bytes() == b'required'
    assert not (previous / 'obsolete.dll').exists()
    backups = list((tmp_path / 'build').glob('previous-bundle-*'))
    assert len(backups) == 1 and (backups[0] / 'main.exe').read_bytes() == b'old'
    assert (backups[0] / 'obsolete.dll').is_file()
    assert shortcuts == [previous]


def test_shortcut_failure_restores_previous_bundle_and_entry_point(tmp_path, monkeypatch):
    previous = make_bundle(tmp_path / 'dist', b'old')
    staged = make_bundle(tmp_path / 'build/staged/music-sorter.dist', b'new')
    shortcut = tmp_path / 'music-sorter.lnk'
    shortcut.write_bytes(b'old shortcut')
    monkeypatch.setattr(builder, 'ensure_not_running', lambda path: None)

    def fail(*args):
        raise OSError('shortcut write denied')

    monkeypatch.setattr(builder, 'write_shortcut', fail)
    with pytest.raises(OSError, match='shortcut write denied'):
        builder.publish_bundle(tmp_path, staged, previous)
    assert (previous / 'main.exe').read_bytes() == b'old'
    assert (staged / 'main.exe').read_bytes() == b'new'
    assert shortcut.read_bytes() == b'old shortcut'


def test_running_bundle_blocks_publication_before_any_move(tmp_path, monkeypatch):
    previous = make_bundle(tmp_path / 'dist', b'old')
    staged = make_bundle(tmp_path / 'build/staged/music-sorter.dist', b'new')

    def running(path):
        raise RuntimeError('running')

    monkeypatch.setattr(builder, 'ensure_not_running', running)
    with pytest.raises(RuntimeError, match='running'):
        builder.publish_bundle(tmp_path, staged, previous)
    assert (previous / 'main.exe').read_bytes() == b'old'
    assert (staged / 'main.exe').read_bytes() == b'new'


def test_publication_refuses_unrelated_data_or_incomplete_build(tmp_path, monkeypatch):
    unrelated = tmp_path / 'personal'
    unrelated.mkdir()
    (unrelated / 'notes.txt').write_text('keep', 'utf-8')
    staged = make_bundle(tmp_path / 'build/staged/music-sorter.dist', b'new')
    with pytest.raises(ValueError, match='기존 배포가 아닌'):
        builder.publish_bundle(tmp_path, staged, unrelated)
    (staged / 'DEPENDENCIES.json').unlink()
    with pytest.raises(ValueError, match='완성된 빌드'):
        builder.publish_bundle(tmp_path, staged, tmp_path / 'dist')
    assert (unrelated / 'notes.txt').read_text('utf-8') == 'keep'
