"""Exercise the actual publication step without sending requests to GitHub."""

import io
import json
from pathlib import Path
import subprocess
from urllib.error import HTTPError

import pytest
import yaml


WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/build-release.yml'


def _workflow():
    return yaml.load(WORKFLOW.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


def _publish(monkeypatch, *, status=200, upload_fails=False):
    import urllib.request

    calls = []
    monkeypatch.setenv('GH_REPO', 'Drentarra/Dofusic')
    monkeypatch.setenv('GH_TOKEN', 'test-token')
    monkeypatch.setenv('RELEASE_TAG', 'v1.0.6')
    monkeypatch.setenv('GITHUB_RUN_ID', '123456')

    def read_release(request, **kwargs):
        assert request.full_url == 'https://api.github.com/repos/Drentarra/Dofusic/releases/tags/v1.0.6'
        assert request.get_header('Authorization') == 'Bearer test-token'
        if status != 200:
            raise HTTPError(request.full_url, status, 'API error', {}, None)
        return io.BytesIO(json.dumps({'tag_name': 'v1.0.6', 'name': 'User title', 'body': 'User description'}).encode())

    def run(command, **kwargs):
        assert kwargs.get('check') is True
        calls.append(command)
        if upload_fails and command[:3] == ['gh', 'release', 'upload']:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(urllib.request, 'urlopen', read_release)
    monkeypatch.setattr(subprocess, 'run', run)
    step = next(s for s in _workflow()['jobs']['release-windows']['steps'] if 'RELEASE_TAG' in s.get('env', {}))
    return step['run'], calls


def test_existing_release_receives_actions_zip_without_replacing_notes(monkeypatch):
    code, calls = _publish(monkeypatch)
    exec(compile(code, '<publication-step>', 'exec'), {})
    assert [c[2] for c in calls] == ['upload', 'edit']
    assert 'Release/Dofusic.zip' in calls[0]
    assert '--clobber' in calls[0]
    assert '--latest' in calls[1]
    assert '--draft=false' in calls[1]
    assert not any(arg in {'--title', '--notes', '--generate-notes'} for c in calls for arg in c)


def test_absent_release_is_created_with_only_the_portable_zip(monkeypatch):
    code, calls = _publish(monkeypatch, status=404)
    exec(compile(code, '<publication-step>', 'exec'), {})
    assert len(calls) == 1
    command = calls[0]
    assert command[:4] == ['gh', 'release', 'create', 'v1.0.6']
    assert '--verify-tag' in command and '--latest' in command
    assert '--generate-notes' in command
    assert command.count('Release/Dofusic.zip') == 1
    assert not any(arg.endswith(('.sha256', '.json', '.txt')) for arg in command)


def test_new_release_notes_link_source_tag_and_actual_actions_run(monkeypatch):
    code, calls = _publish(monkeypatch, status=404)
    exec(compile(code, '<publication-step>', 'exec'), {})
    command = calls[0]
    assert '--notes' in command
    notes = command[command.index('--notes') + 1]
    assert 'https://github.com/Drentarra/Dofusic/tree/v1.0.6' in notes
    assert 'https://github.com/Drentarra/Dofusic/actions/runs/123456' in notes
    assert 'Musiques inclus' in notes


def test_rebuild_branch_updates_existing_release_with_complete_zip_and_new_build_link(monkeypatch):
    code, calls = _publish(monkeypatch)
    monkeypatch.setenv('RELEASE_TAG', 'rebuild/v1.0.6')
    monkeypatch.setenv('RELEASE_REF', 'refs/heads/rebuild/v1.0.6')
    exec(compile(code, '<publication-step>', 'exec'), {})
    assert calls[0][:4] == ['gh', 'release', 'upload', 'v1.0.6']
    notes = calls[1][calls[1].index('--notes') + 1]
    assert 'Musiques inclus' in notes
    assert 'https://github.com/Drentarra/Dofusic/tree/v1.0.6' in notes
    assert 'https://github.com/Drentarra/Dofusic/actions/runs/123456' in notes


def test_rebuild_resolves_existing_source_tag_and_pins_verification_to_built_commit(monkeypatch, tmp_path):
    workflow = _workflow()
    assert 'rebuild/v*' in workflow['on']['push']['branches']
    build = workflow['jobs']['build-windows']
    resolve = next(s for s in build['steps'] if s['name'] == 'Validate requested release tag')
    output = tmp_path / 'outputs'
    monkeypatch.setenv('GITHUB_OUTPUT', str(output))
    monkeypatch.setenv('RELEASE_TAG', '')
    monkeypatch.setenv('RELEASE_REF', 'refs/heads/rebuild/v1.0.10')
    monkeypatch.setenv('SOURCE_SHA', 'a' * 40)
    exec(compile(resolve['run'], '<source-resolution>', 'exec'), {})
    assert 'source_ref=refs/tags/v1.0.10' in output.read_text()
    assert 'release_tag=v1.0.10' in output.read_text()
    checkout = next(s for s in workflow['jobs']['release-windows']['steps'] if s.get('uses', '').startswith('actions/checkout@'))
    assert 'needs.build-windows.outputs.source_sha' in checkout['with']['ref']


def test_release_branch_publishes_its_version_tag(monkeypatch):
    code, calls = _publish(monkeypatch, status=404)
    monkeypatch.setenv('RELEASE_TAG', 'release/v1.0.6')
    monkeypatch.setenv('RELEASE_REF', 'refs/heads/release/v1.0.6')
    exec(compile(code, '<publication-step>', 'exec'), {})
    assert calls[0][:4] == ['gh', 'release', 'create', 'v1.0.6']


@pytest.mark.parametrize('existing_sha', [None, 'a' * 40, 'b' * 40])
def test_release_branch_tag_is_bound_to_checked_out_source(monkeypatch, existing_sha):
    import urllib.request

    monkeypatch.setenv('GH_REPO', 'Drentarra/Dofusic')
    monkeypatch.setenv('GH_TOKEN', 'test-token')
    monkeypatch.setenv('RELEASE_REF', 'refs/heads/release/v1.0.10')
    source_sha = 'a' * 40
    calls = []

    def read_tag(request, **kwargs):
        assert request.full_url.endswith('/git/ref/tags/v1.0.10')
        if existing_sha is None:
            raise HTTPError(request.full_url, 404, 'Not Found', {}, None)
        return io.BytesIO(json.dumps({'object': {'type': 'commit', 'sha': existing_sha}}).encode())

    def run(command, **kwargs):
        assert kwargs.get('check') is True
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=source_sha + '\n')

    monkeypatch.setattr(urllib.request, 'urlopen', read_tag)
    monkeypatch.setattr(subprocess, 'run', run)
    step = next((s for s in _workflow()['jobs']['release-windows']['steps'] if s['name'] == 'Bind release branch tag to built source'), None)
    assert step is not None
    if existing_sha not in (None, source_sha):
        with pytest.raises(ValueError, match='different source'):
            exec(compile(step['run'], '<tag-step>', 'exec'), {})
        assert not any(c[:2] == ['gh', 'api'] for c in calls)
    else:
        exec(compile(step['run'], '<tag-step>', 'exec'), {})
        api_calls = [c for c in calls if c[:2] == ['gh', 'api']]
        assert len(api_calls) == (1 if existing_sha is None else 0)
        if api_calls:
            assert 'ref=refs/tags/v1.0.10' in api_calls[0]
            assert f'sha={source_sha}' in api_calls[0]


