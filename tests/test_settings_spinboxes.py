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
    assert "'Résultats affichés (3–20)', self.results_var, p, minimum=3, maximum=20, increment=1" in source
    assert "'Délai suggestions en ms (150–1200)', self.delay_var, p, minimum=150, maximum=1200, increment=50" in source
    assert "'Cache online en Mo (64–4096)', self.cache_var, p, minimum=64, maximum=4096, increment=64" in source
