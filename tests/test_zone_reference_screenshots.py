"""Pixel regressions using only the HUD text strip, no gameplay recording."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest

from dofusic.vision.layout import trim_zone_line
from tools.image_io import read_image

FIXTURES = Path(__file__).parent / 'hud_fixtures'


@pytest.mark.parametrize('fixture_name', (
    'astrub_pavage_a.png', 'astrub_pavage_b.png',
    'astrub_jardin_a.png', 'astrub_jardin_b.png',
))
@pytest.mark.parametrize('scale', (0.6, 0.8, 1.0, 1.5))
def test_whole_zone_label_survives_background_and_resize(fixture_name, scale):
    raw = read_image(FIXTURES / fixture_name)
    image = cv2.resize(
        raw, None, fx=scale, fy=scale,
        interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC,
    )
    zone = trim_zone_line(
        image,
        gap_stop=round(34 * scale),
        padding=round(5 * scale),
        empty_preview_width=round(76 * scale),
    )
    assert abs(zone.shape[1] - round(204 * scale)) <= 9, (
        fixture_name, scale, zone.shape[1], round(204 * scale)
    )


def test_zone_fixture_decodes_from_unicode_windows_style_path(tmp_path):
    """Prevent the historical Téléchargements/Windows imread regression."""
    source = FIXTURES / 'astrub_pavage_a.png'
    relocated = tmp_path / 'Téléchargements - éàî' / 'captures' / source.name
    relocated.parent.mkdir(parents=True)
    relocated.write_bytes(source.read_bytes())

    assert np.array_equal(read_image(relocated), read_image(source))


def test_corrupted_zone_fixture_does_not_silently_pass(tmp_path):
    corrupted = tmp_path / 'Téléchargements - é' / 'fichier_invalide.png'
    corrupted.parent.mkdir()
    corrupted.write_bytes(b'ceci n\'est pas un PNG')
    with pytest.raises(ValueError, match='Image non décodable'):
        read_image(corrupted)
