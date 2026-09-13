from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Iterable, Sequence

from rapidfuzz import fuzz

TEXT_CACHE_SIZE = 4096

STOP_WORDS = frozenset({
    'de', 'des', 'du', 'la', 'le', 'les', 'un', 'une', 'et', 'en', 'au', 'aux',
    'd', 'l', 'a', 'the', 'sur', 'sous', 'dans', 'vers', 'pour', 'avec', 'sans', 'chez',
})

GENERIC_PLACE_WORDS = frozenset({
    'zone', 'lieu', 'carte', 'map', 'monde', 'dimension', 'dimensions',
    'salle', 'salles', 'salon', 'salons', 'hall', 'halls', 'etage', 'niveau', 'partie',
    'tour', 'tours', 'donjon', 'antre', 'grotte', 'caverne', 'cavernes',
    'repaire', 'temple', 'sanctuaire', 'palais', 'chateau', 'maison',
    'village', 'cite', 'ville', 'port', 'route', 'chemin', 'chemins', 'passage', 'tunnel',
    'mine', 'mines', 'foret', 'bois', 'champ', 'champs', 'plaine', 'plaines',
    'lac', 'lacs', 'ile', 'iles', 'ilot', 'ilots', 'archipel', 'baie',
    'bord', 'bordure', 'centre', 'coin', 'cour', 'quartier', 'territoire',
    'souterrain', 'souterrains', 'egout', 'egouts', 'crypte', 'cryptes',
    'cimetiere', 'camp', 'campement', 'domaine', 'jardin', 'jardins',
    'atelier', 'laboratoire', 'laboratoires', 'arena', 'arenes', 'taverne', 'auberge',
})

_TRANSLATION = str.maketrans({
    '’': "'", '`': "'", '´': "'", 'ʻ': "'", 'ʼ': "'",
    '—': '-', '–': '-', '−': '-', '‑': '-',
    '\u00a0': ' ', '|': ' ', '•': ' ',
})


class TextMatchMode(str, Enum):
    EXACT = 'exact'
    CONTAINED = 'contained'
    FUZZY = 'fuzzy'
    NONE = 'none'


@dataclass(frozen=True, slots=True)
class TextSimilarity:
    score: float
    mode: TextMatchMode
    target_coverage: float


@lru_cache(maxsize=TEXT_CACHE_SIZE)
def _clean_text_cached(text: str) -> str:
    value = text.translate(_TRANSLATION)
    value = re.sub(r'\+\s*Nouveau', '', value, flags=re.IGNORECASE)
    value = re.sub(r'\bNouveau\b', '', value, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', value).strip(' \t\r\n')


def clean_text(value: object) -> str:
    return _clean_text_cached(str(value or ''))


@lru_cache(maxsize=TEXT_CACHE_SIZE)
def _strip_accents_cached(text: str) -> str:
    value = _clean_text_cached(text).lower().replace('œ', 'oe').replace('æ', 'ae')
    value = unicodedata.normalize('NFD', value)
    return ''.join(char for char in value if unicodedata.category(char) != 'Mn')



@lru_cache(maxsize=TEXT_CACHE_SIZE)
def _normalize_text_cached(text: str) -> str:
    value = _strip_accents_cached(text)
    value = re.sub(r'\s*-\s*', ' - ', value)
    return re.sub(r'\s+', ' ', value).strip()


def normalize_text(value: object) -> str:
    return _normalize_text_cached(str(value or ''))


@lru_cache(maxsize=TEXT_CACHE_SIZE)
def _words_cached(text: str) -> tuple[str, ...]:
    return tuple(re.findall(r'[a-z0-9]+', _normalize_text_cached(text)))



def _soft_singular(word: str) -> str:
    if len(word) >= 4 and word.endswith('s') and not word.endswith('ss'):
        return word[:-1]
    return word


@lru_cache(maxsize=TEXT_CACHE_SIZE)
def _match_words_cached(text: str) -> tuple[str, ...]:
    return tuple(_soft_singular(word) for word in _words_cached(text))



@lru_cache(maxsize=TEXT_CACHE_SIZE)
def _content_words_cached(text: str) -> tuple[str, ...]:
    return tuple(word for word in _match_words_cached(text) if word not in STOP_WORDS)


def content_words(value: object) -> tuple[str, ...]:
    return _content_words_cached(str(value or ''))


@lru_cache(maxsize=TEXT_CACHE_SIZE)
def _norm_key_cached(text: str) -> str:
    return ''.join(_words_cached(text))


def norm_key(value: object) -> str:
    return _norm_key_cached(str(value or ''))


@lru_cache(maxsize=TEXT_CACHE_SIZE)
def _match_key_cached(text: str) -> str:
    return ''.join(_content_words_cached(text))


def match_key(value: object) -> str:
    return _match_key_cached(str(value or ''))


@lru_cache(maxsize=TEXT_CACHE_SIZE)
def _important_words_cached(text: str) -> tuple[str, ...]:
    return tuple(
        word for word in _words_cached(text)
        if len(word) >= 3 and word not in STOP_WORDS and word not in GENERIC_PLACE_WORDS
    )


def important_words(value: object) -> tuple[str, ...]:
    return _important_words_cached(str(value or ''))


def unique_texts(values: Iterable[object]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        key = match_key(text)
        if text and key and key not in seen:
            seen.add(key)
            result.append(text)
    return tuple(result)


def _contains_sequence(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    if not needle or len(needle) > len(haystack):
        return False
    width = len(needle)
    return any(tuple(haystack[i:i + width]) == tuple(needle) for i in range(len(haystack) - width + 1))


def _best_token_alignment(target: Sequence[str], observed: Sequence[str]) -> tuple[float, float]:
    if not target or not observed:
        return 0.0, 0.0

    used: set[int] = set()
    similarities: list[float] = []
    strong = 0
    for target_word in target:
        best_index = -1
        best_score = 0.0
        for index, observed_word in enumerate(observed):
            if index in used:
                continue
            score = 100.0 if target_word == observed_word else float(fuzz.ratio(target_word, observed_word))
            if score > best_score:
                best_score = score
                best_index = index
        if best_index >= 0:
            used.add(best_index)
        similarities.append(best_score)
        if best_score >= 75.0:
            strong += 1

    avg = sum(similarities) / len(similarities)
    coverage = strong / len(target)
    return avg, coverage


def _text_similarity_words(target_words: Sequence[str], observed_words: Sequence[str]) -> TextSimilarity:
    if not target_words or not observed_words:
        return TextSimilarity(0.0, TextMatchMode.NONE, 0.0)
    if tuple(target_words) == tuple(observed_words):
        return TextSimilarity(100.0, TextMatchMode.EXACT, 1.0)
    if _contains_sequence(observed_words, target_words):
        return TextSimilarity(99.0, TextMatchMode.CONTAINED, 1.0)

    aligned, coverage = _best_token_alignment(target_words, observed_words)
    whole = float(fuzz.WRatio(' '.join(target_words), ' '.join(observed_words)))
    score = aligned * 0.72 + whole * 0.28
    if coverage < 1.0:
        score -= (1.0 - coverage) * 18.0
    score = max(0.0, min(98.0, score))
    return TextSimilarity(score, TextMatchMode.FUZZY, coverage)


def text_similarity(target: object, observed: object) -> TextSimilarity:
    """Directionally compare a canonical place label with observed HUD text."""
    return _text_similarity_words(content_words(target), content_words(observed))
