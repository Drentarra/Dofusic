"""Contrats de lecture de la bibliothèque et de sauvegarde du portable."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from dofusic import config
from dofusic.audio.library import MusicLibrary
from dofusic.audio.session import MusicSession
from dofusic.models import LocationKind, LocationRecord


class Repository:
    location = LocationRecord(1, 'Astrub', LocationKind.AREA)

    def all_locations(self):
        return (self.location,)

    def parent_area(self, _location):
        return None


class Controller:
    def __init__(self, library):
        self.music_library = library
        self.calls = []

    def play_manual_local_track(self, path, title, loop=False):
        self.calls.append((path, title, loop))
        return True


def test_nested_music_tracks_available_for_automatic_and_manual_playback(tmp_path):
    root = tmp_path / 'Musiques'
    (root / 'Zones' / 'Incarnam').mkdir(parents=True)
    (root / 'Combats').mkdir()
    named = root / 'Zones' / 'Incarnam' / 'Astrub.opus'
    named.write_bytes(b'OggS')
    battle = root / 'Combats' / 'Musique Combat Astrub.opus'
    battle.write_bytes(b'OggS')
    (root / 'Zones' / 'notes.txt').write_text('ignore', encoding='utf-8')
    library = MusicLibrary(Repository(), root)
    session = MusicSession(Controller(library), config.AppConfig())

    assert library.resolve(Repository.location, raw_text='Astrub') == named
    assert library.resolve(Repository.location, raw_text='Astrub', combat=True) == battle
    assert named in session.local_tracks()
    assert battle in session.local_tracks()
    assert session.play_local_track(named)
    assert session.controller.calls == [(named, 'Astrub', False)]
    assert len(library.all_tracks) == 2


def test_music_scan_is_read_only_and_survives_missing_directory(tmp_path, monkeypatch):
    folder = tmp_path / 'unavailable'
    def reject_directory_creation(*_args, **_kwargs):
        raise PermissionError('no permission to create music directory')
    monkeypatch.setattr(Path, 'mkdir', reject_directory_creation)
    library = MusicLibrary(Repository(), folder)
    assert library.all_tracks == ()
    assert library.resolve(None) is None
    assert not folder.exists()


def test_symlink_cannot_escape_music_folder(tmp_path):
    root = tmp_path / 'Musiques'
    root.mkdir()
    secret = tmp_path / 'outside.opus'
    secret.write_bytes(b'not part of music library')
    try:
        (root / 'linked.opus').symlink_to(secret)
        (root / 'linked-dir').symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip('Symbolic links unavailable')
    assert MusicLibrary(Repository(), root).all_tracks == ()


def test_fast_exact_lookup_never_calls_fuzzy_ranking(tmp_path, monkeypatch):
    for number in range(160):
        (tmp_path / f'Localité {number:03}.opus').touch()
    known = tmp_path / 'Astrub.opus'
    known.touch()
    library = MusicLibrary(Repository(), tmp_path)

    def no_fuzzy(_anchors):
        raise AssertionError('Exact matching should avoid all fuzzy computations')

    monkeypatch.setattr(library, '_rank_track_candidates', no_fuzzy)
    assert library.resolve(Repository.location, raw_text='Astrub') == known


def test_config_falls_back_to_local_app_data_when_bundle_read_only(monkeypatch, tmp_path):
    portable = tmp_path / 'ReadOnlyApp' / 'Data'
    user_data = tmp_path / 'LocalAppData'
    monkeypatch.delenv('DOFUSIC_USER_DATA', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(user_data))
    monkeypatch.setattr(config, 'app_dir', lambda: portable)
    monkeypatch.setattr(config, '_portable_storage_writable', lambda _folder: False)

    expected = user_data / 'Dofusic'
    assert config.user_data_dir() == expected
    assert config.config_path() == expected / 'config.json'
    cfg = config.AppConfig(volume=34, theme='brakmar')
    assert config.save_config(cfg) == expected / 'config.json'
    reloaded = config.load_config()
    assert reloaded.volume == 34
    assert reloaded.theme == 'brakmar'
    assert not portable.exists()


def test_portable_storage_permission_probe_uses_actual_write(monkeypatch, tmp_path):
    directory = tmp_path / 'Data' / 'UserData'
    def denied(*_args, **_kwargs):
        raise PermissionError('read-only')
    monkeypatch.setattr(config.tempfile, 'TemporaryFile', denied)
    assert not config._portable_storage_writable(directory)


def test_atomic_config_save_does_not_leave_temp_files_or_damage_settings(tmp_path, monkeypatch):
    target = tmp_path / 'config.json'
    target.write_text(json.dumps({'volume': 32}), encoding='utf-8')
    original = target.read_bytes()
    def fail_replace(_self, _dst):
        raise PermissionError('filesystem denied replacement')
    monkeypatch.setattr(Path, 'replace', fail_replace)
    with pytest.raises(PermissionError):
        config.save_config(config.AppConfig(volume=90), target)
    assert target.read_bytes() == original
    assert list(tmp_path.glob('*.tmp')) == []


def test_read_only_portable_migrates_existing_preferences_once(monkeypatch, tmp_path):
    portable = tmp_path / 'OldPortable' / 'Data'
    previous = portable / 'UserData' / 'config.json'
    previous.parent.mkdir(parents=True)
    previous.write_text(json.dumps({
        'config_version': 27, 'volume': 27, 'theme': 'ivory',
    }), encoding='utf-8')
    monkeypatch.delenv('DOFUSIC_USER_DATA', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'LocalAppData'))
    monkeypatch.setattr(config, 'app_dir', lambda: portable)
    monkeypatch.setattr(config, '_portable_storage_writable', lambda _folder: False)

    target = config.config_path()
    assert target == tmp_path / 'LocalAppData' / 'Dofusic' / 'config.json'
    assert config.load_config(target).volume == 27
    assert config.load_config(target).theme == 'ivory'
    config.save_config(config.AppConfig(volume=55), target)
    assert config.config_path() == target
    assert config.load_config(target).volume == 55  # Pas de migration répétée.


def test_inaccessible_music_folder_reports_error_without_crashing_ui(monkeypatch, tmp_path):
    from dofusic.ui import settings_window
    shown = []
    monkeypatch.setattr(settings_window.messagebox, 'showerror', lambda title, detail: shown.append((title, detail)))
    def denied(*_args, **_kwargs):
        raise PermissionError('access denied')
    monkeypatch.setattr(Path, 'mkdir', denied)
    settings_window._open_path(tmp_path / 'unavailable')
    assert shown and shown[0][0] == 'Dossier inaccessible'
