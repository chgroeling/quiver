"""Benchmarks for high-volume extraction scenarios."""

from __future__ import annotations

import itertools
import random
import string
from typing import TYPE_CHECKING

import pytest

from mdbox.archive import MdboxFile

if TYPE_CHECKING:
    from pathlib import Path

    from pytest_benchmark.fixture import BenchmarkFixture


pytest.importorskip("pytest_benchmark")


def _build_random_project(root: Path, files: int, seed: int) -> Path:
    project = root / "project"
    project.mkdir()
    alphabet = string.ascii_letters + string.digits + " "
    rng = random.Random(seed)
    for idx in range(files):
        path = project / f"file_{idx:03d}.txt"
        content = "".join(rng.choice(alphabet) for _ in range(4096))
        path.write_text(content, encoding="utf-8")
    return project


def test_extractall_benchmark(tmp_path: Path, benchmark: BenchmarkFixture) -> None:
    file_count = 100
    project = _build_random_project(tmp_path, files=file_count, seed=2024)
    archive_path = tmp_path / "archive.xml"

    with MdboxFile.open(str(archive_path), mode="w") as qf:
        qf.write(str(project))

    destinations: list[Path] = []
    counter = itertools.count()

    def run_extract() -> None:
        dest = tmp_path / f"extract_{next(counter)}"
        destinations.append(dest)
        with MdboxFile.open(str(archive_path), mode="r") as qf:
            qf.extractall(path=str(dest))

    benchmark(run_extract)

    assert destinations, "benchmark fixture should execute run_extract at least once"
    last_dest = destinations[-1]
    extracted_files = list(last_dest.rglob("*.txt"))
    assert len(extracted_files) == file_count