def test_release_branch_trigger_and_checkout_pin_build_source():
    workflow = _workflow()
    assert 'release/v*' in workflow['on']['push']['branches']
    build = workflow['jobs']['build-windows']
    checkout = next(s for s in build['steps'] if s.get('uses', '').startswith('actions/checkout@'))
    assert 'steps.release.outputs.source_ref' in checkout['with']['ref']
    for name in ('Attest final public ZIP', 'Publish GitHub release'):
        step = next(s for s in workflow['jobs']['release-windows']['steps'] if s['name'] == name)
        assert "startsWith(github.ref, 'refs/heads/release/')" in step['if']


@pytest.mark.parametrize('status', [401, 403, 429, 500])
def test_api_errors_never_trigger_release_creation(monkeypatch, status):
    code, calls = _publish(monkeypatch, status=status)
    with pytest.raises(HTTPError) as error:
        exec(compile(code, '<publication-step>', 'exec'), {})
    assert error.value.code == status
    assert calls == []


def test_failed_upload_stops_before_marking_the_release_latest(monkeypatch):
    code, calls = _publish(monkeypatch, upload_fails=True)
    with pytest.raises(subprocess.CalledProcessError):
        exec(compile(code, '<publication-step>', 'exec'), {})
    assert len(calls) == 1 and calls[0][2] == 'upload'


def test_manual_recovery_builds_the_tag_and_keeps_attestation(monkeypatch):
    workflow = _workflow()
    assert workflow['on']['workflow_dispatch']['inputs']['release_tag']['default'] == ''
    build = workflow['jobs']['build-windows']
    final = workflow['jobs']['release-windows']
    resolve = next(s for s in build['steps'] if s['name'] == 'Validate requested release tag')
    assert "source_ref = f'refs/tags/{tag}'" in resolve['run']
    attest = next(s for s in final['steps'] if s['name'] == 'Attest final public ZIP')
    publish = next(s for s in final['steps'] if 'RELEASE_TAG' in s.get('env', {}))
    assert attest['if'] == publish['if']
    assert "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in publish['if']
    assert "github.event_name == 'workflow_dispatch' && inputs.release_tag != ''" in publish['if']
    assert "inputs.release_tag != ''" in publish['if']
    assert 'inputs.release_tag || github.ref_name' in publish['env']['RELEASE_TAG']


@pytest.mark.parametrize('tag', ['v1.0.6', 'v1.0.7-rc.1'])
def test_valid_manual_tag_is_accepted(monkeypatch, tmp_path, tag):
    monkeypatch.setenv('RELEASE_TAG', tag)
    monkeypatch.setenv('GITHUB_OUTPUT', str(tmp_path / 'outputs'))
    monkeypatch.setenv('SOURCE_SHA', 'a' * 40)
    step = next(s for s in _workflow()['jobs']['build-windows']['steps'] if s['name'] == 'Validate requested release tag')
    exec(compile(step['run'], '<tag-validation>', 'exec'), {})


@pytest.mark.parametrize('tag', ['main', '../v1.0.6', 'v1.0.6\nother', 'v1.0.6;echo x'])
def test_invalid_manual_tag_is_rejected(monkeypatch, tag):
    monkeypatch.setenv('RELEASE_TAG', tag)
    monkeypatch.setenv('SOURCE_SHA', 'a' * 40)
    step = next(s for s in _workflow()['jobs']['build-windows']['steps'] if s['name'] == 'Validate requested release tag')
    with pytest.raises((ValueError, SystemExit)):
        exec(compile(step['run'], '<tag-validation>', 'exec'), {})
