from __future__ import annotations

from pathlib import Path

import pytest

from dofusic.online.models import OnlineTrack


def test_discovery_preserves_ytdlp_duration_for_cache_guard():
    from dofusic.online.discovery import YouTubeDiscoveryClient

    client = YouTubeDiscoveryClient(
        search_runner=lambda _query, _limit: ({'id': 'abcdefghijk', 'title': 'Long mix', 'duration': 24780},),
        suggestion_runner=lambda _query, _limit: (),
    )
    track = client.search('long mix')[0]
    assert track.duration_seconds == 24780
    assert track.duration_text == '6:53:00'


def test_media_cache_rejects_multi_hour_video_before_downloader_runs(tmp_path):
    from dofusic.online.errors import MediaCacheError, MediaErrorCode
    from dofusic.online.media_cache import MediaCache

    calls = []

    def downloader(track, cache_dir):
        calls.append(track.video_id)
        raise AssertionError('downloader must not run for an oversized online video')

    cache = MediaCache(tmp_path / 'cache', tmp_path / 'music', downloader=downloader)
    track = OnlineTrack('abcdefghijk', 'Zelda ambience', duration_seconds=6 * 3600 + 53 * 60)

    with pytest.raises(MediaCacheError) as caught:
        cache.ensure_cached(track)

    assert caught.value.code is MediaErrorCode.TOO_LONG
    assert '6:53:00' in str(caught.value)
    assert '2:00:00' in str(caught.value)
    assert calls == []


def test_two_hour_video_remains_allowed(tmp_path):
    from dofusic.online.media_cache import MediaCache

    source = tmp_path / 'cache' / 'abcdefghijk.mp3'

    def downloader(track, cache_dir):
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b'audio')
        return source

    def transcoder(source_path, target):
        Path(target).write_bytes(Path(source_path).read_bytes())
        return target

    cache = MediaCache(tmp_path / 'cache', tmp_path / 'music', downloader=downloader, transcoder=transcoder)
    track = OnlineTrack('abcdefghijk', 'Long mix', duration_seconds=2 * 3600)

    assert cache.ensure_cached(track) == tmp_path / 'cache' / 'abcdefghijk.opus'
