import json
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from app.__main__ import app as cli_app
from app.smoke import SmokeProbeError, SmokeServiceResult


runner = CliRunner()


def _write_config(tmp_path: Path) -> Path:
    path = tmp_path / "servers.yaml"
    path.write_text(
        textwrap.dedent(
            """
            services:
              slack:
                mode: real
                kind: python
                exec: /bin/echo
                args: ["ready"]
                workdir: .
                env:
                  SLACK_USER_TOKEN: ${SLACK_USER_TOKEN}
                auth_files:
                  - path: ${SLACK_TOKEN_PATH}
                mock:
                  exec: /bin/echo
                  args: ["mock-slack"]
                  workdir: .
              github:
                mode: real
                kind: python
                exec: /bin/echo
                args: ["ready"]
                workdir: .
                env:
                  GITHUB_TOKEN: ${GITHUB_TOKEN}
                auth_files:
                  - path: ${GITHUB_TOKEN_PATH}
                mock:
                  exec: /bin/echo
                  args: ["mock-github"]
                  workdir: .
              drive:
                mode: real
                kind: python
                exec: /bin/echo
                args: ["ready"]
                workdir: .
                env:
                  DRIVE_TOKEN_PATH: ${DRIVE_TOKEN_PATH}
                  GOOGLE_CREDENTIALS_PATH: ${GOOGLE_CREDENTIALS_PATH}
                auth_files:
                  - path: ${DRIVE_TOKEN_PATH}
                mock:
                  exec: /bin/echo
                  args: ["mock-drive"]
                  workdir: .
            """
        ).strip()
    )
    return path


def _set_env(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ALLOW_REAL", "1")
    monkeypatch.setenv("SLACK_USER_TOKEN", "token-slack")
    monkeypatch.setenv("GITHUB_TOKEN", "token-gh")

    slack_auth = tmp_path / "slack_token.json"
    slack_auth.write_text("{}")
    monkeypatch.setenv("SLACK_TOKEN_PATH", str(slack_auth))

    github_auth = tmp_path / "github_token.json"
    github_auth.write_text("{}")
    monkeypatch.setenv("GITHUB_TOKEN_PATH", str(github_auth))

    drive_token = tmp_path / "token.json"
    drive_token.write_text("{}")
    monkeypatch.setenv("DRIVE_TOKEN_PATH", str(drive_token))
    creds = tmp_path / "creds.json"
    creds.write_text("{}")
    monkeypatch.setenv("GOOGLE_CREDENTIALS_PATH", str(creds))


def test_smoke_command_reports_success(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path)
    _set_env(monkeypatch, tmp_path)

    monkeypatch.setattr(
        "app.smoke.slack_probe",
        lambda: SmokeServiceResult(name="slack", ok=True, detail="ok", dm_hit=True),
    )
    monkeypatch.setattr(
        "app.smoke.github_probe",
        lambda: SmokeServiceResult(name="github", ok=True, detail="ok"),
    )
    monkeypatch.setattr(
        "app.smoke.drive_probe",
        lambda: SmokeServiceResult(name="drive", ok=True, detail="ok"),
    )

    report_path = tmp_path / "report.json"
    result = runner.invoke(
        cli_app, ["smoke", "--config", str(config_path), "--report", str(report_path)]
    )

    assert result.exit_code == 0
    assert "real smoke passed" in result.stdout
    assert report_path.exists()

    payload = json.loads(report_path.read_text())
    assert payload["summary"]["status"] == "passed"
    assert payload["services"]["slack"]["dm_hit"] is True


def test_smoke_command_marks_failure(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path)
    _set_env(monkeypatch, tmp_path)

    def _raise():
        raise SmokeProbeError("slack search failed")

    monkeypatch.setattr("app.smoke.slack_probe", _raise)
    monkeypatch.setattr(
        "app.smoke.github_probe",
        lambda: SmokeServiceResult(name="github", ok=True, detail="ok"),
    )
    monkeypatch.setattr(
        "app.smoke.drive_probe",
        lambda: SmokeServiceResult(name="drive", ok=True, detail="ok"),
    )

    result = runner.invoke(cli_app, ["smoke", "--config", str(config_path)])

    assert result.exit_code != 0
    assert "slack" in result.stdout
    assert "real smoke failed" in result.stdout


def test_smoke_skips_when_allow_real_missing(monkeypatch, tmp_path):
    config_path = _write_config(tmp_path)
    _set_env(monkeypatch, tmp_path)
    monkeypatch.delenv("ALLOW_REAL", raising=False)

    result = runner.invoke(cli_app, ["smoke", "--config", str(config_path)])

    assert result.exit_code == 0
    assert "real smoke skipped" in result.stdout
