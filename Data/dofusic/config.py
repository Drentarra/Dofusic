from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import asdict, dataclass, fields
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    config_version: int = 26
    process_name: str = 'dofus.exe'

    capture_fps: int = 10
    ui_tick_ms: int = 80
    zone_ocr_min_interval_sec: float = 0.60
    position_ocr_min_interval_sec: float = 0.40
    zone_ocr_idle_refresh_sec: float = 2.0
    position_ocr_idle_refresh_sec: float = 1.0
    ocr_change_threshold: float = 4.0
    position_context_max_age_sec: float = 4.0
    context_position_tolerance: int = 2
    pending_confirmation_interval_sec: float = 0.35
    worker_restart_backoff_sec: float = 2.0
    capture_missing_grace_sec: float = 1.2
    fallback_unknown_delay_sec: float = 2.5

    cpu_threads: int = 2
    min_confidence: float = 0.55
    min_fuzzy_score: float = 86.0
    min_margin: float = 8.0
    exact_single_confidence: float = 0.92
    required_confirmations: int = 2
    text_stability_min: float = 84.0

    volume: int = 70
    mute: bool = False
    fade_ms: int = 650
    fallback_stem: str = 'Musique'
    debug: bool = False
    music_dir: str = ''

    # Stable user-facing UI/online music preferences.
    theme: str = 'emerald'
    online_music_enabled: bool = True
    online_suggestions: bool = True
    online_suggest_delay_ms: int = 320
    online_search_results: int = 8
    online_cache_mb: int = 128
    show_visualizer: bool = True
    normalize_loudness: bool = True
    always_on_top: bool = False

    def sanitized(self) -> 'AppConfig':
        values = asdict(self)
        values.update({
            'capture_fps': max(5, min(30, int(self.capture_fps))),
            'ui_tick_ms': max(50, min(250, int(self.ui_tick_ms))),
            'zone_ocr_min_interval_sec': max(0.25, float(self.zone_ocr_min_interval_sec)),
            'position_ocr_min_interval_sec': max(0.20, float(self.position_ocr_min_interval_sec)),
            'zone_ocr_idle_refresh_sec': max(0.75, float(self.zone_ocr_idle_refresh_sec)),
            'position_ocr_idle_refresh_sec': max(0.50, float(self.position_ocr_idle_refresh_sec)),
            'ocr_change_threshold': max(0.0, min(64.0, float(self.ocr_change_threshold))),
            'position_context_max_age_sec': max(0.0, float(self.position_context_max_age_sec)),
            'context_position_tolerance': max(0, min(4, int(self.context_position_tolerance))),
            'pending_confirmation_interval_sec': max(0.15, float(self.pending_confirmation_interval_sec)),
            'worker_restart_backoff_sec': max(0.5, float(self.worker_restart_backoff_sec)),
            'capture_missing_grace_sec': max(0.0, float(self.capture_missing_grace_sec)),
            'fallback_unknown_delay_sec': max(0.0, float(self.fallback_unknown_delay_sec)),
            'cpu_threads': max(1, min(4, int(self.cpu_threads))),
            'min_confidence': max(0.0, min(1.0, float(self.min_confidence))),
            'min_fuzzy_score': max(0.0, min(100.0, float(self.min_fuzzy_score))),
            'min_margin': max(0.0, min(100.0, float(self.min_margin))),
            'exact_single_confidence': max(0.0, min(1.0, float(self.exact_single_confidence))),
            'required_confirmations': max(2, int(self.required_confirmations)),
            'text_stability_min': max(0.0, min(100.0, float(self.text_stability_min))),
            'volume': max(0, min(100, int(self.volume))),
            'fade_ms': max(0, min(5000, int(self.fade_ms))),
            'theme': self.theme.strip().lower() if self.theme.strip().lower() in {'emerald', 'bonta', 'brakmar', 'arcane', 'ivory', 'graphite'} else 'emerald',
            'online_suggest_delay_ms': max(150, min(1200, int(self.online_suggest_delay_ms))),
            'online_search_results': max(3, min(20, int(self.online_search_results))),
            'online_cache_mb': max(64, min(4096, int(self.online_cache_mb))),
        })
        return AppConfig(**values)


