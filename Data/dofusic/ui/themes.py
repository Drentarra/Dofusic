from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThemePalette:
    key: str
    label: str
    bg: str
    bg_top: str
    top_glow: str
    card: str
    card_alt: str
    card_field: str
    border: str
    accent_border: str
    text: str
    muted: str
    accent: str
    accent_soft: str
    accent_dark: str
    amber: str
    red: str
    track: str
    track_active: str
    header_chip: str
    zone_card: str
    music_card: str
    restart_fill: str
    visualizer_idle: str
    pin_hole: str


THEME_PRESETS: dict[str, ThemePalette] = {
    'emerald': ThemePalette(
        'emerald', 'Dofusic Emerald', '#06100e', '#071411', '#092019', '#0b1a17', '#0a1715', '#0a1515',
        '#214139', '#197a55', '#f4f8f6', '#8fa69f', '#43e39e', '#91f0c1', '#0d3025', '#f0c56c', '#ff7c7c',
        '#233b35', '#39d995', '#0a1b17', '#0b1d18', '#0a1b17', '#0a1816', '#13513c', '#0a1d18',
    ),
    'bonta': ThemePalette(
        'bonta', 'Bonta', '#071017', '#091824', '#0b2940', '#0c1b26', '#0b1923', '#091720', '#244255', '#1f668c',
        '#f3f8fb', '#91a8b8', '#49bdf2', '#9ddcff', '#12384d', '#f1c873', '#ff8585', '#243b4b', '#3ab5ef',
        '#0b1c28', '#0c1e2a', '#0b1c28', '#0b1a24', '#174c68', '#0b202c',
    ),
    'brakmar': ThemePalette(
        'brakmar', 'Brâkmar', '#140908', '#1d0b0a', '#3a1210', '#21100e', '#1c0f0e', '#190d0c', '#4d2b27', '#8e342b',
        '#fff5f2', '#b89b94', '#ff6757', '#ffad82', '#4a1711', '#ffc56c', '#ff7c7c', '#452521', '#ef5346',
        '#24110f', '#25110f', '#24110f', '#20100e', '#6a251f', '#25120f',
    ),
    'arcane': ThemePalette(
        'arcane', 'Arcane', '#0d0b18', '#121026', '#21164a', '#171429', '#151224', '#131020', '#393253', '#644cb3',
        '#f7f4ff', '#aaa0c3', '#9a7cff', '#c8b7ff', '#2e2459', '#f0c56c', '#ff808f', '#322b46', '#8a6dff',
        '#17132b', '#18142d', '#17132b', '#151226', '#4a3a7a', '#18142d',
    ),
    'ivory': ThemePalette(
        'ivory', 'Ivoire', '#f3efe4', '#ebe5d7', '#ded2b4', '#fffaf0', '#f7f1e5', '#f0eadf', '#c8bea9', '#b79a55',
        '#29261f', '#766e60', '#9b7b2f', '#6f571f', '#e6d7ad', '#9b6d22', '#bb4c4c', '#d5cdbd', '#ab8738',
        '#fbf5e9', '#fff9ee', '#fbf5e9', '#f6f0e3', '#c7b170', '#fff9ee',
    ),
    'graphite': ThemePalette(
        'graphite', 'Graphite', '#0d0f11', '#121518', '#20252a', '#171a1e', '#15181b', '#121519', '#363c42', '#57616a',
        '#f4f5f6', '#9ca3aa', '#d5d9dd', '#eef0f2', '#30353a', '#e2bd71', '#ff7f7f', '#2d3237', '#c9ced3',
        '#171b1f', '#191d21', '#171b1f', '#15191c', '#555d64', '#191d21',
    ),
}


def get_theme(name: str | None) -> ThemePalette:
    return THEME_PRESETS.get((name or '').strip().lower(), THEME_PRESETS['emerald'])


def theme_choices() -> tuple[tuple[str, str], ...]:
    return tuple((key, palette.label) for key, palette in THEME_PRESETS.items())


_THEME_COLOR_ROLES = (
    'bg', 'bg_top', 'top_glow', 'card', 'card_alt', 'card_field',
    'border', 'accent_border', 'text', 'muted', 'accent', 'accent_soft',
    'accent_dark', 'amber', 'red', 'track', 'track_active', 'header_chip',
    'zone_card', 'music_card', 'restart_fill', 'visualizer_idle', 'pin_hole',
)


def theme_color_mapping(target: ThemePalette) -> dict[str, str]:
    """Map every known preset color to the corresponding role in *target*."""
    mapping: dict[str, str] = {}
    for source in THEME_PRESETS.values():
        for role in _THEME_COLOR_ROLES:
            mapping[str(getattr(source, role)).casefold()] = str(getattr(target, role))
    return mapping


def recolor_widget_tree(root, target: ThemePalette) -> None:
    """Recolor classic Tk widgets in-place while preserving their state and values."""
    mapping = theme_color_mapping(target)
    options = (
        'bg', 'background', 'fg', 'foreground', 'activebackground', 'activeforeground',
        'selectcolor', 'selectbackground', 'selectforeground', 'insertbackground', 'highlightbackground', 'highlightcolor',
        'troughcolor', 'buttonbackground',
    )

    def visit(widget) -> None:
        updates: dict[str, str] = {}
        for option in options:
            try:
                current = str(widget.cget(option) or '')
            except Exception:
                continue
            replacement = mapping.get(current.casefold())
            if replacement and replacement != current:
                updates[option] = replacement
        if updates:
            try:
                widget.configure(**updates)
            except Exception:
                # Some Tk widgets expose aliases that cannot all be set at once;
                # retry one-by-one so one unsupported option does not block the rest.
                for option, value in updates.items():
                    try:
                        widget.configure(**{option: value})
                    except Exception:
                        pass
        try:
            children = tuple(widget.winfo_children())
        except Exception:
            children = ()
        for child in children:
            visit(child)

    visit(root)
