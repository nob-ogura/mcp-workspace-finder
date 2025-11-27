import json
import textwrap
from pathlib import Path

import pytest

from app.config import RunMode, load_server_definitions, resolve_service_modes
from app.process import start_services


def _write_config(tmp_path: Path, yaml_text: str) -> Path:
    path = tmp_path / "servers.yaml"
    path.write_text(textwrap.dedent(yaml_text))
    return path


def _terminate_if_alive(process) -> None:
    if process and process.poll() is None:
        process.terminate()
        process.wait(timeout=2)


def test_real_mode_builds_command_and_pipes_stdio(monkeypatch, tmp_path):
    trace_file = tmp_path / "trace.json"
    monkeypatch.setenv("SLACK_USER_TOKEN", "token-value")
    monkeypatch.setenv("TRACE_FILE", str(trace_file))

    fake_binary = tmp_path / "slack_server.py"
    fake_binary.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json, os, sys, time

            trace = {"argv": sys.argv[1:], "token": os.getenv("SLACK_USER_TOKEN")}
            with open(os.environ["TRACE_FILE"], "w", encoding="utf-8") as fp:
                json.dump(trace, fp)
            print("ready", flush=True)
            time.sleep(5)
            """
        )
    )
    fake_binary.chmod(0o755)

    config_path = _write_config(
        tmp_path,
        f"""
        services:
          slack:
            mode: real
            kind: binary
            exec: {fake_binary}
            args: ["--listen", "4000"]
            workdir: {tmp_path}
            env:
              SLACK_USER_TOKEN: ${{SLACK_USER_TOKEN}}
            mock:
              exec: {fake_binary}
              args: ["--mock"]
              workdir: {tmp_path}
        """,
    )

    definitions = load_server_definitions(config_path)
    resolved = resolve_service_modes(definitions, force_mock=False, allow_real=True)

    results = start_services(definitions, resolved)
    slack = results["slack"]

    try:
        assert slack.mode is RunMode.REAL
        assert slack.process is not None
        assert slack.command == [str(fake_binary), "--listen", "4000"]
        assert slack.process.stdin is not None
        assert slack.process.stdout is not None

        line = slack.process.stdout.readline().strip()
        assert line == "ready"

        payload = json.loads(trace_file.read_text())
        assert payload["argv"] == ["--listen", "4000"]
        assert payload["token"] == "token-value"
    finally:
        _terminate_if_alive(slack.process)


def test_missing_exec_path_logs_error_and_continues(monkeypatch, tmp_path, caplog):
    caplog.set_level("ERROR")
    simple_runner = tmp_path / "runner.py"
    simple_runner.write_text("#!/usr/bin/env python3\nimport time; time.sleep(0.05)")
    simple_runner.chmod(0o755)

    missing_exec = tmp_path / "missing-drive"

    config_path = _write_config(
        tmp_path,
        f"""
        services:
          slack:
            mode: mock
            kind: binary
            exec: {simple_runner}
            args: ["slack"]
            workdir: {tmp_path}
            env: {{}}
          github:
            mode: mock
            kind: python
            exec: {simple_runner}
            args: ["github"]
            workdir: {tmp_path}
            env: {{}}
          drive:
            mode: mock
            kind: python
            exec: {missing_exec}
            args: []
            workdir: {tmp_path}
            env: {{}}
        """,
    )

    definitions = load_server_definitions(config_path)
    resolved = resolve_service_modes(definitions, force_mock=True, allow_real=False)

    results = start_services(definitions, resolved)

    assert "drive" in caplog.text
    assert "実行パス不備" in caplog.text
    assert results["drive"].error is not None
    assert results["drive"].process is None
    assert results["slack"].process is not None
    assert results["github"].process is not None


def test_mock_mode_uses_mock_command(monkeypatch, tmp_path):
    trace_file = tmp_path / "mock_trace.txt"
    monkeypatch.setenv("TRACE_FILE", str(trace_file))

    real_exec = tmp_path / "real.py"
    real_exec.write_text("#!/usr/bin/env python3\nimport sys; open(sys.argv[1], 'w').write('real')")
    real_exec.chmod(0o755)

    mock_exec = tmp_path / "mock.py"
    mock_exec.write_text(
        "#!/usr/bin/env python3\nimport os; open(os.environ['TRACE_FILE'], 'w').write('mock-run')"
    )
    mock_exec.chmod(0o755)

    config_path = _write_config(
        tmp_path,
        f"""
        services:
          github:
            mode: mock
            kind: python
            exec: {real_exec}
            args: ["real-target"]
            workdir: {tmp_path}
            env: {{}}
            mock:
              exec: {mock_exec}
              args: ["--serve"]
              workdir: {tmp_path}
        """,
    )

    definitions = load_server_definitions(config_path)
    resolved = resolve_service_modes(definitions, force_mock=True, allow_real=True)

    results = start_services(definitions, resolved)
    github = results["github"]
    github.process.wait(timeout=2)

    assert github.mode is RunMode.MOCK
    assert github.command == [str(mock_exec), "--serve"]
    assert trace_file.read_text() == "mock-run"
