"""The CI and release workflows pin their actions, build from the lock and fail on findings.

These tests read the workflow and composite action files as text (PyYAML is not a
dependency). A job is the block under `jobs:` from its two-space-indented key to the next one.
"""
import re
from pathlib import Path

import pytest

GITHUB = Path(__file__).resolve().parent.parent / ".github"
WORKFLOWS = GITHUB / "workflows"
YAML_FILES = sorted([*WORKFLOWS.glob("*.yml"), *GITHUB.glob("actions/*/action.yml")])
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+)*$")


def read(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def job(text: str, name: str) -> str:
    start = text.index(f"\n  {name}:")
    following = re.search(r"\n  [\w-]+:", text[start + 1:])
    return text[start:start + 1 + following.start()] if following else text[start:]


def uses_lines():
    for path in YAML_FILES:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            match = re.search(r"uses:\s*(.+)$", line)
            if match and not match.group(1).startswith("./"):
                yield f"{path.relative_to(GITHUB).as_posix()}:{number}", match.group(1).strip()


def test_every_workflow_file_is_found():
    names = {path.name for path in YAML_FILES}
    assert {"release.yml", "2_sonar.yml", "dev.yml", "repo-hygiene.yml", "security.yml"} <= names


@pytest.mark.parametrize("where, ref", list(uses_lines()))
def test_every_third_party_action_is_pinned_to_a_commit_with_its_version(where, ref):
    assert PINNED.match(ref), f"{where}: {ref}"


def test_no_workflow_step_is_allowed_to_fail_quietly():
    for path in YAML_FILES:
        assert "continue-on-error: true" not in path.read_text(encoding="utf-8"), path.name


def test_the_unused_install_action_is_gone():
    assert not (GITHUB / "actions" / "install-dependencies").exists()
    for path in YAML_FILES:
        assert "install-dependencies" not in path.read_text(encoding="utf-8"), path.name


def test_the_release_build_checks_the_lock_before_building():
    build = job(read("release.yml"), "build")
    assert "uv lock --check" in build
    assert build.index("run: uv lock --check") < build.index("run: uv build")


def test_the_publish_job_runs_in_the_pypi_environment():
    publish = job(read("release.yml"), "publish-to-pypi")
    assert re.search(r"\n    environment: pypi\n", publish)
    assert "id-token: write" in publish
    assert "release/v1" not in publish


def test_sonar_installs_from_the_lock_and_a_failing_test_fails_the_job():
    sonar = read("2_sonar.yml")
    assert "pip install" not in sonar
    assert "uv sync --locked --all-groups" in sonar
    assert "uv run pytest" in sonar
    assert sonar.index("uv sync --locked") < sonar.index("uv run pytest")


def test_dev_writes_step_outputs_to_the_output_file():
    dev = read("dev.yml")
    assert "::set-output" not in dev
    assert '>> "$GITHUB_OUTPUT"' in dev


def test_repo_hygiene_runs_on_pull_requests_to_dev_and_main():
    hygiene = read("repo-hygiene.yml")
    assert "branches: [dev, main]" in hygiene
    assert "inception" not in hygiene


@pytest.fixture(scope="module")
def security() -> str:
    path = WORKFLOWS / "security.yml"
    assert path.exists()
    return path.read_text(encoding="utf-8")


def test_security_runs_on_pull_requests_and_pushes_to_dev_and_main_with_read_only_access(security):
    assert "\n  pull_request:\n" in security
    assert "\n  push:\n    branches: [dev, main]\n" in security
    assert "\npermissions:\n  contents: read\n" in security
    assert "write" not in security


def test_security_audits_the_locked_dependencies(security):
    assert "uv audit --frozen" in job(security, "dependency-audit")


def test_security_scans_the_new_commits_with_a_checksummed_gitleaks(security):
    scan = job(security, "secret-scan")
    assert "fetch-depth: 0" in scan
    assert re.search(r"GITLEAKS_SHA256: [0-9a-f]{64}\n", scan)
    assert "sha256sum --check" in scan
    assert scan.index("sha256sum --check") < scan.index("git --log-opts")
    assert "github.event.pull_request.base.sha" in scan
    assert "github.event.before" in scan
    assert "GITLEAKS_LICENSE" not in scan


def test_the_secret_scan_keeps_the_default_rules_and_allows_only_known_fake_values():
    config = (GITHUB.parent / ".gitleaks.toml").read_text(encoding="utf-8")
    assert "useDefault = true" in config
    assert "paths" not in config
    assert "stopwords" not in config
