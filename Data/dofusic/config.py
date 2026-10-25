from __future__ import annotations

import json
import math
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path


def _bounded_float(value: object, minimum: float, maximum: float, default: float) -> float:
    """Clamp finite configuration values; reject NaN/Infinity and invalid inputs."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(number):
        return default
    return max(minimum, min(maximum, number))


def _bounded_int(value: object, minimum: int, maximum: int, default: int) -> int:
    """Prevent malformed numeric input and unbounded thresholds."""
    try:
        number = float(value)
        if not math.isfinite(number):
            return default
        return max(minimum, min(maximum, int(number)))
    except (TypeError, ValueError, OverflowError):
        return default


@dataclass(slots=True)
class AppConfig:
    config_version: int = 27
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
    music_dir: str = ''

    # Local music and appearance preferences.
    theme: str = 'emerald'
    show_visualizer: bool = True
    normalize_loudness: bool = True
    always_on_top: bool = False

    def sanitized(self) -> 'AppConfig':
        defaults = AppConfig()
        values = asdict(self)
        # All timeouts, OCR thresholds and numerical preferences are finite.
        # Upper bounds also prevent a corrupted config from disabling timers.
        float_bounds = {
            'zone_ocr_min_interval_sec': (0.25, 30.0),
            'position_ocr_min_interval_sec': (0.20, 30.0),
            'zone_ocr_idle_refresh_sec': (0.75, 60.0),
            'position_ocr_idle_refresh_sec': (0.50, 60.0),
            'ocr_change_threshold': (0.0, 64.0),
            'position_context_max_age_sec': (0.0, 120.0),
            'pending_confirmation_interval_sec': (0.15, 30.0),
            'worker_restart_backoff_sec': (0.5, 120.0),
            'capture_missing_grace_sec': (0.0, 30.0),
            'fallback_unknown_delay_sec': (0.0, 60.0),
            'min_confidence': (0.0, 1.0),
            'min_fuzzy_score': (0.0, 100.0),
            'min_margin': (0.0, 100.0),
            'exact_single_confidence': (0.0, 1.0),
            'text_stability_min': (0.0, 100.0),
        }
        int_bounds = {
            'capture_fps': (5, 30), 'ui_tick_ms': (50, 250),
            'context_position_tolerance': (0, 4), 'cpu_threads': (1, 4),
            'required_confirmations': (2, 10), 'volume': (0, 100),
            'fade_ms': (0, 5000),
        }
        for field, (minimum, maximum) in float_bounds.items():
            values[field] = _bounded_float(values[field], minimum, maximum, getattr(defaults, field))
        for field, (minimum, maximum) in int_bounds.items():
            values[field] = _bounded_int(values[field], minimum, maximum, getattr(defaults, field))
        for field in ('mute', 'show_visualizer', 'normalize_loudness', 'always_on_top'):
            values[field] = _coerce_value(getattr(defaults, field), values[field])
        values['config_version'] = defaults.config_version
        themes = {'emerald', 'bonta', 'brakmar', 'arcane', 'ivory', 'graphite'}
        theme = str(self.theme).strip().lower()
        values['theme'] = theme if theme in themes else defaults.theme
        process_name = str(self.process_name).strip()
        if (not process_name or len(process_name) > 128 or
                any(character in process_name for character in '/\\\x00\r\n')):
            process_name = defaults.process_name
        values['process_name'] = process_name
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


def _portable_storage_writable(folder: Path) -> bool:
    """Teste les droits réels, sans laisser de fichier dans le dossier.

    os.access() ne suffit pas sur Windows (ACL et dossiers protégés).
    """
    try:
        folder.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile(dir=folder):
            pass
        return True
    except OSError:
        return False


def user_data_dir() -> Path:
    """Conserve les paramètres portables, avec repli Windows si nécessaire.

    Les données utilisateur n'appartiennent jamais aux sources ou au ZIP de
    distribution. DOFUSIC_USER_DATA reste prioritaire, sans repli implicite.
    """
    override = os.environ.get('DOFUSIC_USER_DATA', '').strip()
    if override:
        return Path(override).expanduser().resolve()
    portable = app_dir() / 'UserData'
    if _portable_storage_writable(portable):
        return portable
    root = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
    if root:
        fallback = Path(root).expanduser() / 'Dofusic'
    else:
        fallback = Path.home() / ('AppData/Local' if os.name == 'nt' else '.local/share') / 'Dofusic'

    # Une installation déplacée vers un emplacement protégé ne doit pas
    # perdre les préférences déjà enregistrées dans le dossier portable.
    previous = portable / 'config.json'
    destination = fallback / 'config.json'
    try:
        if previous.is_file() and not destination.exists():
            save_config(load_config(previous), destination)
    except OSError:
        # Le lancement reste possible avec les valeurs par défaut ; l'UI
        # indiquera une erreur explicite si l'enregistrement échoue ensuite.
        pass
    return fallback


def config_path() -> Path:
    return user_data_dir() / 'config.json'


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
        except (TypeError, ValueError, OverflowError):
            continue
    return AppConfig(**values).sanitized()


# Preserve relevant user preferences across older configuration schemas;
# unknown fields are ignored by _apply_dict (including removed options).
_MIGRATION_KEEP = frozenset({
    'process_name', 'volume', 'mute', 'fallback_stem', 'music_dir',
    'theme', 'always_on_top', 'show_visualizer', 'normalize_loudness', 'fade_ms',
})


def _migrate_current_config_data(data: dict) -> dict:
    try:
        version = int(data.get('config_version', 0))
    except (TypeError, ValueError, OverflowError):
        version = 0
    if version >= AppConfig().config_version:
        return data
    # Audio/visualizer defaults changed in v26; keep them only if they
    # belonged to a v26+ user configuration, not old experimental profiles.
    choices = _MIGRATION_KEEP if version >= 26 else _MIGRATION_KEEP - {
        'show_visualizer', 'normalize_loudness', 'fade_ms',
    }
    return {key: value for key, value in data.items() if key in choices}


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
    # Fichier temporaire distinct pour chaque instance : remplacement atomique
    # du JSON existant, jamais d'écriture partielle en cas d'erreur disque.
    staged: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode='w', encoding='utf-8', dir=target.parent,
            prefix=f'.{target.name}.', suffix='.tmp', delete=False,
        ) as handle:
            staged = Path(handle.name)
            json.dump(asdict(config.sanitized()), handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        staged.replace(target)
    finally:
        if staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass
    return target
