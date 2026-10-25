"""Non-regressions added after the 2026-10-20 whole-project audit."""
from __future__ import annotations

import json
import math
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from dofusic.audio.player import MusicPlayer
from dofusic.config import AppConfig, load_config, save_config
from dofusic.location.repository import DofusRepository
from dofusic.ui.settings_window import SettingsWindow
from dofusic.version import __version__, portable_zip_name


def test_numeric_config_rejects_nan_and_infinity_without_disabling_timers(tmp_path):
    defaults = AppConfig()
    config = AppConfig(
        zone_ocr_idle_refresh_sec=math.inf,
        worker_restart_backoff_sec=math.inf,
        position_context_max_age_sec=math.nan,
        pending_confirmation_interval_sec=-math.inf,
        min_confidence=math.nan,
        required_confirmations=10_000,
        process_name='../invalid\\name',
    ).sanitized()
    assert config.zone_ocr_idle_refresh_sec == defaults.zone_ocr_idle_refresh_sec
    assert config.worker_restart_backoff_sec == defaults.worker_restart_backoff_sec
    assert config.position_context_max_age_sec == defaults.position_context_max_age_sec
    assert config.pending_confirmation_interval_sec == defaults.pending_confirmation_interval_sec
    assert config.min_confidence == defaults.min_confidence
    assert config.required_confirmations == 10
    assert config.process_name == defaults.process_name

    path = tmp_path / 'user.json'
    path.write_text(json.dumps({
        'config_version': 27,
        'zone_ocr_idle_refresh_sec': 'Infinity',
        'worker_restart_backoff_sec': 'NaN',
        'position_context_max_age_sec': '1e300',
        'position_ocr_idle_refresh_sec': '99999',
    }), encoding='utf-8')
    loaded = load_config(path)
    assert loaded.zone_ocr_idle_refresh_sec == defaults.zone_ocr_idle_refresh_sec
    assert loaded.worker_restart_backoff_sec == defaults.worker_restart_backoff_sec
    assert loaded.position_context_max_age_sec == 120.0
    assert loaded.position_ocr_idle_refresh_sec == 60.0
    assert all(math.isfinite(value) for value in (
        loaded.zone_ocr_idle_refresh_sec, loaded.worker_restart_backoff_sec,
        loaded.position_context_max_age_sec, loaded.position_ocr_idle_refresh_sec,
    ))


def test_failed_config_replace_keeps_old_config_and_cleans_staging(tmp_path, monkeypatch):
    target = tmp_path / 'config.json'
    target.write_text('already here', encoding='utf-8')

    def fail_replace(_self, _target):
        raise OSError('read only')

    monkeypatch.setattr(Path, 'replace', fail_replace)
    with pytest.raises(OSError, match='read only'):
        save_config(AppConfig(), target)
    assert target.read_text(encoding='utf-8') == 'already here'
    assert not (tmp_path / 'config.json.tmp').exists()


def test_loudness_analysis_is_single_worker_latest_wins_even_with_50_changes():
    player = MusicPlayer(spectrum_enabled=False, normalize_loudness=True)
    begin = threading.Event()
    release = threading.Event()
    observed = []
    lock = threading.Lock()
    active = 0
    peak = 0

    def fake_loudness(path, generation):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            observed.append((path, generation))
            begin.set()
        release.wait(timeout=3.0)
        with lock:
            active -= 1

    player._loudness_worker = fake_loudness
    player._start_loudness_analysis(Path('first.opus'))
    assert begin.wait(timeout=1.0)
    for index in range(50):
        player._start_loudness_analysis(Path(f'track-{index}.opus'))
    assert player._loudness_jobs.qsize() <= 1
    assert peak == 1
    release.set()
    deadline = time.monotonic() + 2.0
    while len(observed) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert observed[-1][0] == Path('track-49.opus')
    assert peak == 1
    worker = player._loudness_thread
    player.close()
    assert worker is not None and not worker.is_alive()


def test_close_flushes_pending_loudness_work():
    player = MusicPlayer(spectrum_enabled=False, normalize_loudness=True)
    player._queue_latest_loudness((Path('obsolete.mp3'), 99))
    player.close()
    assert player._loudness_stop.is_set()
    assert player._loudness_thread is None


def test_settings_refuse_to_apply_when_disk_persistence_fails(tmp_path, monkeypatch):
    from dofusic.ui import settings_window as ui

    original = AppConfig()
    widget = SettingsWindow.__new__(SettingsWindow)
    widget.config = original
    widget.config_file = tmp_path / 'settings.json'
    widget.window = SimpleNamespace(destroy=lambda: pytest.fail('must stay open'))
    widget.notebook = SimpleNamespace(select=lambda *args: 'first')
    widget.on_apply = lambda: pytest.fail('must not apply unpersisted settings')
    for field, value in (
        ('theme_var', 'bonta'), ('visualizer_var', False),
        ('normalize_var', False), ('fade_var', 200),
        ('topmost_var', True),
    ):
        setattr(widget, field, SimpleNamespace(get=lambda v=value: v))
    messages = []
    monkeypatch.setattr(ui.messagebox, 'showerror', lambda *a, **kw: messages.append(a))
    monkeypatch.setattr(ui, 'save_config', lambda *_a, **_kw: (_ for _ in ()).throw(OSError('denied')))
    assert widget._apply() is False
    assert widget.config.theme == original.theme == 'emerald'
    assert widget.config.fade_ms == 650
    widget.close()
    assert len(messages) == 2
    assert 'Sauvegarde impossible' in messages[0]


def test_release_artifact_name_and_tag_are_derived_from_canonical_version():
    from tools.check_release_tag import validate_tag

    assert portable_zip_name() == f'Dofusic_{__version__}_LOCAL_WINDOWS_X64_AVEC_MUSIQUES.zip'
    assert portable_zip_name('3.2.1').startswith('Dofusic_3.2.1_')
    for invalid in ('../../hidden', 'v2.3.4', '', 'Infinity'):
        with pytest.raises(ValueError):
            portable_zip_name(invalid)
    validate_tag('')
    validate_tag(f'v{__version__}')
    with pytest.raises(ValueError, match='does not match'):
        validate_tag('v9.9.9')


def test_integrity_check_catches_dangling_aliases_and_coordinate_area(tmp_path):
    data = Path(__file__).resolve().parents[1] / 'Data' / 'dofus_data.sqlite'
    copy = tmp_path / 'db.sqlite'
    shutil.copy2(data, copy)
    conn = sqlite3.connect(copy)
    try:
        conn.execute("INSERT INTO place_aliases(alias,target_kind,target_id,priority) VALUES ('BadArea','area',-131,1)")
        conn.execute("INSERT INTO place_aliases(alias,target_kind,target_id,priority) VALUES ('BadSub','subarea',-132,1)")
        conn.execute('UPDATE map_coordinates SET area_id=-135 WHERE rowid=(SELECT MIN(rowid) FROM map_coordinates)')
        conn.commit()
    finally:
        conn.close()
    problems = DofusRepository(copy).integrity_problems()
    assert any('BadArea' in item for item in problems)
    assert any('BadSub' in item for item in problems)
    assert any('missing Area -135' in item for item in problems)
