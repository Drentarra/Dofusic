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
    for job in (build, final):
        checkout = next(s for s in job['steps'] if s.get('uses', '').startswith('actions/checkout@'))
        assert "format('refs/tags/{0}', inputs.release_tag)" in checkout['with']['ref']
    attest = next(s for s in final['steps'] if s['name'] == 'Attest final public ZIP')
    publish = next(s for s in final['steps'] if 'RELEASE_TAG' in s.get('env', {}))
    assert attest['if'] == publish['if']
    assert "github.event_name == 'push' && startsWith(github.ref, 'refs/tags/v')" in publish['if']
    assert "github.event_name == 'workflow_dispatch' && inputs.release_tag != ''" in publish['if']
    assert "inputs.release_tag != ''" in publish['if']
    assert 'inputs.release_tag || github.ref_name' in publish['env']['RELEASE_TAG']


@pytest.mark.parametrize('tag', ['v1.0.6', 'v1.0.7-rc.1'])
def test_valid_manual_tag_is_accepted(monkeypatch, tag):
    monkeypatch.setenv('RELEASE_TAG', tag)
    step = next(s for s in _workflow()['jobs']['build-windows']['steps'] if s['name'] == 'Validate requested release tag')
    exec(compile(step['run'], '<tag-validation>', 'exec'), {})


@pytest.mark.parametrize('tag', ['main', '../v1.0.6', 'v1.0.6\nother', 'v1.0.6;echo x'])
def test_invalid_manual_tag_is_rejected(monkeypatch, tag):
    monkeypatch.setenv('RELEASE_TAG', tag)
    step = next(s for s in _workflow()['jobs']['build-windows']['steps'] if s['name'] == 'Validate requested release tag')
    with pytest.raises((ValueError, SystemExit)):
        exec(compile(step['run'], '<tag-validation>', 'exec'), {})
