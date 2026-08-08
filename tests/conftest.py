"""Shared pytest configuration for the radar-vitals test suite.

Defines the --run-dir CLI option and the run_dir fixture used by integration
tests in test_exp004_analysis.py. Integration tests are skipped by default;
supply --run-dir <path> or set EXP004_RUN_DIR to activate them.

Also defines the shared M8 test-attestation fixtures.  See the block at the end of
this file for why a fake command runner is mandatory there.
"""
import hashlib
import os
from pathlib import Path
from xml.etree import ElementTree

# Set non-interactive backend before any test module (or the modules they import)
# can import matplotlib.pyplot.  Prevents GUI/font crashes in headless environments.
import matplotlib
matplotlib.use("Agg")

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-dir",
        action="store",
        default=None,
        help="Path to a completed exp004 results directory for integration tests.",
    )


def pytest_configure(config):
    """Register the two marker classes whose skips the M8 test attestation may record.

    ``real_data`` marks a node that reads a frozen real capture or reference file.  Those
    are opt-in (see ``pytest_collection_modifyitems``) because the synthetic gate must not
    depend on real capture bytes.

    ``optional_artifact_or_mode`` marks a node whose subject is an OPTIONAL input: an
    artifact under the gitignored ``results/`` tree, or an optional configuration mode.
    Such a node executes on a machine where that input happens to exist and skips where it
    does not, so "how many nodes skipped" is a property of the operator's filesystem.  The
    attestation must still be reproducible, so the permitted-skip set is derived from these
    markers — never from a count, and never from what is on disk.  Every other skip,
    failure, or error refuses to produce a gate.
    """
    config.addinivalue_line(
        "markers",
        "real_data: test that reads frozen real capture/reference inputs; skipped by default",
    )
    config.addinivalue_line(
        "markers",
        "optional_artifact_or_mode: test whose subject is an optional on-disk artifact or "
        "an optional configuration mode; a declared, recordable skip on a clean clone",
    )


def pytest_collection_modifyitems(config, items):
    """Keep real-data access opt-in, including during the M8 test attestation."""
    if os.environ.get("M8_RUN_REAL_DATA_TESTS") == "1":
        return
    skip_real_data = pytest.mark.skip(
        reason="set M8_RUN_REAL_DATA_TESTS=1 to authorize frozen real-data checks"
    )
    for item in items:
        if "real_data" in item.keywords:
            item.add_marker(skip_real_data)


@pytest.fixture
def run_dir(request):
    d = request.config.getoption("--run-dir") or os.environ.get("EXP004_RUN_DIR")
    if d is None:
        pytest.skip("--run-dir not provided")
    return Path(d)


# ─────────────────────────────────────────────────────────────────────────────
# M8 strict test-attestation fixtures (test-only helpers)
#
# RECURSION HAZARD.  ``src.m8.ahmed_provenance.build_test_attestation`` shells out
# to pytest over its attested file set, and that set contains the very test files
# that exercise it.  Calling it with the real ``subprocess.run`` from inside a
# pytest process would spawn pytest inside pytest without bound.  The
# ``command_runner`` parameter is the injection seam that prevents this, and every
# test in this repository MUST supply ``FakePytestRunner`` (or an equivalent fake).
# Nothing below ever starts a process, touches the network, or reads capture data.
# ─────────────────────────────────────────────────────────────────────────────


