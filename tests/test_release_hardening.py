from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_release_documentation_and_update_checks_are_present():
    """Protect release documentation from regressions in security guidance."""
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    security_policy = REPOSITORY_ROOT / "SECURITY.md"
    dependabot = (REPOSITORY_ROOT / ".github" / "dependabot.yml").read_text(
        encoding="utf-8"
    )

    assert "CONVERTIR_EN_OPUS_64K.bat" not in readme
    assert security_policy.is_file()
    assert 'package-ecosystem: "pip"' in dependabot
    assert 'directory: "/Data"' in dependabot
    assert 'package-ecosystem: "github-actions"' in dependabot
    assert 'directory: "/"' in dependabot


def test_portable_build_dependency_lock_is_complete_and_avoids_opencv_conflict():
    lock = REPOSITORY_ROOT / 'Data' / 'requirements-lock.txt'

    assert lock.is_file()
    entries = [
        line.strip()
        for line in lock.read_text(encoding='utf-8').splitlines()
        if line.strip() and not line.lstrip().startswith('#')
    ]
    assert entries
    assert all('==' in entry for entry in entries)
    assert any(entry.lower().startswith('opencv-python-headless==') for entry in entries)
    assert not any(entry.lower().startswith('opencv-python==') for entry in entries)
    assert not any(entry.lower().startswith('rapidocr==') for entry in entries)


def test_music_pack_metadata_pins_the_independent_release():
    import json
    import re

    path = REPOSITORY_ROOT / 'Data' / 'music-pack.json'
    assert path.is_file(), 'music pack metadata is missing'
    metadata = json.loads(path.read_text(encoding='utf-8'))
    assert metadata['tag'] == 'music-v1'
    assert metadata['asset'] == 'Dofusic-Musiques-v1.zip'
    assert re.fullmatch(r'[0-9a-f]{64}', metadata['sha256'])


def _music_pack_module():
    import importlib.util

    path = REPOSITORY_ROOT / '.github' / 'scripts' / 'music_pack.py'
    assert path.is_file(), 'music pack generator is missing'
    spec = importlib.util.spec_from_file_location('music_pack', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _music_source(tmp_path, entries):
    import zipfile
    import warnings

    source = tmp_path / 'source.zip'
    with zipfile.ZipFile(source, 'w') as archive:
        for name, content in entries:
            item = zipfile.ZipInfo(name)
            item.filename = name  # Preserve malformed paths instead of Windows normalization.
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='Duplicate name:', category=UserWarning)
                archive.writestr(item, content)
    return source


def test_music_pack_preserves_only_music_bytes_in_sorted_root_and_is_reproducible(tmp_path, monkeypatch):
    import hashlib
    import zipfile

    module = _music_pack_module()
    source = _music_source(tmp_path, [
        ('Dofusic/Data/secret.txt', b'not music'),
        ('Dofusic/Musiques/z.opus', b'last track'),
        ('Dofusic/Musiques/a/first.opus', b'first track'),
        ('Dofusic/Musiques/', b''),
    ])
    monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source.read_bytes()).hexdigest())
    first = tmp_path / 'Dofusic-Musiques-v1.zip'
    second = tmp_path / 'second.zip'
    result = module.build_pack(source, first)
    module.build_pack(source, second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ['Musiques/a/first.opus', 'Musiques/z.opus']
        assert archive.read('Musiques/a/first.opus') == b'first track'
        assert archive.read('Musiques/z.opus') == b'last track'
        assert all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist())
    actual = hashlib.sha256(first.read_bytes()).hexdigest()
    assert result == {'sha256': actual, 'file_count': 2, 'uncompressed_bytes': 21}
    assert first.with_suffix('.zip.sha256').read_text(encoding='ascii') == f'{actual}  Dofusic-Musiques-v1.zip\n'


def test_music_pack_rejects_wrong_source_sha_before_writing(tmp_path):
    import pytest

    module = _music_pack_module()
    source = _music_source(tmp_path, [('Dofusic/Musiques/track.opus', b'music')])
    output = tmp_path / 'pack.zip'
    with pytest.raises(ValueError, match='SHA256'):
        module.build_pack(source, output)
    assert not output.exists()
    assert not output.with_suffix('.zip.sha256').exists()


