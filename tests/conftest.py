"""Pytest configuration and shared fixtures."""

import pytest
from pyfakefs.fake_filesystem import FakeFilesystem


@pytest.fixture
def fake_fs(fs: FakeFilesystem) -> FakeFilesystem:
    """Provide pyfakefs filesystem fixture.

    Args:
        fs: The fake filesystem provided by pyfakefs.

    Returns:
        The fake filesystem for use in tests.
    """
    return fs
