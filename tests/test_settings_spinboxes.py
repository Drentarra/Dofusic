from __future__ import annotations

import inspect
from types import SimpleNamespace

from dofusic.ui import settings_window
from dofusic.ui.settings_window import SettingsWindow


class _Widget:
    def pack(self, *args, **kwargs):
        return self


def test_numeric_field_configures_working_spinbox_arrows(monkeypatch):
    captured = {}

    monkeypatch.setattr(settings_window.tk, 'Frame', lambda *a, **k: _Widget())
    monkeypatch.setattr(settings_window.tk, 'Label', lambda *a, **k: _Widget())

    def make_spinbox(*args, **kwargs):
        captured.update(kwargs)
        return _Widget()

    monkeypatch.setattr(settings_window.tk, 'Spinbox', make_spinbox)
    palette = SimpleNamespace(card='#111', muted='#888', card_field='#222', text='#fff', card_alt='#333')

    SettingsWindow._field(
        object(),
        'Fondu',
        object(),
        palette,
        minimum=0,
        maximum=5000,
        increment=50,
    )

    assert captured['from_'] == 0
    assert captured['to'] == 5000
    assert captured['increment'] == 50


def test_all_settings_numeric_fields_declare_their_real_ranges():
    source = inspect.getsource(SettingsWindow._build)
    assert "'Fondu audio en ms (0–5000)', self.fade_var, p, minimum=0, maximum=5000, increment=50" in source
    assert 'self.results_var' not in source
    assert 'self.delay_var' not in source
    assert 'self.cache_var' not in source
