from __future__ import annotations

from pathlib import Path

from dofusic.online.media_cache import MediaCache
from dofusic.online.models import OnlineTrack


def test_save_to_library_reuses_cached_media_without_redownloading(tmp_path):
    calls = []

    def downloader(track, cache_dir):
        calls.append(track.video_id)
        target = Path(cache_dir) / f'{track.video_id}.mp3'
        target.write_bytes(b'audio')
        return target

    def transcoder(source, target):
        Path(target).write_bytes(Path(source).read_bytes())
        return target

    cache = MediaCache(tmp_path / 'cache', tmp_path / 'music', downloader=downloader, transcoder=transcoder)
    track = OnlineTrack('abcdefghijk', 'Titre')

    cached = cache.ensure_cached(track)
    saved = cache.save_to_library(track, 'Mon titre')

    assert cached.name == 'abcdefghijk.opus'
    assert saved == tmp_path / 'music' / 'Mon titre.opus'
    assert saved.read_bytes() == b'audio'
    assert calls == ['abcdefghijk']


def test_custom_downloader_is_invoked_once(tmp_path):
    calls = []
    downloaded = tmp_path / 'cache' / 'abcdefghijk.mp3'

    def downloader(track, cache_dir):
        calls.append((track.video_id, Path(cache_dir)))
        downloaded.parent.mkdir(parents=True, exist_ok=True)
        downloaded.write_bytes(b'audio')
        return downloaded

    def transcoder(source, target):
        Path(target).write_bytes(Path(source).read_bytes())
        return target

    cache = MediaCache(tmp_path / 'cache', tmp_path / 'music', downloader=downloader, transcoder=transcoder)
    track = OnlineTrack('abcdefghijk', 'Titre')
    expected = tmp_path / 'cache' / 'abcdefghijk.opus'

    assert cache.ensure_cached(track) == expected
    assert cache.ensure_cached(track) == expected
    assert calls == [('abcdefghijk', tmp_path / 'cache')]
