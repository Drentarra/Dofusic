from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
import threading

from dofusic.online.discovery import YouTubeDiscoveryClient
from dofusic.online.models import OnlineTrack


class SearchPhase(str, Enum):
    IDLE = 'idle'
    LOADING = 'loading'
    RESULTS = 'results'
    EMPTY = 'empty'
    ERROR = 'error'


@dataclass(frozen=True, slots=True)
class SearchSnapshot:
    revision: int
    phase: SearchPhase
    query: str
    results: tuple[OnlineTrack, ...]
    error: str = ''


@dataclass(frozen=True, slots=True)
class SuggestionSnapshot:
    revision: int
    query: str
    values: tuple[str, ...]


class SearchController:
    """Own all asynchronous discovery state outside Tkinter.

    Search and suggestions have independent executors and freshness generations.
    Only the newest submitted request may publish state. Stale completions are
    discarded and queued stale futures are cancelled whenever possible.
    """

    def __init__(
        self,
        discovery: YouTubeDiscoveryClient,
        *,
        result_limit: int = 8,
        executor=None,
        suggestion_executor=None,
    ) -> None:
        self.discovery = discovery
        self.result_limit = max(1, int(result_limit))
        self._executor = executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='DofusicSearch')
        self._suggestion_executor = suggestion_executor or ThreadPoolExecutor(max_workers=1, thread_name_prefix='DofusicSuggest')
        self._owns_executor = executor is None
        self._owns_suggestion_executor = suggestion_executor is None
        self._lock = threading.RLock()

        self._generation = 0
        self._future: Future | None = None
        self._snapshot = SearchSnapshot(0, SearchPhase.IDLE, '', tuple(), '')

        self._suggestion_generation = 0
        self._suggestion_future: Future | None = None
        self._suggestion_query = ''
        self._suggestions = SuggestionSnapshot(0, '', tuple())

        self._prewarm_future: Future | None = None

    def snapshot(self) -> SearchSnapshot:
        with self._lock:
            return self._snapshot

    def suggestions_snapshot(self) -> SuggestionSnapshot:
        with self._lock:
            return self._suggestions

    @property
    def busy(self) -> bool:
        return self.snapshot().phase is SearchPhase.LOADING

    def prewarm(self) -> None:
        with self._lock:
            future = self._prewarm_future
            if future is not None and not future.done():
                return
            try:
                self._prewarm_future = self._executor.submit(self.discovery.prewarm)
            except RuntimeError:
                self._prewarm_future = None

    def submit_search(self, query: str) -> int:
        text = ' '.join(str(query or '').split()).strip()
        if not text:
            return self._generation
        with self._lock:
            current = self._future
            if (
                current is not None
                and not current.done()
                and self._snapshot.phase is SearchPhase.LOADING
                and self._snapshot.query.casefold() == text.casefold()
            ):
                return self._generation
            if current is not None and not current.done():
                current.cancel()
            self._generation += 1
            generation = self._generation
            self._snapshot = SearchSnapshot(generation, SearchPhase.LOADING, text, self._snapshot.results, '')
            self.cancel_suggestions()
            try:
                future = self._executor.submit(self.discovery.search, text, limit=self.result_limit)
            except RuntimeError as exc:
                self._future = None
                self._snapshot = SearchSnapshot(generation, SearchPhase.ERROR, text, tuple(), str(exc))
                return generation
            setattr(future, '_dofusic_generation', generation)
            setattr(future, '_dofusic_query', text)
            self._future = future
            return generation

    def submit_suggestions(self, query: str, *, limit: int = 8) -> int:
        text = ' '.join(str(query or '').split()).strip()
        with self._lock:
            self._suggestion_generation += 1
            generation = self._suggestion_generation
            previous = self._suggestion_future
            if previous is not None and not previous.done():
                previous.cancel()
            self._suggestion_query = text
            if len(text) < 2:
                self._suggestion_future = None
                self._suggestions = SuggestionSnapshot(generation, text, tuple())
                return generation
            try:
                future = self._suggestion_executor.submit(self.discovery.suggestions, text, limit=max(1, int(limit)))
            except RuntimeError:
                self._suggestion_future = None
                self._suggestions = SuggestionSnapshot(generation, text, tuple())
                return generation
            setattr(future, '_dofusic_generation', generation)
            self._suggestion_future = future
            return generation

    def cancel_suggestions(self) -> None:
        self._suggestion_generation += 1
        future = self._suggestion_future
        self._suggestion_future = None
        if future is not None and not future.done():
            future.cancel()
        self._suggestions = SuggestionSnapshot(self._suggestion_generation, '', tuple())

    def tick(self) -> None:
        with self._lock:
            future = self._future
            if future is not None and future.done():
                self._future = None
                generation = int(getattr(future, '_dofusic_generation', -1))
                query = str(getattr(future, '_dofusic_query', '') or '')
                if generation == self._generation:
                    try:
                        results = tuple(future.result() or ())
                    except Exception as exc:
                        self._snapshot = SearchSnapshot(generation, SearchPhase.ERROR, query, tuple(), str(exc))
                    else:
                        phase = SearchPhase.RESULTS if results else SearchPhase.EMPTY
                        self._snapshot = SearchSnapshot(generation, phase, query, results, '')

            suggestion = self._suggestion_future
            if suggestion is not None and suggestion.done():
                self._suggestion_future = None
                generation = int(getattr(suggestion, '_dofusic_generation', -1))
                if generation == self._suggestion_generation:
                    try:
                        values = tuple(str(value) for value in (suggestion.result() or ()) if str(value).strip())
                    except Exception:
                        values = tuple()
                    self._suggestions = SuggestionSnapshot(generation, self._suggestion_query, values)

            prewarm = self._prewarm_future
            if prewarm is not None and prewarm.done():
                self._prewarm_future = None
                try:
                    prewarm.result()
                except Exception:
                    pass

    def close(self) -> None:
        with self._lock:
            for future in (self._future, self._suggestion_future, self._prewarm_future):
                if future is not None and not future.done():
                    future.cancel()
            self._future = None
            self._suggestion_future = None
            self._prewarm_future = None
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
        if self._owns_suggestion_executor and self._suggestion_executor is not self._executor:
            self._suggestion_executor.shutdown(wait=False, cancel_futures=True)
