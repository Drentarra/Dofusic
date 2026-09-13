from __future__ import annotations

import dofusic.ui.overlay as overlay


def test_overlay_has_no_zone_width_memory_or_stabilizer():
    assert not hasattr(overlay, '_ZoneWidthStabilizer')