def app_dir() -> Path:
    """Directory containing immutable application data.

    In a PyInstaller onedir build this is the bundle contents directory
    (``Data``). In source mode it is the checked-out ``Data`` directory.
    """
    frozen_root = getattr(sys, '_MEIPASS', None)
    if frozen_root:
        return Path(frozen_root).resolve()
    return Path(__file__).resolve().parents[1]


def user_data_dir() -> Path:
    override = os.environ.get('DOFUSIC_USER_DATA', '').strip()
    if override:
        return Path(override).expanduser().resolve()
    return app_dir() / 'UserData'


def cleanup_legacy_user_data(base: Path | str | None = None) -> None:
    """Remove app-owned browser state left by V24 and older builds.

    Only the former ``UserData/webview2`` directory is removed; user settings,
    logs, cache and unrelated files are preserved.
    """
    root = Path(base) if base is not None else user_data_dir()
    legacy = root / 'webview2'
    if legacy.is_dir():
        shutil.rmtree(legacy, ignore_errors=True)


def config_path() -> Path:
    return user_data_dir() / 'config.json'


def log_dir() -> Path:
    return user_data_dir() / 'logs'


def database_path() -> Path:
    return app_dir() / 'dofus_data.sqlite'


def models_dir() -> Path:
    return app_dir() / 'Models'


def default_music_dir() -> Path:
    return app_dir().parent / 'Musiques'


def _coerce_value(default_value, value):
    if isinstance(default_value, bool):
        if isinstance(value, str):
            return value.strip().lower() in {'1', 'true', 'yes', 'on'}
        return bool(value)
    if isinstance(default_value, int) and not isinstance(default_value, bool):
        return int(value)
    if isinstance(default_value, float):
        return float(value)
    return str(value) if isinstance(default_value, str) else value


def _apply_dict(config: AppConfig, data: dict) -> AppConfig:
    defaults = AppConfig()
    allowed = {field.name for field in fields(AppConfig)}
    values = asdict(config)
    for key, value in data.items():
        if key not in allowed:
            continue
        try:
            values[key] = _coerce_value(getattr(defaults, key), value)
        except (TypeError, ValueError):
            continue
    return AppConfig(**values).sanitized()


# Only stable user choices survive a schema migration. Runtime/OCR tuning is
# version-owned so old alpha settings cannot silently degrade a newer release.
_MIGRATION_KEEP = frozenset({
    'process_name', 'volume', 'mute', 'fallback_stem', 'debug', 'music_dir',
    'theme', 'online_music_enabled', 'online_suggestions', 'online_suggest_delay_ms',
    'online_search_results', 'online_cache_mb',
    'always_on_top',
})


def _migrate_current_config_data(data: dict) -> dict:
    try:
        version = int(data.get('config_version', 0))
    except (TypeError, ValueError):
        version = 0
    current_version = AppConfig().config_version
    if version >= current_version:
        return data
    return {key: value for key, value in data.items() if key in _MIGRATION_KEEP}


def load_config(path: Path | str | None = None) -> AppConfig:
    target = Path(path) if path is not None else config_path()
    config = AppConfig()
    if not target.exists():
        return config.sanitized()

    try:
        raw = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return config.sanitized()
    if not isinstance(raw, dict):
        return config.sanitized()

    config = _apply_dict(config, _migrate_current_config_data(raw))
    config.config_version = AppConfig().config_version
    return config.sanitized()


def save_config(config: AppConfig, path: Path | str | None = None) -> Path:
    target = Path(path) if path is not None else config_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix(target.suffix + '.tmp')
    temp.write_text(
        json.dumps(asdict(config.sanitized()), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    temp.replace(target)
    return target
