#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Tests for the host tooling parity check and runtime import audit."""

import ast
import contextlib
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Final

import check_host_tooling as subject
from device_test_fixtures import install_no_real_sleep_guard

_module_cleanups: list[Callable[[], None]] = []


def setUpModule() -> None:
    """Enforce that host tests run without real wall-clock sleeps."""
    _module_cleanups.append(install_no_real_sleep_guard())


def tearDownModule() -> None:
    """Restore time.sleep after the test module finishes."""
    while _module_cleanups:
        _module_cleanups.pop()()


PINNED_PYPROJECT: Final = """\
[project]
name = "fixture"
version = "0.0.0"

[dependency-groups]
dev = [
  "codespell==2.4.3",
  "ruff==0.16.10",
  "ty==0.0.84",
]
"""

PINNED_PRE_COMMIT: Final = """\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.16.10
    hooks:
      - id: ruff-check
      - id: ruff-format
  - repo: https://github.com/astral-sh/ty-pre-commit
    rev: v0.0.84
    hooks:
      - id: ty
  - repo: https://github.com/codespell-project/codespell
    rev: v2.4.3
    hooks:
      - id: codespell
"""

SWAPPED_PRE_COMMIT: Final = """\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.0.84
    hooks:
      - id: ruff-check
  - repo: https://github.com/astral-sh/ty-pre-commit
    rev: v0.16.10
    hooks:
      - id: ty
  - repo: https://github.com/codespell-project/codespell
    rev: v2.4.3
    hooks:
      - id: codespell
"""

PREFIX_PRE_COMMIT: Final = """\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.16.100
    hooks:
      - id: ruff-check
  - repo: https://github.com/astral-sh/ty-pre-commit
    rev: v0.0.84
    hooks:
      - id: ty
  - repo: https://github.com/codespell-project/codespell
    rev: v2.4.3
    hooks:
      - id: codespell
"""

COMMENT_ONLY_PRE_COMMIT: Final = """\
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    # rev: v0.16.10
    rev: v0.16.9
    hooks:
      - id: ruff-check
  - repo: https://github.com/astral-sh/ty-pre-commit
    rev: v0.0.84
    hooks:
      - id: ty
  - repo: https://github.com/codespell-project/codespell
    rev: v2.4.3
    hooks:
      - id: codespell
"""

REPO_ROOT: Final = Path(__file__).resolve().parents[1]


class PreCommitParserTest(unittest.TestCase):
    """Revision parsing associates each rev with its own repository."""

    def test_revisions_pair_with_their_repositories(self) -> None:
        revisions = subject.parse_pre_commit_revisions(PINNED_PRE_COMMIT)
        self.assertEqual(
            revisions,
            {
                "https://github.com/astral-sh/ruff-pre-commit": "v0.16.10",
                "https://github.com/astral-sh/ty-pre-commit": "v0.0.84",
                "https://github.com/codespell-project/codespell": "v2.4.3",
            },
        )

    def test_full_line_comments_are_ignored(self) -> None:
        config = """\
# Top-level comment mentioning rev: v9.9.9.
repos:
  # The ruff repository.
  - repo: https://example.invalid/tool
    # rev: v9.9.9
    rev: v1.0.0
"""
        self.assertEqual(
            subject.parse_pre_commit_revisions(config),
            {"https://example.invalid/tool": "v1.0.0"},
        )

    def test_inline_comments_are_ignored(self) -> None:
        config = """\
repos:
  - repo: https://example.invalid/tool  # trailing comment
    rev: v1.0.0  # another comment
"""
        self.assertEqual(
            subject.parse_pre_commit_revisions(config),
            {"https://example.invalid/tool": "v1.0.0"},
        )

    def test_quoted_values_preserve_their_hash(self) -> None:
        config = """\
repos:
  - repo: "https://example.invalid/#fragment"
    rev: "v1.0.0"
"""
        self.assertEqual(
            subject.parse_pre_commit_revisions(config),
            {"https://example.invalid/#fragment": "v1.0.0"},
        )

    def test_single_quoted_values_preserve_their_hash(self) -> None:
        config = """\
repos:
  - repo: 'https://example.invalid/#fragment'
    rev: 'v1.0.0'
"""
        self.assertEqual(
            subject.parse_pre_commit_revisions(config),
            {"https://example.invalid/#fragment": "v1.0.0"},
        )

    def test_nested_revision_is_not_the_repository_revision(self) -> None:
        config = """\
repos:
  - repo: https://example.invalid/tool
    hooks:
      - id: custom
        rev: nested
      - id: other
        rev: also-nested
    rev: v1.0.0
"""
        self.assertEqual(
            subject.parse_pre_commit_revisions(config),
            {"https://example.invalid/tool": "v1.0.0"},
        )

    def test_missing_revision_is_rejected(self) -> None:
        config = """\
repos:
  - repo: https://example.invalid/tool
    hooks:
      - id: custom
"""
        with self.assertRaises(ValueError) as raised:
            subject.parse_pre_commit_revisions(config)
        self.assertIn("has no rev", str(raised.exception))

    def test_duplicate_repository_is_rejected(self) -> None:
        config = """\
repos:
  - repo: https://example.invalid/tool
    rev: v1.0.0
  - repo: https://example.invalid/tool
    rev: v2.0.0
"""
        with self.assertRaises(ValueError) as raised:
            subject.parse_pre_commit_revisions(config)
        self.assertIn("more than once", str(raised.exception))

    def test_duplicate_revision_is_rejected(self) -> None:
        config = """\
repos:
  - repo: https://example.invalid/first
    rev: v1.0.0
  - repo: https://example.invalid/second
    rev: v1.0.0
"""
        with self.assertRaises(ValueError) as raised:
            subject.parse_pre_commit_revisions(config)
        self.assertIn("uses rev", str(raised.exception))

    def test_revision_before_repository_is_rejected(self) -> None:
        config = """\
repos:
    rev: v1.0.0
  - repo: https://example.invalid/tool
    rev: v2.0.0
"""
        with self.assertRaises(ValueError) as raised:
            subject.parse_pre_commit_revisions(config)
        self.assertIn("rev before any repo", str(raised.exception))

    def test_local_repository_is_ignored(self) -> None:
        config = """\
repos:
  - repo: local
    hooks:
      - id: custom
  - repo: https://example.invalid/tool
    rev: v1.0.0
"""
        self.assertEqual(
            subject.parse_pre_commit_revisions(config),
            {"https://example.invalid/tool": "v1.0.0"},
        )


