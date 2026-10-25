"""Régressions de combat à partir de captures recadrées du HUD."""
from pathlib import Path

import cv2
import numpy as np
import pytest

from Data.tools.hud_reference import validated_hud_measurement
from dofusic.vision.combat import CombatStateTracker, analyze_combat_toolbar
from dofusic.vision.layout import HUDGeometry
from tools.hud_reference import HUDTransformSmoother, estimate_hud_transform, toolbar_icon_signature
from tools.hud_reference import extract_reference_hud_inputs as extract_hud_inputs

IMAGES = Path(__file__).parent / 'fixtures' / 'combat_reference'


def image(number, zoom=1.0):
    encoded = np.fromfile(str(IMAGES / f'{number}.png'), np.uint8)
    src = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    assert src is not None
    if zoom == 1:
        return src
    return cv2.resize(src, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_LINEAR)


@pytest.mark.parametrize('before', [1, 2, 3])
@pytest.mark.parametrize('zoom', [1., 1.25])
def test_reference_expanded_combat_not_dependent_on_previous_hud(before, zoom):
    geometry = HUDGeometry()
    smoother = HUDTransformSmoother(confirmations=4)
    previous = image(before, zoom)
    known = estimate_hud_transform(previous, geometry)
    assert known is not None
    signature = toolbar_icon_signature(previous, known)
    assert smoother.update(known) == known
    stable = smoother.stable
    actual = image(4, zoom)
    recognitions = []
    tracker = CombatStateTracker(required_confirmations=3)
    # Several identical video frames: scale should recover without needing
    # the user to show screenshot 3 (collapsed combat) first.
    for _ in range(12):
        measured = validated_hud_measurement(actual, geometry, stable, signature)
        transform = smoother.update(measured)
        assert transform is not None
        if measured is not None and transform == measured:
            signature = toolbar_icon_signature(actual, transform)
        stable = smoother.stable
        observation = analyze_combat_toolbar(extract_hud_inputs(actual, geometry, transform=transform).combat)
        recognitions.append(observation.in_combat)
        tracker.update(observation)
    assert tracker.in_combat is True, (before, zoom, recognitions)
    assert recognitions[-4:] == [True]*4


def test_reference_exploration_2_still_not_recognized_as_combat():
    for n, expected in ((1, False), (2, False), (3, True), (4, True)):
        raw = image(n)
        state = analyze_combat_toolbar(extract_hud_inputs(raw).combat)
        assert state.in_combat is expected


def test_real_video_expanded_combat_is_recognized_without_prior_combat_state():
    """t=28 s : barre réellement dépliée de la vidéo de jeu reçue."""
    path = IMAGES / 'video_expanded_28s.png'
    recorded = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    observation = analyze_combat_toolbar(extract_hud_inputs(recorded).combat)
    assert observation.in_combat is True
    tracker = CombatStateTracker(required_confirmations=3)
    for _ in range(3):
        tracker.update(observation)
    assert tracker.in_combat is True


@pytest.mark.parametrize('zoom', [1., 1.25])
def test_reference_return_to_expanded_exploration(zoom):
    """After 4 -> 1, the confirmed state must eventually return to exploration."""
    tracker = CombatStateTracker(required_confirmations=3)
    for key in (4, 4, 4):
        tracker.update(analyze_combat_toolbar(extract_hud_inputs(image(key, zoom)).combat))
    assert tracker.in_combat
    for key in (1, 1, 1):
        tracker.update(analyze_combat_toolbar(extract_hud_inputs(image(key, zoom)).combat))
    assert tracker.in_combat is False
