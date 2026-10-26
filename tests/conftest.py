import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import helpers  # noqa: E402


@pytest.fixture(autouse=True)
def _close_test_sockets():
    yield
    helpers.close_all()

pytest_plugins = ["pytester"]