class PinParityTest(unittest.TestCase):
    """Only the pinned versions may satisfy the parity check."""

    def test_real_configuration_passes(self) -> None:
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        pre_commit = (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        self.assertEqual(subject.check_pin_parity(pyproject, pre_commit), [])

    def test_swapped_revisions_produce_two_problems(self) -> None:
        problems = subject.check_pin_parity(PINNED_PYPROJECT, SWAPPED_PRE_COMMIT)
        self.assertEqual(len(problems), 2)

    def test_longer_version_with_shared_prefix_fails(self) -> None:
        problems = subject.check_pin_parity(PINNED_PYPROJECT, PREFIX_PRE_COMMIT)
        self.assertEqual(len(problems), 1)
        self.assertIn("v0.16.100", problems[0])

    def test_revision_only_in_comment_fails(self) -> None:
        problems = subject.check_pin_parity(PINNED_PYPROJECT, COMMENT_ONLY_PRE_COMMIT)
        self.assertEqual(len(problems), 1)
        self.assertIn("does not match", problems[0])

    def test_missing_repository_fails(self) -> None:
        config = PINNED_PRE_COMMIT.replace(
            """\
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.16.10
    hooks:
      - id: ruff-check
      - id: ruff-format
""",
            "",
        )
        problems = subject.check_pin_parity(PINNED_PYPROJECT, config)
        self.assertEqual(len(problems), 1)
        self.assertIn("ruff-pre-commit", problems[0])

    def test_missing_pin_fails(self) -> None:
        pyproject = PINNED_PYPROJECT.replace('  "ruff==0.16.10",\n', "")
        problems = subject.check_pin_parity(pyproject, PINNED_PRE_COMMIT)
        self.assertEqual(len(problems), 1)
        self.assertIn("no ruff pin", problems[0])

    def test_malformed_configuration_is_reported_not_raised(self) -> None:
        problems = subject.check_pin_parity(PINNED_PYPROJECT, "repos: [{repo: broken}]\n")
        self.assertEqual(len(problems), 1)
        self.assertIn("invalid .pre-commit-config.yaml", problems[0])


class RuntimeImportAuditTest(unittest.TestCase):
    """The import audit reports third-party imports and parse failures."""

    def _write(self, root: Path, name: str, source: str) -> Path:
        path = root / name
        path.write_text(source, encoding="utf-8")
        return path

    def test_stdlib_imports_pass(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        paths = [
            self._write(root, "alpha.py", "import json\nfrom pathlib import Path\n"),
            self._write(root, "beta.py", "from collections.abc import Sequence\n"),
        ]
        self.assertEqual(subject.check_runtime_imports(paths), [])

    def test_third_party_import_is_reported(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        paths = [self._write(root, "alpha.py", "import numpy\n")]
        problems = subject.check_runtime_imports(paths)
        self.assertEqual(len(problems), 1)
        self.assertIn("numpy", problems[0])

    def test_bare_sibling_import_is_reported(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        paths = [
            self._write(root, "alpha.py", "import beta\n"),
            self._write(root, "beta.py", "from alpha import value\n"),
        ]
        problems = subject.check_runtime_imports(paths)
        self.assertEqual(len(problems), 2)
        self.assertTrue(all("Non-stdlib" in problem for problem in problems))

    def test_relative_imports_pass(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        paths = [self._write(root, "alpha.py", "from . import beta\nfrom .beta import value\n")]
        self.assertEqual(subject.check_runtime_imports(paths), [])

    def test_absolute_host_import_passes(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        paths = [self._write(root, "alpha.py", "from host import contract\nfrom host.device import probes\n")]
        self.assertEqual(subject.check_runtime_imports(paths), [])

    def test_parse_failure_is_reported(self) -> None:
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        paths = [self._write(root, "broken.py", "def broken(:\n")]
        problems = subject.check_runtime_imports(paths)
        self.assertEqual(len(problems), 1)
        self.assertIn("cannot parse runtime harness", problems[0])


class PackageInitializerTest(unittest.TestCase):
    """Package initializers stay docstring-only, so importing one submodule loads no siblings."""

    def test_every_package_initializer_has_no_imports(self) -> None:
        root = Path(__file__).resolve().parent
        initializers = sorted((root / "host").rglob("__init__.py"))
        self.assertIn(root / "host" / "__init__.py", initializers)
        self.assertGreaterEqual(len(initializers), 4)
        for path in initializers:
            with self.subTest(initializer=path.relative_to(root)):
                tree = ast.parse(path.read_text(encoding="utf-8"))
                imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
                self.assertFalse(imports, f"{path.relative_to(root)} must not import")


class TopLevelImportOrderTest(unittest.TestCase):
    """Each package module imports first in a fresh interpreter, whatever order the graph is entered."""

    def test_each_device_module_imports_first(self) -> None:
        root = Path(__file__).resolve().parent.parent
        for module in ("host.device.trace", "host.device.frame", "host.device.probes", "host.battery.cli"):
            with self.subTest(module=module):
                # S603: this runs the same interpreter with a fixed script; the module name is one of
                # the literals above, and no shell is involved.
                result = subprocess.run(  # noqa: S603
                    [sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, 'scripts'); import {module}"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(0, result.returncode, result.stderr)


class RuntimeSourcesTest(unittest.TestCase):
    """The audit covers the four wrappers plus every module in the host package."""

    def test_includes_wrappers_and_host_modules(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        scripts = directory / "scripts"
        (scripts / "host" / "battery").mkdir(parents=True)
        (scripts / "host" / "__init__.py").write_text("", encoding="utf-8")
        (scripts / "host" / "battery" / "probes.py").write_text("from . import stats\n", encoding="utf-8")
        self.enterContext(contextlib.chdir(directory))
        self.assertEqual(
            subject.runtime_sources(),
            [*subject.RUNTIME_SCRIPTS, Path("scripts/host/__init__.py"), Path("scripts/host/battery/probes.py")],
        )


class MainTest(unittest.TestCase):
    """The entry point combines every problem and exits non-zero."""

    def test_main_reports_every_problem(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (directory / "pyproject.toml").write_text(PINNED_PYPROJECT, encoding="utf-8")
        (directory / ".pre-commit-config.yaml").write_text(SWAPPED_PRE_COMMIT, encoding="utf-8")
        self.enterContext(contextlib.chdir(directory))
        with self.assertRaises(SystemExit) as raised:
            subject.main()
        message = str(raised.exception)
        self.assertIn("does not match", message)
        self.assertIn("cannot parse runtime harness", message)
        self.assertGreaterEqual(len(message.splitlines()), 6)

    def test_main_passes_with_a_consistent_tree(self) -> None:
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (directory / "pyproject.toml").write_text(PINNED_PYPROJECT, encoding="utf-8")
        (directory / ".pre-commit-config.yaml").write_text(PINNED_PRE_COMMIT, encoding="utf-8")
        scripts = directory / "scripts"
        (scripts / "host" / "battery").mkdir(parents=True)
        for name in (
            "battery_protocol.py",
            "battery_report.py",
            "device_qualification.py",
            "device_smoke.py",
        ):
            (scripts / name).write_text("import json\n", encoding="utf-8")
        (scripts / "host" / "__init__.py").write_text("", encoding="utf-8")
        (scripts / "host" / "battery" / "probes.py").write_text("from host import contract\n", encoding="utf-8")
        self.enterContext(contextlib.chdir(directory))
        self.assertIsNone(subject.main())
