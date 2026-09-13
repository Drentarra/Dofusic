from __future__ import annotations

from pathlib import Path

import dofusic.config as config


def test_source_app_dir_is_data_directory():
    assert config.app_dir() == (Path(config.__file__).resolve().parents[1])


def test_frozen_app_dir_uses_meipass(monkeypatch, tmp_path):
    monkeypatch.setattr(config.sys, '_MEIPASS', str(tmp_path), raising=False)
    assert config.app_dir() == tmp_path.resolve()


def test_default_user_data_is_inside_portable_data(monkeypatch, tmp_path):
    monkeypatch.delenv('DOFUSIC_USER_DATA', raising=False)
    monkeypatch.setattr(config, 'app_dir', lambda: tmp_path / 'Data')
    assert config.user_data_dir() == tmp_path / 'Data' / 'UserData'


def test_user_data_override_is_preserved(monkeypatch, tmp_path):
    custom = tmp_path / 'custom-user-data'
    monkeypatch.setenv('DOFUSIC_USER_DATA', str(custom))
    assert config.user_data_dir() == custom.resolve()


def test_models_dir_is_inside_data(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'app_dir', lambda: tmp_path / 'Data')
    assert config.models_dir() == tmp_path / 'Data' / 'Models'

def test_v26_config_schema_removes_legacy_scheduler_tuning():
    cfg = config.AppConfig()

    assert cfg.config_version == 26
    assert not hasattr(cfg, 'signature_change_min')
    assert not hasattr(cfg, 'zone_ocr_force_interval_sec')
    assert not hasattr(cfg, 'position_ocr_force_interval_sec')

