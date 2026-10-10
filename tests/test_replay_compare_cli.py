"""Operator CLI guardrails for replay without hardware or raw-path overrides."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import scripts.replay_demo_compare as replay_cli
import scripts.verify_replay_demo_artifacts as verify_cli
from src.m2.common import ContractError
from src.replay_compare.advanced import AdvancedReplayUnavailable
from src.replay_compare.source import ReplaySourceError


def _config(path: Path, *, mode: str = "baseline", min_pi=0.5) -> Path:
    path.write_text(
        yaml.safe_dump(
            {
                "schema": "replay_demo_compare_config_v1",
                "capture": "20260728_224902_live_demo_massimo3",
                "mode": mode,
                "speed": 1.0,
                "output_root": "results/replay_compare",
                "reference_min_pi": min_pi,
                "poll_interval_ms": 50,
            },
            sort_keys=True,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return path


def _minimal_spec():
    return SimpleNamespace(
        capture_id="20260728_224902_live_demo_massimo3",
        frame_count=1,
        frame_rate_hz=20.0,
        registered_adc_sha256="a" * 64,
        effective_config_sha256="b" * 64,
    )


@pytest.mark.parametrize(
    "forbidden",
    ["--adc-path", "--reference-path", "--range-bin", "--locked-bin"],
)
def test_operator_cli_has_no_raw_reference_or_manual_bin_override(
    forbidden, capsys
):
    with pytest.raises(SystemExit) as exc_info:
        replay_cli.main(
            [
                "--development-data-root", "development-root",
                forbidden, "untrusted-value",
            ]
        )

    assert exc_info.value.code == 2
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize(
    "invalid_min_pi",
    [0.01, True, float("nan"), float("inf"), "0.5"],
    ids=("lower", "boolean", "nan", "infinity", "string"),
)
def test_replay_config_fixes_reference_quality_threshold(
    tmp_path, invalid_min_pi
):
    valid = replay_cli._load_config(_config(tmp_path / "valid.yaml"))
    assert valid["reference_min_pi"] == 0.5

    with pytest.raises(ValueError, match="fixed at exactly 0.5"):
        replay_cli._load_config(
            _config(tmp_path / "invalid.yaml", min_pi=invalid_min_pi)
        )


def test_clean_source_gate_covers_all_scientific_inputs(monkeypatch):
    calls = []

    def clean_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(stdout="")

    monkeypatch.setattr(replay_cli.subprocess, "run", clean_run)
    replay_cli._require_clean_scientific_source()

    command, kwargs = calls.pop()
    marker = command.index("--")
    assert command[:marker] == [
        "git", "status", "--porcelain", "--untracked-files=all",
    ]
    assert command[marker + 1:] == [
        "src", "scripts", "experiments", "reference_registry", "environment.yml",
    ]
    assert kwargs["cwd"] == replay_cli.ROOT
    assert kwargs["check"] is True
    assert kwargs["capture_output"] is True

    monkeypatch.setattr(
        replay_cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout=" M src/replay_compare/engine.py\n"
        ),
    )
    with pytest.raises(RuntimeError, match="commit the reviewed replay scientific source"):
        replay_cli._require_clean_scientific_source()

    monkeypatch.setattr(
        replay_cli.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            stdout="?? reference_registry/development_references_v1.json\n"
        ),
    )
    with pytest.raises(RuntimeError, match="commit the reviewed replay scientific source"):
        replay_cli._require_clean_scientific_source()


def test_operator_cli_rejects_output_outside_checkout_results(
    tmp_path, monkeypatch
):
    config = _config(tmp_path / "config.yaml")
    monkeypatch.setattr(replay_cli, "ROOT", tmp_path)
    monkeypatch.setattr(replay_cli, "_require_clean_scientific_source", lambda: None)
    monkeypatch.setattr(
        "src.replay_compare.session.resolve_replay_session",
        lambda *_args, **_kwargs: _minimal_spec(),
    )
    monkeypatch.setattr(
        "src.replay_compare.session.duration_frame_limit",
        lambda *_args, **_kwargs: 1,
    )
    escaped = tmp_path / "outside"

    with pytest.raises(SystemExit) as exc_info:
        replay_cli.main(
            [
                "--config", str(config),
                "--development-data-root", str(tmp_path / "development"),
                "--output-root", str(escaped),
                "--headless",
            ]
        )

    assert exc_info.value.code == 2
    assert escaped.exists() is False


def test_operator_cli_rejects_redirected_checkout_results_before_output(
    tmp_path, monkeypatch
):
    config = _config(tmp_path / "config.yaml")
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    redirected = tmp_path / "outside-results"
    redirected.mkdir()
    monkeypatch.setattr(replay_cli, "ROOT", checkout)
    monkeypatch.setattr(replay_cli, "_require_clean_scientific_source", lambda: None)
    monkeypatch.setattr(
        "src.replay_compare.session.resolve_replay_session",
        lambda *_args, **_kwargs: _minimal_spec(),
    )
    monkeypatch.setattr(
        "src.replay_compare.session.duration_frame_limit",
        lambda *_args, **_kwargs: 1,
    )
    original_resolve = Path.resolve

    def redirected_resolve(path, *args, **kwargs):
        if path == checkout / "results":
            return redirected
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", redirected_resolve)

    with pytest.raises(SystemExit) as exc_info:
        replay_cli.main(
            [
                "--config", str(config),
                "--development-data-root", str(tmp_path / "development"),
                "--headless",
            ]
        )

    assert exc_info.value.code == 2
    assert list(redirected.iterdir()) == []


def test_motion_gate_refusal_is_structured_before_reference_or_adc_read(
    tmp_path, monkeypatch, capsys
):
    config = _config(tmp_path / "motion.yaml", mode="motion")
    motion = tmp_path / "motion-config.yaml"
    motion.write_text("schema: test-only\n", encoding="utf-8", newline="\n")
    spec = _minimal_spec()
    reference_called = []
    monkeypatch.setattr(replay_cli, "ROOT", tmp_path)
    monkeypatch.setattr(replay_cli, "_require_clean_scientific_source", lambda: None)
    monkeypatch.setattr(
        "src.replay_compare.session.resolve_replay_session",
        lambda *_args, **_kwargs: spec,
    )
    monkeypatch.setattr(
        "src.replay_compare.session.duration_frame_limit",
        lambda *_args, **_kwargs: 1,
    )
    monkeypatch.setattr(
        "src.replay_compare.advanced.authorize_advanced_replay",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AdvancedReplayUnavailable("calibration fingerprint mismatch")
        ),
    )
    monkeypatch.setattr(
        "src.replay_compare.reference.PassiveReferenceTimeline.load",
        lambda *_args, **_kwargs: reference_called.append(True),
    )

    result = replay_cli.main(
        [
            "--config", str(config),
            "--development-data-root", str(tmp_path / "development"),
            "--mode", "motion",
            "--motion-config", str(motion),
            "--allow-legacy-validity-assumption",
            "--headless",
        ]
    )

    assert result == 2
    assert reference_called == []
    payload = json.loads(capsys.readouterr().err)
    refusal_path = Path(payload["diagnostic"])
    refusal = json.loads(refusal_path.read_text(encoding="utf-8"))
    assert refusal == {
        "adc_content_was_not_opened": True,
        "capture_id": spec.capture_id,
        "effective_config_sha256": spec.effective_config_sha256,
        "hardware_was_not_accessed": True,
        "mode": "motion",
        "reason": "calibration fingerprint mismatch",
        "registered_adc_expected_sha256": spec.registered_adc_sha256,
        "schema": "replay_compare_startup_refusal_v1",
    }


@pytest.mark.parametrize(
    "error",
    [
        ReplaySourceError("registered ADC is unavailable"),
        ContractError("guarded reference is unavailable"),
    ],
)
def test_verifier_cli_reports_guarded_input_errors_as_incomplete(
    tmp_path, monkeypatch, capsys, error
):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run_metadata.json").write_text(
        '{"mode":"baseline"}\n', encoding="utf-8", newline="\n"
    )
    monkeypatch.setattr(
        "src.replay_compare.session.resolve_replay_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(error),
    )

    result = verify_cli.main(
        [
            "--run-dir", str(run_dir),
            "--development-data-root", str(tmp_path / "development"),
        ]
    )

    assert result == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["accepted"] is False
    assert payload["source_binding_complete"] is False
    assert payload["issues"][0]["code"] == "verification_inputs_unavailable"
    assert type(error).__name__ in payload["issues"][0]["details"]
