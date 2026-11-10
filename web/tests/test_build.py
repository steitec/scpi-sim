"""Tests for web/build_site.py: downloads are only used when their checksum matches."""

import gzip
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import build_site as build  # noqa: E402


def test_fetch_checks_sha256(tmp_path):
    source = tmp_path / "file.bin"
    source.write_bytes(b"pyodide")
    good = hashlib.sha256(b"pyodide").hexdigest()
    cache = tmp_path / "cache"
    assert build.fetch(source.as_uri(), good, cache).read_bytes() == b"pyodide"
    with pytest.raises(SystemExit, match="checksum"):
        build.fetch(source.as_uri(), "0" * 64, tmp_path / "other")


def test_fetch_rejects_tampered_cache(tmp_path):
    source = tmp_path / "file.bin"
    source.write_bytes(b"pyodide")
    good = hashlib.sha256(b"pyodide").hexdigest()
    cache = tmp_path / "cache"
    build.fetch(source.as_uri(), good, cache).write_bytes(b"evil")
    with pytest.raises(SystemExit, match="checksum"):
        build.fetch(source.as_uri(), good, cache)


def test_precompress_writes_gzip_next_to_big_files(tmp_path):
    big = tmp_path / "pyodide" / "pyodide.asm.wasm"
    big.parent.mkdir()
    big.write_bytes(b"\0asm" + bytes(range(256)) * 20)
    small = tmp_path / "sim.js"
    small.write_text("export {};\n")
    other = tmp_path / "python_stdlib.zip"
    other.write_bytes(b"PK" * 2000)
    build.precompress(tmp_path)
    assert gzip.decompress((tmp_path / "pyodide" / "pyodide.asm.wasm.gz").read_bytes()) == big.read_bytes()
    assert not (tmp_path / "sim.js.gz").exists()          # below gzip_min_length
    assert not (tmp_path / "python_stdlib.zip.gz").exists()  # already compressed
    # reproducible: no time stamp in the gzip header
    first = (tmp_path / "pyodide" / "pyodide.asm.wasm.gz").read_bytes()
    build.precompress(tmp_path)
    assert (tmp_path / "pyodide" / "pyodide.asm.wasm.gz").read_bytes() == first
