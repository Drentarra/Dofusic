"""UI regression: the detection chip must never overflow its rounded border."""

from dofusic.ui.main_window import _AdaptiveStatusBinding, fit_text_to_width


class FakeCanvas:
    def __init__(self):
        self.options = {3: {'text': 'En attente de Dofus.exe'}}
        self.position = {}

    def itemcget(self, item, option):
        return self.options[item][option]

    def itemconfigure(self, item, **options):
        self.options.setdefault(item, {}).update(options)

    def coords(self, item, *positions):
        self.position[item] = positions


def _chip(font_pixels_per_character=8):
    canvas = FakeCanvas()
    def measure(value):
        return len(value) * font_pixels_per_character
    binding = _AdaptiveStatusBinding(
        canvas, 3, background_item=1, dot_item=2, measure=measure,
    )
    return canvas, binding, measure


def _check_fit(canvas, measure):
    outline = canvas.position[1]
    left = min(outline[::2])
    right = max(outline[::2])
    text_x, _y = canvas.position[3]
    fitted = canvas.options[3]['text']
    dot_x1, _dy1, dot_x2, _dy2 = canvas.position[2]
    assert left >= 124  # The music/settings buttons remain unobstructed.
    assert right == 538  # Right alignment is constant.
    assert left < dot_x1 < dot_x2 < text_x
    assert text_x + measure(fitted) <= right - 12


def test_badge_grows_left_for_dofus_status_without_cutting_the_text():
    canvas, _binding, measure = _chip(font_pixels_per_character=8)
    _check_fit(canvas, measure)
    assert canvas.options[3]['text'] == 'En attente de Dofus.exe'
    assert min(canvas.position[1][::2]) < 374  # 164-pixel old chip was too narrow.


def test_status_chip_reflows_short_long_and_error_states():
    canvas, binding, measure = _chip()
    waiting_left = min(canvas.position[1][::2])
    for text in ('Dofus détecté', 'Initialisation…', 'Échec détection',
                 'Erreur Dofusic', 'Erreur au démarrage'):
        binding.config(text=text, fg='#fafafa')
        _check_fit(canvas, measure)
        assert canvas.options[3]['text'] == text
        assert canvas.options[3]['fill'] == '#fafafa'
    assert min(canvas.position[1][::2]) >= waiting_left


def test_status_chip_respects_maximum_width_at_high_dpi():
    canvas, binding, measure = _chip(font_pixels_per_character=15)
    _check_fit(canvas, measure)
    binding.config(text='Attention : message volontairement très long dans le statut')
    _check_fit(canvas, measure)
    assert canvas.options[3]['text'].endswith('…')
    assert min(canvas.position[1][::2]) == 124


def test_text_fitter_handles_narrow_spaces_without_overflow():
    def measure(value):
        return len(value) * 10
    assert fit_text_to_width('Bonjour', 70, measure) == 'Bonjour'
    assert fit_text_to_width('Bonjour', 55, measure) == 'Bonj…'
    assert fit_text_to_width('Bonjour', 10, measure) == '…'
    assert fit_text_to_width('Bonjour', 9, measure) == ''
    assert fit_text_to_width('', 100, measure) == ''
    assert fit_text_to_width('Bonjour', 0, measure) == ''
