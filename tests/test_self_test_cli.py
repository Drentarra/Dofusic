from __future__ import annotations

import main as app_main


def test_self_test_cli_flag_is_available():
    args = app_main._parse_args(['--self-test'])
    assert args.self_test is True


def test_self_test_checks_local_ffmpeg_and_omits_online_runtimes():
    source = __import__('inspect').getsource(app_main._run_self_test)
    assert "'deno'" not in source
    assert "'yt_dlp_ejs'" not in source
    assert 'resolve_quickjs_executable' not in source
    assert 'imageio_ffmpeg.get_ffmpeg_exe()' in source