class _FakeCompletedProcess:
    """The three ``subprocess.CompletedProcess`` fields the builder consumes."""

    def __init__(self, returncode: int, stdout: str, stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _collect_only_stdout(node_ids) -> str:
    return "\n".join(node_ids) + f"\n\n{len(node_ids)} tests collected in 0.10s\n"


def _write_junit_xml(report_path: Path, node_ids, skipped_node_ids) -> None:
    """Write the outcome report pytest itself would write for this run.

    The builder reads the skipped node identities from this file rather than from a marker
    collection, so a fake pytest has to produce it.  The layout mirrors pytest's default
    ``xunit2`` family: ``classname`` is the node ID's file path with ``/`` replaced by ``.``
    and ``.py`` stripped, plus any enclosing classes, and ``name`` is the final component.
    """
    skipped = set(skipped_node_ids)
    suites = ElementTree.Element("testsuites")
    suite = ElementTree.SubElement(
        suites,
        "testsuite",
        name="pytest",
        errors="0",
        failures="0",
        skipped=str(len(skipped)),
        tests=str(len(node_ids)),
    )
    for node_id in node_ids:
        file_path, *rest = node_id.split("::")
        classname = ".".join([file_path.removesuffix(".py").replace("/", "."), *rest[:-1]])
        case = ElementTree.SubElement(
            suite, "testcase", classname=classname, name=rest[-1], time="0.01"
        )
        if node_id in skipped:
            ElementTree.SubElement(
                case, "skipped", type="pytest.skip", message="fixture skip"
            )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    ElementTree.ElementTree(suites).write(report_path, encoding="utf-8", xml_declaration=True)


class FakePytestRunner:
    """Deterministic ``subprocess.run`` stand-in that records argv and spawns nothing.

    The builder issues exactly four commands: a ``--collect-only`` pass whose stdout
    supplies the ordered node IDs, one ``-m <marker> --collect-only`` pass per declared
    skip class (``real_data``, then ``optional_artifact_or_mode``) whose stdout supplies
    that class's declared nodes, then an execution pass whose stdout supplies the outcome
    summary and whose ``--junitxml`` report supplies the OBSERVED skipped node IDs.  All
    four are synthesised here so a test can describe a green run, a red run, a declared or
    an undeclared skip, a declared node that executed anyway, or a crashed command without
    running anything.

    ``skipped_node_ids`` defaults to the union of the declared classes — the clean case in
    which every declared node did skip — and can be set independently to describe a machine
    where a declared node executed, or a run where an undeclared node skipped.
    """

    def __init__(
        self,
        node_ids,
        *,
        real_data_node_ids=(),
        optional_artifact_node_ids=(),
        skipped_node_ids=None,
        collect_stdout=None,
        real_data_collect_stdout=None,
        optional_artifact_collect_stdout=None,
        execute_stdout=None,
        collect_returncode: int = 0,
        real_data_collect_returncode: int | None = None,
        optional_artifact_collect_returncode: int | None = None,
        execute_returncode: int = 0,
    ):
        self.node_ids = list(node_ids)
        self.real_data_node_ids = list(real_data_node_ids)
        self.optional_artifact_node_ids = list(optional_artifact_node_ids)
        declared_union = list(
            dict.fromkeys([*self.real_data_node_ids, *self.optional_artifact_node_ids])
        )
        self.skipped_node_ids = (
            declared_union if skipped_node_ids is None else list(skipped_node_ids)
        )
        self.collect_stdout = (
            _collect_only_stdout(self.node_ids)
            if collect_stdout is None
            else collect_stdout
        )
        self.real_data_collect_stdout = (
            _collect_only_stdout(self.real_data_node_ids)
            if real_data_collect_stdout is None
            else real_data_collect_stdout
        )
        self.optional_artifact_collect_stdout = (
            _collect_only_stdout(self.optional_artifact_node_ids)
            if optional_artifact_collect_stdout is None
            else optional_artifact_collect_stdout
        )
        self.execute_stdout = (
            (
                f"{len(self.node_ids) - len(self.skipped_node_ids)} passed, "
                f"{len(self.skipped_node_ids)} skipped in 1.00s\n"
                if self.skipped_node_ids
                else f"{len(self.node_ids)} passed in 1.00s\n"
            )
            if execute_stdout is None
            else execute_stdout
        )
        self.collect_returncode = collect_returncode
        # pytest exits 5 when a marker expression selects no test at all.
        self.real_data_collect_returncode = (
            (0 if self.real_data_node_ids else 5)
            if real_data_collect_returncode is None
            else real_data_collect_returncode
        )
        self.optional_artifact_collect_returncode = (
            (0 if self.optional_artifact_node_ids else 5)
            if optional_artifact_collect_returncode is None
            else optional_artifact_collect_returncode
        )
        self.execute_returncode = execute_returncode
        self.calls: list[dict] = []

    def __call__(self, argv, *, cwd=None, env=None, capture_output=False, text=False):
        assert capture_output is True, "the builder must capture command output"
        assert text is True, "the builder must decode command output as text"
        assert env is not None, "the builder must pass an explicit environment"
        argv = list(argv)
        self.calls.append({"argv": argv, "cwd": cwd, "env": dict(env)})
        if "--collect-only" in argv:
            if "real_data" in argv:
                return _FakeCompletedProcess(
                    self.real_data_collect_returncode, self.real_data_collect_stdout
                )
            if "optional_artifact_or_mode" in argv:
                return _FakeCompletedProcess(
                    self.optional_artifact_collect_returncode,
                    self.optional_artifact_collect_stdout,
                )
            return _FakeCompletedProcess(self.collect_returncode, self.collect_stdout)
        report_options = [item for item in argv if item.startswith("--junitxml=")]
        assert len(report_options) == 1, (
            "the builder must ask the executed command for exactly one junit XML report"
        )
        _write_junit_xml(
            Path(report_options[0].removeprefix("--junitxml=")),
            self.node_ids,
            self.skipped_node_ids,
        )
        return _FakeCompletedProcess(self.execute_returncode, self.execute_stdout)


def fake_input_digest(path: str) -> str:
    """A stable, distinct, syntactically valid SHA-256 for one fixture path."""
    return hashlib.sha256(path.encode("utf-8")).hexdigest()


#: Fixture manifest digest; kept equal to the literal used by the preflight tests.
FAKE_SOURCE_MANIFEST_SHA256 = "1" * 64


@pytest.fixture
def attestation_inputs():
    """The attested test files and config inputs, imported as fixture plumbing only.

    These private constants are read here so a fixture can build a *consistent*
    source manifest and argv.  The scientific expectation — which files must be
    attested — is asserted from a literal list in
    ``tests/test_m8_ahmed_provenance.py`` and never from these constants.
    """
    from src.m8.ahmed_provenance import (  # noqa: WPS436 - test plumbing
        _ATTESTATION_CONFIG_INPUTS,
        _ATTESTED_TEST_FILES,
    )

    return tuple(_ATTESTED_TEST_FILES), tuple(_ATTESTATION_CONFIG_INPUTS)


@pytest.fixture
def fake_source_manifest(attestation_inputs):
    """A ``SourceManifest`` whose entries cover exactly the attestation input closure."""
    from src.m8.ahmed_provenance import SourceManifest

    test_files, config_inputs = attestation_inputs
    entries = tuple(
        {
            "path": path,
            "size_bytes": 1,
            "sha256": fake_input_digest(path),
            "source_commit": "a" * 40,
            "tracked": True,
            "dirty": False,
            "untracked": False,
            "status": "clean",
        }
        for path in sorted({*test_files, *config_inputs})
    )
    return SourceManifest(
        entries=entries,
        reference_entries=(),
        manifest_sha256=FAKE_SOURCE_MANIFEST_SHA256,
        git_commit="a" * 40,
        git_branch="fixture-branch",
        scoped_dirty=(),
        scoped_untracked=(),
        promotion_eligible=True,
    )


@pytest.fixture
def attested_node_ids(attestation_inputs):
    """One synthetic pytest node ID per attested test file, in production order."""
    test_files, _config_inputs = attestation_inputs
    return [f"{path}::test_contract_{index}" for index, path in enumerate(test_files)]


@pytest.fixture
def declared_real_data_node_ids(attested_node_ids):
    """One attested node standing in for a node carrying the ``real_data`` marker."""
    return [attested_node_ids[0]]


@pytest.fixture
def declared_optional_artifact_node_ids(attested_node_ids):
    """Two attested nodes standing in for the ``optional_artifact_or_mode`` class.

    Two, not one, so a test can describe the scientifically important asymmetric case: a
    machine that holds the optional artifact executes one of them and skips the other.
    """
    return [attested_node_ids[1], attested_node_ids[2]]


@pytest.fixture
def valid_test_attestation(fake_source_manifest, attested_node_ids, tmp_path):
    """A genuine ``build_test_attestation`` product, produced with a fake runner.

    Downstream preflight tests consume the builder's real output rather than a
    hand-rolled forgery, so a schema drift between builder and validator surfaces.
    """
    from src.m8.ahmed_provenance import build_test_attestation

    return build_test_attestation(
        fake_source_manifest,
        root=tmp_path,
        command_runner=FakePytestRunner(attested_node_ids),
    )
