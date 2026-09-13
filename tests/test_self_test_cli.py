from __future__ import annotations

import main as app_main


def test_self_test_cli_flag_is_available():
    args = app_main._parse_args(['--self-test'])
    assert args.self_test is True


def test_self_test_source_checks_quickjs_and_ejs_runtime():
    source = __import__('inspect').getsource(app_main._run_self_test)
    assert "'deno'" not in source
    assert "'yt_dlp_ejs'" in source
    assert 'resolve_quickjs_executable' in source
