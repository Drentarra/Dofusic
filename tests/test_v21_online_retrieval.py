from __future__ import annotations

import pytest

from dofusic.online.models import OnlineTrack


def _track():
    return OnlineTrack('abcdefghijk', 'Titre')


def test_bot_challenge_is_a_typed_error():
    from dofusic.online.errors import MediaErrorCode
    from dofusic.online.retrieval import classify_retrieval_error

    code = classify_retrieval_error(RuntimeError("Sign in to confirm you’re not a bot"))

    assert code is MediaErrorCode.BOT_CHALLENGE


def test_bot_challenge_is_surfaced_without_repeating_the_request(tmp_path):
    from dofusic.online.errors import MediaCacheError, MediaErrorCode
    from dofusic.online.retrieval import YouTubeAudioFetcher

    calls = []
    def runner(track, options):
        calls.append(dict(options))
        raise RuntimeError("Sign in to confirm you’re not a bot")

    sleeps = []
    fetcher = YouTubeAudioFetcher(tmp_path, runner=runner, sleep_fn=sleeps.append)

    with pytest.raises(MediaCacheError) as caught:
        fetcher.fetch(_track())

    assert caught.value.code is MediaErrorCode.BOT_CHALLENGE
    assert 'anti-bot' in str(caught.value)
    assert len(calls) == 1
    assert sleeps == []
