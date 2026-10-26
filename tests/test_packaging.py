"""What PyPI shows and how a release is made."""

import re
from pathlib import Path

import pytest
import yaml

import scpi_sim

ROOT = Path(__file__).resolve().parent.parent


def test_readme_links_work_outside_github():
    # PyPI renders the README without the repository: relative links break there
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    links = re.findall(r"\]\(([^)#]+)\)", readme)
    assert links
    assert [link for link in links if not link.startswith("https://")] == []


def test_project_urls_and_version():
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert {"Homepage", "Repository", "Changelog", "Issues"} <= set(project["urls"])
    assert project["version"] == scpi_sim.__version__


def test_release_runs_tests_and_checks_tag_before_publishing():
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert "test" in jobs
    assert "test" in jobs["build"]["needs"] and jobs["publish"]["needs"] == "build"
    steps = " ".join(str(step.get("run", "")) for step in jobs["build"]["steps"])
    assert "GITHUB_REF_NAME" in steps and "__version__" in steps
