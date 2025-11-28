import asyncio
import os
import signal
import sys
import textwrap
from pathlib import Path

import pytest

from app.config import load_server_definitions, resolve_service_modes
from app.process import launch_services_async, monitor_services


def _write_config(tmp_path: Path, yaml_text: str) -> Path:
    path = tmp_path / "servers.yaml"
    path.write_text(textwrap.dedent(yaml_text))
    return path


def _script(path: Path, body: str) -> Path:
    content = textwrap.dedent(body).lstrip()
    path.write_text(content)
    path.chmod(0o755)
    return path


async def _cleanup(statuses):
    for status in statuses.values():
        process = status.process
        if process and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=1)
            except asyncio.TimeoutError:
                process.kill()


def test_restart_once_on_abnormal_exit(tmp_path, caplog):
    caplog.set_level("WARNING")

    async def _scenario():
        pid_file = tmp_path / "slack.pid"
        crashable = _script(
            tmp_path / "crashable.py",
            """
            #!/usr/bin/env python3
            import os, time

            with open(os.environ["PID_FILE"], "w", encoding="utf-8") as fp:
                fp.write(str(os.getpid()))

            print("ready", flush=True)
            time.sleep(5)
            """,
        )

        config_path = _write_config(
            tmp_path,
            f"""
            services:
              slack:
                mode: mock
                kind: python
                exec: {sys.executable}
                args: ["{crashable}"]
                workdir: {tmp_path}
                env:
                  PID_FILE: {pid_file}
                mock:
                  exec: {sys.executable}
                  args: ["{crashable}"]
                  workdir: {tmp_path}
            """,
        )

        definitions = load_server_definitions(config_path)
        resolved = resolve_service_modes(definitions, force_mock=True, allow_real=True)

        statuses = await launch_services_async(definitions, resolved, readiness_timeout=0.3)
        assert statuses["slack"].ready is True

        async def _kill_once():
            await asyncio.sleep(0.1)
            os.kill(int(pid_file.read_text()), signal.SIGTERM)

        monitor = asyncio.create_task(
            monitor_services(
                definitions,
                resolved,
                statuses,
                readiness_timeout=0.3,
                stop_after=0.8,
            )
        )

        await asyncio.gather(_kill_once(), monitor)

        slack = statuses["slack"]
        try:
            assert slack.restart_count == 1
            assert slack.process is not None
            assert slack.process.returncode is None
            assert pid_file.read_text() == str(slack.process.pid)
            assert "restart attempt #1" in caplog.text
        finally:
            await _cleanup(statuses)

    asyncio.run(_scenario())


def test_does_not_restart_more_than_once(tmp_path, caplog):
    caplog.set_level("WARNING")

    async def _scenario():
        pid_file = tmp_path / "slack.pid"
        crashable = _script(
            tmp_path / "crash_twice.py",
            """
            #!/usr/bin/env python3
            import os, time

            with open(os.environ["PID_FILE"], "w", encoding="utf-8") as fp:
                fp.write(str(os.getpid()))

            print("ready", flush=True)
            time.sleep(5)
            """,
        )

        config_path = _write_config(
            tmp_path,
            f"""
            services:
              slack:
                mode: mock
                kind: python
                exec: {sys.executable}
                args: ["{crashable}"]
                workdir: {tmp_path}
                env:
                  PID_FILE: {pid_file}
                mock:
                  exec: {sys.executable}
                  args: ["{crashable}"]
                  workdir: {tmp_path}
            """,
        )

        definitions = load_server_definitions(config_path)
        resolved = resolve_service_modes(definitions, force_mock=True, allow_real=True)

        statuses = await launch_services_async(definitions, resolved, readiness_timeout=0.3)
        assert statuses["slack"].ready is True

        async def _kill_twice():
            await asyncio.sleep(0.1)
            os.kill(int(pid_file.read_text()), signal.SIGTERM)
            await asyncio.sleep(0.3)
            os.kill(int(pid_file.read_text()), signal.SIGTERM)

        monitor = asyncio.create_task(
            monitor_services(
                definitions,
                resolved,
                statuses,
                readiness_timeout=0.3,
                stop_after=1.0,
            )
        )

        await asyncio.gather(_kill_twice(), monitor)

        slack = statuses["slack"]
        try:
            assert slack.restart_count == 1
            assert slack.process is None or slack.process.returncode is not None
            assert "再起動上限に達した" in caplog.text
        finally:
            await _cleanup(statuses)

    asyncio.run(_scenario())


def test_auth_error_does_not_retry(tmp_path, caplog):
    caplog.set_level("WARNING")

    async def _scenario():
        auth_failure = _script(
            tmp_path / "auth_fail.py",
            """
            #!/usr/bin/env python3
            import sys

            sys.stderr.write("auth error: invalid token\\n")
            sys.stderr.flush()
            sys.exit(1)
            """,
        )

        ok_service = _script(
            tmp_path / "ok.py",
            """
            #!/usr/bin/env python3
            import time
            print("ready", flush=True)
            time.sleep(1.0)
            """,
        )

        config_path = _write_config(
            tmp_path,
            f"""
            services:
              drive:
                mode: mock
                kind: python
                exec: {sys.executable}
                args: ["{auth_failure}"]
                workdir: {tmp_path}
                env: {{}}
                mock:
                  exec: {sys.executable}
                  args: ["{auth_failure}"]
                  workdir: {tmp_path}
              slack:
                mode: mock
                kind: python
                exec: {sys.executable}
                args: ["{ok_service}"]
                workdir: {tmp_path}
                env: {{}}
                mock:
                  exec: {sys.executable}
                  args: ["{ok_service}"]
                  workdir: {tmp_path}
            """,
        )

        definitions = load_server_definitions(config_path)
        resolved = resolve_service_modes(definitions, force_mock=True, allow_real=True)

        statuses = await launch_services_async(definitions, resolved, readiness_timeout=0.2)

        await monitor_services(
            definitions,
            resolved,
            statuses,
            readiness_timeout=0.2,
            stop_after=0.5,
        )

        drive = statuses["drive"]
        slack = statuses["slack"]

        try:
            assert drive.restart_count == 0
            assert drive.process is None or drive.process.returncode is not None
            assert drive.ready is False
            assert "auth error" in caplog.text.lower()
            assert "restart attempt" not in caplog.text
            assert slack.ready is True
        finally:
            await _cleanup(statuses)

    asyncio.run(_scenario())
