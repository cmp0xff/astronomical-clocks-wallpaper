# SPDX-License-Identifier: Apache-2.0
"""Shared pytest fixtures and session guards for the host-only test suite."""

from collections.abc import Iterator

import pytest

from device_test_fixtures import install_no_real_sleep_guard


@pytest.fixture(autouse=True, scope="module")
def no_real_sleep() -> Iterator[None]:
    """Enforce that every host test module runs without real wall-clock sleeps."""
    restore = install_no_real_sleep_guard()
    try:
        yield
    finally:
        restore()
