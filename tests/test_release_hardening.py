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
