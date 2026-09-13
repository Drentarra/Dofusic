from __future__ import annotations

import os
from pathlib import Path

import dofusic.online.retrieval as retrieval_module
from dofusic.online.media_cache import MediaCache
from dofusic.online.retrieval import YouTubeAudioFetcher


def test_ytdlp_format_accepts_any_best_audio_codec(tmp_path):
    fetcher = YouTubeAudioFetcher(tmp_path / 'cache')
    options = fetcher._options('abcdefghijk')

    assert options['format'] == 'bestaudio/best'


def test_quickjs_resolver_uses_builder_override(monkeypatch, tmp_path):
    qjs_exe = tmp_path / 'qjs.exe'
    qjs_exe.write_bytes(b'qjs')
    monkeypatch.setenv('DOFUSIC_QJS_BINARY', str(qjs_exe))
    monkeypatch.delattr(retrieval_module.sys, '_MEIPASS', raising=False)
    assert retrieval_module.resolve_quickjs_executable() == qjs_exe


def test_cache_recognizes_webm_and_opus_sources(tmp_path):
    cache = MediaCache(tmp_path / 'cache', tmp_path / 'music')
    webm = cache.cache_dir / 'abcdefghijk.webm'
    webm.write_bytes(b'webm')
    assert cache._cached_path('abcdefghijk') == webm

    webm.unlink()
    opus = cache.cache_dir / 'abcdefghijk.opus'
    opus.write_bytes(b'opus')
    assert cache._cached_path('abcdefghijk') == opus


def test_retrieval_layer_owns_ytdlp_and_has_no_invidious_direct_stream_path():
    assert not hasattr(MediaCache, '_download_with_invidious')
    assert hasattr(retrieval_module, 'resolve_quickjs_executable')
    assert hasattr(YouTubeAudioFetcher, 'fetch')


def test_frozen_runtime_prefers_bundled_quickjs(monkeypatch, tmp_path):
    bundled = tmp_path / 'js' / 'qjs.exe'
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b'qjs')

    monkeypatch.setattr(retrieval_module.sys, '_MEIPASS', str(tmp_path), raising=False)
    monkeypatch.setenv('DOFUSIC_QJS_BINARY', str(tmp_path / 'wrong.exe'))

    assert retrieval_module.resolve_quickjs_executable() == bundled


def test_main_self_test_uses_shared_quickjs_resolver():
    main_source = (Path(__file__).resolve().parents[1] / 'Data' / 'main.py').read_text(encoding='utf-8')
    assert 'from dofusic.online.retrieval import resolve_quickjs_executable' in main_source
    assert 'qjs_bin = resolve_quickjs_executable()' in main_source
