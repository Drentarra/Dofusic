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


def _publish(monkeypatch, *, status=200, upload_fails=False, tag='v1.0.6'):
    import urllib.request

    calls = []
    monkeypatch.setenv('GH_REPO', 'Drentarra/Dofusic')
    monkeypatch.setenv('GH_TOKEN', 'test-token')
    monkeypatch.setenv('RELEASE_TAG', tag)

    def read_release(request, **kwargs):
        assert request.full_url == f'https://api.github.com/repos/Drentarra/Dofusic/releases/tags/{tag}'
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
    step = next(s for s in _workflow()['jobs']['release-windows']['steps'] if s['name'] == 'Publish GitHub release')
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
    publish = next(s for s in final['steps'] if s['name'] == 'Publish GitHub release')
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


def test_next_release_uses_reviewed_french_notes(monkeypatch):
    code, calls = _publish(monkeypatch, status=404, tag='v1.0.12')
    exec(compile(code, '<publication-step>', 'exec'), {})
    assert len(calls) == 1
    assert '--notes-file' in calls[0]
    assert '.github/RELEASE_NOTES_v1.0.12.md' in calls[0]
    assert '--generate-notes' not in calls[0]
    assert (WORKFLOW.parent.parent / 'RELEASE_NOTES_v1.0.12.md').is_file()


def test_manual_publish_from_main_creates_tag_after_verifying_the_portable():
    workflow = _workflow()
    inputs = workflow['on']['workflow_dispatch']['inputs']
    assert inputs['create_tag_from_main']['default'] == 'false'
    build = workflow['jobs']['build-windows']['steps']
    final = workflow['jobs']['release-windows']['steps']

    validation = next(s for s in build if s['name'] == 'Validate new release request')
    assert "refs/heads/main" in validation['run']
    assert "inputs.create_tag_from_main" in validation['if']

    for job in (build, final):
        checkout = next(s for s in job if s['name'] == 'Checkout sources')
        assert 'inputs.create_tag_from_main && github.sha' in checkout['with']['ref']
        assert "format('refs/tags/{0}', inputs.release_tag)" in checkout['with']['ref']

    names = [step['name'] for step in final]
    tag_step = next(s for s in final if s['name'] == 'Create verified release tag from main')
    assert names.index('Generate and validate installed build environment SBOM') < names.index(tag_step['name'])
    assert names.index('Upload verification artifact') < names.index(tag_step['name'])
    assert names.index(tag_step['name']) < names.index('Publish GitHub release')
    assert "inputs.create_tag_from_main" in tag_step['if']
    assert 'refs/tags/$env:RELEASE_TAG' in tag_step['run']
    assert '$env:VERIFIED_COMMIT' in tag_step['run']


def test_one_time_v114_request_only_triggers_on_its_exact_file():
    workflow = _workflow()
    on_push = workflow['on']['push']
    assert on_push['tags'] == ['v*']
    assert on_push['branches'] == ['main']
    assert on_push['paths'] == ['.github/release-requests/v1.0.14.txt']
    assert (
        WORKFLOW.parent.parent.joinpath('release-requests/v1.0.14.txt')
        .read_text(encoding='utf-8').strip() == 'v1.0.14'
    )

    main_event = "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    for job in workflow['jobs'].values():
        checkout = next(s for s in job['steps'] if s['name'] == 'Checkout sources')
        assert f'({main_event}) && github.sha' in checkout['with']['ref']
        assert "format('refs/tags/{0}', inputs.release_tag)" in checkout['with']['ref']
    release_steps = workflow['jobs']['release-windows']['steps']
    attest = next(s for s in release_steps if s['name'] == 'Attest final public ZIP')
    publish = next(s for s in release_steps if s['name'] == 'Publish GitHub release')
    tag = next(s for s in release_steps if s['name'] == 'Create verified release tag from main')
    assert attest['if'] == publish['if']
    assert main_event in attest['if'] and main_event in tag['if']
    assert "'v1.0.14'" in tag['env']['RELEASE_TAG']
    assert "'v1.0.14'" in publish['env']['RELEASE_TAG']
    assert release_steps.index(tag) < release_steps.index(publish)
    assert release_steps.index(next(s for s in release_steps if s['name'] == 'Upload verification artifact')) < release_steps.index(tag)
    for job in workflow['jobs'].values():
        verification = next(s for s in job['steps'] if s['name'] == 'Verify one-time release request')
        assert verification['if'] == f'({main_event})'
        assert '1.0.14' in verification['run']


def test_one_time_request_does_not_change_manual_release_behavior():
    workflow = _workflow()
    steps = workflow['jobs']['build-windows']['steps']
    manual = next(s for s in steps if s['name'] == 'Validate new release request')
    assert "github.event_name == 'workflow_dispatch' && inputs.create_tag_from_main" in manual['if']
    assert "refs/heads/main" in manual['run']
    assert workflow['on']['workflow_dispatch']['inputs']['create_tag_from_main']['default'] == 'false'