def test_music_pack_rejects_missing_source_before_writing(tmp_path):
    import pytest

    module = _music_pack_module()
    output = tmp_path / 'pack.zip'
    with pytest.raises(FileNotFoundError):
        module.build_pack(tmp_path / 'missing.zip', output)
    assert not output.exists()


def test_music_pack_rejects_empty_selection_before_writing(tmp_path, monkeypatch):
    import hashlib
    import pytest

    module = _music_pack_module()
    source = _music_source(tmp_path, [('Dofusic/Data/app.py', b'app')])
    monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source.read_bytes()).hexdigest())
    output = tmp_path / 'pack.zip'
    with pytest.raises(ValueError, match='empty'):
        module.build_pack(source, output)
    assert not output.exists()


def test_music_pack_rejects_unsafe_paths_and_collisions_before_writing(tmp_path, monkeypatch):
    import hashlib
    import pytest

    module = _music_pack_module()
    unsafe_entries = [
        [('Dofusic/Musiques/../outside.opus', b'bad')],
        [('Dofusic/Musiques/a\\outside.opus', b'bad')],
        [('Dofusic/Musiques/C:/outside.opus', b'bad')],
        [('Dofusic/Musiques//outside.opus', b'bad')],
        [('Dofusic/Musiques/CON.opus', b'bad')],
        [('Dofusic/Musiques/a', b'file'), ('Dofusic/Musiques/a/track.opus', b'track')],
        [('Dofusic/Musiques/track.opus', b'1'), ('Dofusic/Musiques/TRACK.opus', b'2')],
    ]
    for entries in unsafe_entries:
        source = _music_source(tmp_path, entries)
        monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source.read_bytes()).hexdigest())
        output = tmp_path / 'pack.zip'
        try:
            module.build_pack(source, output)
        except ValueError as error:
            assert 'path' in str(error) or 'collision' in str(error)
        else:
            pytest.fail(f'Unsafe entries accepted: {entries!r}')
        assert not output.exists()


def test_music_pack_never_overwrites_existing_pack_or_checksum(tmp_path, monkeypatch):
    import hashlib
    import pytest

    module = _music_pack_module()
    source = _music_source(tmp_path, [('Dofusic/Musiques/track.opus', b'music')])
    monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(source.read_bytes()).hexdigest())
    output = tmp_path / 'pack.zip'
    checksum = output.with_suffix('.zip.sha256')
    checksum.write_bytes(b'existing checksum')
    with pytest.raises(FileExistsError):
        module.build_pack(source, output)
    assert not output.exists()
    assert checksum.read_bytes() == b'existing checksum'
    checksum.unlink()
    output.write_bytes(b'existing pack')
    with pytest.raises(FileExistsError):
        module.build_pack(source, output)
    assert output.read_bytes() == b'existing pack'


