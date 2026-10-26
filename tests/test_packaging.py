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
    path = ROOT / ".github/workflows/release.yml"
    if not path.exists():
        pytest.skip("not in the source distribution, only in the repository")
    workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert "test" in jobs
    assert "test" in jobs["build"]["needs"] and jobs["publish"]["needs"] == "build"
    steps = " ".join(str(step.get("run", "")) for step in jobs["build"]["steps"])
    assert "GITHUB_REF_NAME" in steps and "__version__" in steps


def test_classifiers():
    tomllib = pytest.importorskip("tomllib")
    classifiers = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["classifiers"]
    for version in ("3.10", "3.11", "3.12", "3.13"):
        assert f"Programming Language :: Python :: {version}" in classifiers
    assert any(c.startswith("Development Status ::") for c in classifiers)
    assert "Framework :: Pytest" in classifiers


def test_sdist_can_run_its_own_tests(tmp_path):
    import subprocess
    import sys
    import tarfile
    pytest.importorskip("build")
    subprocess.run([sys.executable, "-m", "build", "--sdist", "--outdir", str(tmp_path), str(ROOT)],
                   check=True, capture_output=True, timeout=300)
    (sdist,) = tmp_path.glob("*.tar.gz")
    with tarfile.open(sdist) as tar:
        names = {name.split("/", 1)[1] for name in tar.getnames() if "/" in name}
    for needed in ("tests/helpers.py", "tests/conftest.py", "CHANGELOG.md", "LICENSE",
                   "examples/bench.yaml", "examples/test_example.py"):
        assert needed in names, needed


def test_dockerfile_entrypoint():
    path = ROOT / "Dockerfile"
    if not path.exists() and not (ROOT / ".git").exists():
        pytest.skip("not in the source distribution")
    text = path.read_text(encoding="utf-8")
    assert 'ENTRYPOINT ["scpi-sim"]' in text
    assert "USER " in text  # not root


def test_release_publishes_docker_image_after_tests():
    path = ROOT / ".github/workflows/release.yml"
    if not path.exists():
        pytest.skip("not in the source distribution, only in the repository")
    job = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]["docker"]
    assert "test" in job["needs"]
    assert job["permissions"]["packages"] == "write"
    text = path.read_text(encoding="utf-8")
    assert "ghcr.io/steitec/scpi-sim" in text and ":latest" in text


def test_ci_builds_and_smoke_tests_docker_image():
    path = ROOT / ".github/workflows/ci.yml"
    if not path.exists():
        pytest.skip("not in the source distribution, only in the repository")
    steps = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]["docker"]["steps"]
    runs = " ".join(str(step.get("run", "")) for step in steps)
    assert "docker build" in runs and "*IDN?" in runs
