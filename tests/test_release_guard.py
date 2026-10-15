"""The publish workflow refuses to build a release that does not contain main.

0.3.6 to 0.3.8 were cut from a branch that did not contain main, so main's delegation
checks never shipped. These tests read release.yml as text (PyYAML is not a dependency)
and pin the guard in the build job, ahead of the build step.
"""
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "release.yml"


@pytest.fixture(scope="module")
def build_job() -> str:
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("\n  build:")
    end = text.index("\n  publish-to-pypi:")
    return text[start:end]


def test_the_checkout_has_the_full_history(build_job):
    checkout = build_job[build_job.index("actions/checkout"):build_job.index("- name:", build_job.index("actions/checkout"))]
    assert "fetch-depth: 0" in checkout


def test_a_commit_without_main_is_refused_before_the_build(build_job):
    guard = build_job.find("git merge-base --is-ancestor origin/main HEAD")
    assert guard != -1
    assert "git fetch --no-tags origin main" in build_job
    assert guard < build_job.index("uv build")


def test_a_tag_that_does_not_match_the_package_version_is_refused_before_the_build(build_job):
    start = build_job.find("- name: Refuse a tag that does not match the package version")
    assert start != -1
    step = build_job[start:build_job.index("- name:", start + 1)]
    assert "pyproject.toml" in step
    assert '"${GITHUB_REF_NAME#v}" != "$version"' in step
    assert start < build_job.index("uv build")