def _ci_workflow():
    import yaml

    path = REPOSITORY_ROOT / '.github' / 'workflows' / 'ci.yml'
    assert path.is_file(), 'read-only CI workflow is missing'
    # BaseLoader keeps GitHub's "on" key a string instead of YAML 1.1 boolean.
    return yaml.load(path.read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


def test_ci_runs_on_main_hardening_branch_and_pull_requests_with_read_only_permissions():
    workflow = _ci_workflow()
    assert set(workflow['on']) == {'push', 'pull_request'}
    assert set(workflow['on']['push']['branches']) == {'main', 'release-hardening-v1.0.2'}
    assert workflow['permissions'] == {}
    assert workflow['jobs']
    for job in workflow['jobs'].values():
        assert job['permissions'] == {'contents': 'read'}
        assert 'uses' not in job, 'CI must not delegate to an unreviewed reusable workflow'


def test_ci_pins_windows_python_and_checks_dependencies_probes_and_portable_build():
    workflow = _ci_workflow()
    jobs = list(workflow['jobs'].values())
    assert len(jobs) == 1
    job = jobs[0]
    assert job['runs-on'] == 'windows-2025'
    steps = job['steps']
    actions = [step for step in steps if 'uses' in step]
    assert [step['uses'] for step in actions] == [
        'actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1',
        'actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97',
    ]
    assert actions[0]['with']['persist-credentials'] == 'false'
    assert actions[1]['with']['python-version'] == '3.11.9'
    assert actions[1]['with']['architecture'] == 'x64'
    run_steps = [step for step in steps if 'run' in step]
    scripts = [step['run'] for step in run_steps]
    for step in run_steps:
        assert step.get('shell', job.get('defaults', {}).get('run', {}).get('shell')) == 'pwsh'
        assert '$ErrorActionPreference = "Stop"' in step['run']
        assert '$PSNativeCommandUseErrorActionPreference = $true' in step['run']
        assert '${{' not in step['run'], 'GitHub inputs must not be interpolated into scripts'
    joined = '\n'.join(scripts)
    assert 'python -m pip install -r Data/requirements-lock.txt' in joined
    assert 'python -m pip install --no-deps rapidocr==3.9.2' in joined
    assert "assert 'opencv-python' not in d" in joined
    assert "assert 'opencv-python-headless' in d" in joined
    required_order = [
        'python -m pytest -q',
        'prepare_ocr_models(root /',
        'prepare_quickjs_runtime(root)',
        'DOFUSIC_QJS_BINARY=',
        'python Data/tools/runtime_probe.py',
        'python Data/tools/prepare_models.py',
        'python Data/tools/build_portable.py --root .',
    ]
    positions = [joined.index(command) for command in required_order]
    assert positions == sorted(positions)
    assert 'Path.cwd().resolve()' in joined
    assert 'GITHUB_ENV' in joined


def test_ci_has_no_secrets_music_downloads_artifact_uploads_or_publishing():
    import json

    workflow = _ci_workflow()
    serialized = json.dumps(workflow).lower()
    for forbidden in (
        'secrets.', 'contents: write', 'upload-artifact', 'download-artifact',
        'music_pack', 'music-pack', 'gh release', 'softprops/', 'git push',
        'invoke-webrequest', 'curl ', 'wget ',
    ):
        assert forbidden not in serialized
    for job in workflow['jobs'].values():
        assert set(job['permissions'].values()) <= {'read', 'none'}
        assert 'environment' not in job


def _release_workflow():
    import yaml
    return yaml.load((REPOSITORY_ROOT / '.github/workflows/build-release.yml').read_text(encoding='utf-8'), Loader=yaml.BaseLoader)


def test_release_actions_permissions_triggers_and_artifacts_are_pinned():
    workflow = _release_workflow()
    assert workflow['permissions'] == {}
    assert set(workflow['on']) == {'push', 'workflow_dispatch'}
    assert workflow['on']['push']['tags'] == ['v*']
    job = workflow['jobs']['build-windows']
    assert job['runs-on'] == 'windows-2025'
    assert job['permissions'] == {'contents': 'write', 'id-token': 'write', 'attestations': 'write', 'actions': 'read'}
    actions = {step['uses'].split('@')[0]: step for step in job['steps'] if 'uses' in step}
    for name, pin in {
        'actions/checkout': '3d3c42e5aac5ba805825da76410c181273ba90b1',
        'actions/setup-python': '5fda3b95a4ea91299a34e894583c3862153e4b97',
        'actions/upload-artifact': '043fb46d1a93c77aae656e7c1c64a875d1fc6a0a',
        'actions/attest': '1e69f48acb82d1966a394da916b4c1698aa569d6',
    }.items():
        assert actions[name]['uses'] == f'{name}@{pin}'
    assert actions['actions/checkout']['with']['persist-credentials'] == 'false'
    assert actions['actions/setup-python']['with']['python-version'] == '3.11.9'
    assert actions['actions/setup-python']['with']['architecture'] == 'x64'
    expected = {'Release/Dofusic.zip', 'Release/Dofusic.zip.sha256', 'Release/Dofusic.sbom.json', 'Release/BUILD_SIZE_REPORT.txt'}
    assert set(actions['actions/upload-artifact']['with']['path'].splitlines()) == expected
    attest = actions['actions/attest']
    assert attest['if'] == "startsWith(github.ref, 'refs/tags/v')"
    assert attest['with']['subject-path'] == 'Release/Dofusic.zip'
    assert attest['with']['create-storage-record'] == 'false'


def test_release_scripts_fail_fast_and_gate_publication_on_verified_final_assets():
    steps = _release_workflow()['jobs']['build-windows']['steps']
    scripts = [step['run'] for step in steps if 'run' in step]
    for step in steps:
        if 'run' in step:
            assert step['shell'] == 'pwsh'
            assert '$ErrorActionPreference = "Stop"' in step['run']
            assert '$PSNativeCommandUseErrorActionPreference = $true' in step['run']
            assert '${{' not in step['run']
    joined = '\n'.join(scripts)
    for command in ('Data/music-pack.json', 'gh release download $metadata.tag', '--pattern $metadata.asset', 'verify_release.py music', 'python -m pip install -r Data/requirements-lock.txt', 'python -m pip install --no-deps rapidocr==3.9.2', "assert 'opencv-python' not in d", "assert 'opencv-python-headless' in d"):
        assert command in joined
    assert 'v1.0.0' not in joined
    assert 'Expand-Archive' not in joined
    assert joined.index('python -m pytest -q') < joined.index('python Data/tools/build_portable.py --root .')
    sbom = next(step for step in steps if 'cyclonedx_py environment' in step.get('run', ''))['run']
    assert sbom.index('$buildPython =') < sbom.index('python -m venv .sbom-venv')
    for command in ('sys.executable', 'cyclonedx-bom==7.3.1', 'cyclonedx_py environment $buildPython', '--output-reproducible --spec-version 1.6 --output-format JSON', 'verify_release.py sbom'):
        assert command in sbom
    final_index = next(i for i, step in enumerate(steps) if 'verify_release.py final' in step.get('run', ''))
    attest_index = next(i for i, step in enumerate(steps) if step.get('uses', '').startswith('actions/attest@'))
    publish_index = next(i for i, step in enumerate(steps) if 'gh release create' in step.get('run', ''))
    assert final_index < attest_index < publish_index
    publish = steps[publish_index]
    assert publish['if'] == "startsWith(github.ref, 'refs/tags/v')"
    assert publish['env']['RELEASE_TAG'] == '${{ github.ref_name }}'
    assert publish['env']['GH_REPO'] == '${{ github.repository }}'
    assert '--verify-tag' in publish['run']
    assert '--clobber' not in publish['run']
    for asset in ('Dofusic.zip', 'Dofusic.zip.sha256', 'Dofusic.sbom.json', 'BUILD_SIZE_REPORT.txt'):
        assert 'Release/' + asset in publish['run'].replace('\\', '/')


def _release_verifier():
    import importlib.util
    path = REPOSITORY_ROOT / '.github/scripts/verify_release.py'
    assert path.is_file(), 'safe release verifier is missing'
    spec = importlib.util.spec_from_file_location('verify_release', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verified_music_extracts_only_after_hash_and_archive_validation(tmp_path):
    import hashlib
    verifier = _release_verifier()
    source = _music_source(tmp_path, [('Musiques/track.opus', b'music')])
    destination = tmp_path / 'extract'
    verifier.extract_music(source, hashlib.sha256(source.read_bytes()).hexdigest(), destination)
    assert (destination / 'Musiques/track.opus').read_bytes() == b'music'
    assert sorted(path.name for path in destination.iterdir()) == ['Musiques']


def test_verified_music_rejects_wrong_hash_before_any_extraction(tmp_path):
    import pytest
    verifier = _release_verifier()
    source = _music_source(tmp_path, [('Musiques/track.opus', b'music')])
    destination = tmp_path / 'extract'
    with pytest.raises(ValueError, match='SHA256'):
        verifier.extract_music(source, '0' * 64, destination)
    assert not destination.exists()


def test_verified_music_rejects_empty_unexpected_or_unsafe_zip_before_any_extraction(tmp_path):
    import hashlib
    import pytest
    verifier = _release_verifier()
    fixtures = [[], [('Musiques/empty.opus', b'')], [('Dofusic/Musiques/track.opus', b'music')], [('Musiques/track.opus', b'music'), ('Data/extra', b'extra')], [('Musiques/../outside.opus', b'music')], [('Musiques/a\\outside.opus', b'music')], [('Musiques/NUL.opus', b'music')], [('Musiques/track.opus.', b'music')], [('Musiques/track.opus', b'1'), ('Musiques/TRACK.opus', b'2')], [('Musiques/A/one.opus', b'1'), ('Musiques/a/two.opus', b'2')], [('Musiques/a', b'1'), ('Musiques/a/two.opus', b'2')], [('Musiques/track.opus', b'1'), ('Musiques/track.opus', b'2')]]
    for entries in fixtures:
        source = _music_source(tmp_path, entries)
        destination = tmp_path / 'extract'
        with pytest.raises(ValueError):
            verifier.extract_music(source, hashlib.sha256(source.read_bytes()).hexdigest(), destination)
        assert not destination.exists()


def test_final_release_validates_layout_and_writes_digest_for_exact_bytes(tmp_path):
    import hashlib
    verifier = _release_verifier()
    source = _music_source(tmp_path, [('Dofusic/Dofusic.exe', b'executable'), ('Dofusic/Data/runtime.bin', b'data'), ('Dofusic/Musiques/track.opus', b'music')])
    checksum = tmp_path / 'Dofusic.zip.sha256'
    verifier.finalize_release(source, checksum)
    assert checksum.read_text(encoding='ascii') == f'{hashlib.sha256(source.read_bytes()).hexdigest()}  {source.name}\n'


def test_final_release_rejects_incomplete_or_unsafe_layout_before_checksum(tmp_path):
    import pytest
    verifier = _release_verifier()
    valid = [('Dofusic/Dofusic.exe', b'exe'), ('Dofusic/Data/a', b'data'), ('Dofusic/Musiques/a', b'music')]
    fixtures = [valid[1:], valid[:2], [valid[0], valid[2]], [('Dofusic/Dofusic.exe', b''), *valid[1:]], [*valid, ('Dofusic/extra.txt', b'extra')], [*valid, ('Other/extra.txt', b'extra')], [*valid, ('Dofusic/Data/../bad', b'extra')], [*valid, ('Dofusic/data/B', b'extra')]]
    for entries in fixtures:
        source = _music_source(tmp_path, entries)
        checksum = tmp_path / 'Dofusic.zip.sha256'
        with pytest.raises(ValueError):
            verifier.finalize_release(source, checksum)
        assert not checksum.exists()


def test_sbom_validation_requires_schema_actual_rapidocr_and_all_lock_versions(tmp_path):
    import json
    import pytest
    verifier = _release_verifier()
    lock = tmp_path / 'lock.txt'
    lock.write_text('numpy==2.4.6\nopencv-python-headless==4.14.0.94\n', encoding='utf-8')
    sbom = tmp_path / 'sbom.json'
    valid = {'bomFormat': 'CycloneDX', 'specVersion': '1.6', 'components': [{'name': 'numpy', 'version': '2.4.6'}, {'name': 'opencv-python-headless', 'version': '4.14.0.94'}, {'name': 'RapidOCR', 'version': '3.9.2'}]}
    sbom.write_text(json.dumps(valid), encoding='utf-8')
    verifier.validate_sbom(sbom, lock)
    for document in [{**valid, 'specVersion': '1.5'}, {**valid, 'components': valid['components'][:2]}, {**valid, 'components': [{**valid['components'][0], 'version': '0'}, *valid['components'][1:]]}, {**valid, 'components': [*valid['components'], {'name': 'opencv-python', 'version': '4'}]}]:
        sbom.write_text(json.dumps(document), encoding='utf-8')
        with pytest.raises(ValueError):
            verifier.validate_sbom(sbom, lock)


def test_verified_music_rejects_windows_extended_device_aliases_before_extraction(tmp_path):
    import hashlib
    import pytest
    verifier = _release_verifier()
    for name in ('COM\u00b9.opus', 'LPT\u00b2.opus', 'CON .opus'):
        source = _music_source(tmp_path, [('Musiques/' + name, b'music')])
        destination = tmp_path / 'extract'
        with pytest.raises(ValueError):
            verifier.extract_music(source, hashlib.sha256(source.read_bytes()).hexdigest(), destination)
        assert not destination.exists()



def test_signpath_transport_is_tag_only_and_final_artifacts_follow_validation():
    steps = _release_workflow()['jobs']['build-windows']['steps']
    config = next(step for step in steps if step.get('id') == 'signpath-config')
    upload = next(step for step in steps if step.get('id') == 'signpath-upload')
    signing = next(step for step in steps if step.get('uses', '').startswith('signpath/'))
    validate = next(step for step in steps if 'verify_release.py signed' in step.get('run', ''))
    tag_only = "startsWith(github.ref, 'refs/tags/v')"
    assert config['if'] == tag_only
    enabled_only = tag_only + " && steps.signpath-config.outputs.enabled == 'true'"
    for step in (upload, signing, validate):
        assert step['if'] == enabled_only
        assert step.get('continue-on-error', 'false') == 'false'
    assert upload['uses'] == 'actions/upload-artifact@043fb46d1a93c77aae656e7c1c64a875d1fc6a0a'
    assert upload['with']['path'] == 'Release/Dofusic.zip'
    assert upload['with']['archive'] == 'false'
    assert upload['with']['if-no-files-found'] == 'error'
    assert signing['uses'] == 'signpath/github-action-submit-signing-request@c92b958760219087e01f8d67a1669ed57afe2627'
    assert signing['with']['github-artifact-id'] == '${{ steps.signpath-upload.outputs.artifact-id }}'
    assert signing['with']['wait-for-completion'] == 'true'
    assert signing['with']['skip-decompress'] == 'true'
    assert signing['with']['output-artifact-directory'] == '${{ steps.signpath-config.outputs.signed-directory }}'
    assert signing['with']['github-token'] == '${{ github.token }}'
    for step in steps:
        if 'secrets.SIGNPATH' in str(step):
            assert step['if'] in (tag_only, enabled_only)
    script = validate['run']
    assert 'Get-AuthenticodeSignature' in script
    assert "$signature.Status -ne 'Valid'" in script
    assert script.index('verify_release.py signed') < script.index('Get-AuthenticodeSignature') < script.index('Copy-Item')
    assert '-PathType Leaf' in script
    assert 'Expand-Archive' not in script
    validation_index = steps.index(validate)
    final_index = next(i for i, step in enumerate(steps) if 'verify_release.py final' in step.get('run', ''))
    sbom_index = next(i for i, step in enumerate(steps) if 'cyclonedx_py environment' in step.get('run', ''))
    attest_index = next(i for i, step in enumerate(steps) if step.get('uses', '').startswith('actions/attest@'))
    publish_index = next(i for i, step in enumerate(steps) if 'gh release create' in step.get('run', ''))
    assert validation_index < final_index < sbom_index < attest_index < publish_index


def test_signpath_tag_configuration_fails_closed_using_synthetic_values(tmp_path):
    import os
    import subprocess
    import shutil
    config = next(step for step in _release_workflow()['jobs']['build-windows']['steps'] if step.get('id') == 'signpath-config')
    script = tmp_path / 'config.ps1'
    script.write_text(config['run'], encoding='utf-8')
    keys = ('SIGNPATH_API_TOKEN', 'SIGNPATH_ORGANIZATION_ID', 'SIGNPATH_PROJECT_SLUG', 'SIGNPATH_SIGNING_POLICY_SLUG', 'SIGNPATH_ARTIFACT_CONFIGURATION_SLUG')
    complete = {key: 'synthetic-test-value' for key in keys}
    fixtures = [({}, '', 0, 'false'), ({}, 'false', 0, 'false'), ({}, 'true', 1, None), ({}, 'invalid', 1, None), ({}, 'TRUE', 1, None), ({}, ' false ', 1, None), (complete, 'false', 0, 'true'), (complete, 'true', 0, 'true'), (complete, '', 0, 'true')]
    fixtures += [({key: value for key, value in complete.items() if key != missing}, 'false', 1, None) for missing in keys]
    fixtures += [({'SIGNPATH_API_TOKEN': ' '}, 'false', 1, None)]
    for index, (values, required, failed, enabled) in enumerate(fixtures):
        output = tmp_path / f'outputs-{index}'
        runner_temp = tmp_path / f'runner-{index}'
        runner_temp.mkdir()
        # Explicit allowlist: never inherit or inspect any SignPath environment values.
        env = {key: os.environ[key] for key in ('PATH', 'SystemRoot', 'TEMP', 'TMP') if key in os.environ}
        env.update({key: '' for key in keys})
        env.update(values)
        env.update(SIGNPATH_REQUIRED=required, GITHUB_OUTPUT=str(output), RUNNER_TEMP=str(runner_temp))
        result = subprocess.run([shutil.which('pwsh') or 'powershell', '-NoProfile', '-NonInteractive', '-File', str(script)], env=env, capture_output=True, text=True)
        assert bool(result.returncode) == bool(failed), (required, sorted(values), result.stdout, result.stderr)
        if enabled is not None:
            assert f'enabled={enabled}' in output.read_text(encoding='utf-8-sig')
            if enabled == 'true':
                lines = output.read_text(encoding='utf-8-sig').splitlines()
                directory = Path(next(line.split('=', 1)[1] for line in lines if line.startswith('signed-directory=')))
                assert directory.is_dir() and not list(directory.iterdir())
        else:
            assert not output.exists()


def _signed_zip(tmp_path, filename, entries):
    source = _music_source(tmp_path, entries)
    target = tmp_path / filename
    source.replace(target)
    return target


def test_signed_archive_allows_changed_executable_preserves_data_music_and_candidate(tmp_path):
    verifier = _release_verifier()
    unchanged = [('Dofusic/Data/runtime.bin', b'data'), ('Dofusic/Musiques/track.opus', b'music')]
    candidate = _signed_zip(tmp_path, 'unsigned.zip', [('Dofusic/Dofusic.exe', b'unsigned'), *unchanged])
    signed = _signed_zip(tmp_path, 'signed.zip', [('Dofusic/Dofusic.exe', b'signed executable'), *reversed(unchanged)])
    original = candidate.read_bytes()
    extract = tmp_path / 'extract'
    verifier.validate_signed_release(candidate, signed, extract)
    assert candidate.read_bytes() == original
    assert (extract / 'Dofusic/Dofusic.exe').read_bytes() == b'signed executable'
    assert sorted(path.relative_to(extract).as_posix() for path in extract.rglob('*') if path.is_file()) == ['Dofusic/Dofusic.exe']


def test_signed_archive_rejects_changed_removed_added_or_unsafe_content_before_extraction(tmp_path):
    import pytest
    verifier = _release_verifier()
    valid = [('Dofusic/Dofusic.exe', b'exe'), ('Dofusic/Data/a', b'data'), ('Dofusic/Musiques/a', b'music')]
    candidate = _signed_zip(tmp_path, 'unsigned.zip', valid)
    fixtures = [valid[1:], valid[:2], [valid[0], valid[2]], [(valid[0][0], b''), *valid[1:]], [valid[0], (valid[1][0], b'changed'), valid[2]], [*valid[:2], (valid[2][0], b'changed')], [*valid, ('Dofusic/Data/added', b'new')], [valid[0], ('Dofusic/Data/renamed', b'data'), valid[2]], [*valid, ('Other/file', b'bad')], [*valid, ('Dofusic/Data/../outside', b'bad')], [*valid, ('Dofusic/Data/a\\outside', b'bad')], [*valid, ('Dofusic/Data/A', b'bad')], [*valid, valid[1]], [*valid, ('Dofusic/Data/a/child', b'bad')]]
    fixtures += [[*valid, ('Dofusic/Data/' + prefix + digit + '.bin', b'bad')] for prefix in ('COM', 'LPT') for digit in ('\u00b9', '\u00b2', '\u00b3')]
    original = candidate.read_bytes()
    for index, entries in enumerate(fixtures):
        signed = _signed_zip(tmp_path, f'signed-{index}.zip', entries)
        extract = tmp_path / f'extract-{index}'
        with pytest.raises(ValueError):
            verifier.validate_signed_release(candidate, signed, extract)
        assert not extract.exists()
        assert candidate.read_bytes() == original


def test_signed_archive_rejects_missing_corrupt_nonregular_and_existing_destination(tmp_path):
    import pytest
    import stat
    import zipfile
    verifier = _release_verifier()
    valid = [('Dofusic/Dofusic.exe', b'exe'), ('Dofusic/Data/a', b'data'), ('Dofusic/Musiques/a', b'music')]
    candidate = _signed_zip(tmp_path, 'unsigned.zip', valid)
    extract = tmp_path / 'extract'
    with pytest.raises(FileNotFoundError):
        verifier.validate_signed_release(candidate, tmp_path / 'missing.zip', extract)
    signed = tmp_path / 'signed.zip'
    signed.write_bytes(b'not a zip')
    with pytest.raises(zipfile.BadZipFile):
        verifier.validate_signed_release(candidate, signed, extract)
    with zipfile.ZipFile(signed, 'w') as archive:
        for name, content in valid:
            item = zipfile.ZipInfo(name)
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, content)
    with pytest.raises(ValueError):
        verifier.validate_signed_release(candidate, signed, extract)
    assert not extract.exists()
    signed = _signed_zip(tmp_path, 'signed.zip', valid)
    extract.mkdir()
    sentinel = extract / 'sentinel'
    sentinel.write_bytes(b'existing')
    with pytest.raises(FileExistsError):
        verifier.validate_signed_release(candidate, signed, extract)
    assert sentinel.read_bytes() == b'existing'
