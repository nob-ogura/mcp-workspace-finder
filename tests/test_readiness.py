import asyncio
import sys
import textwrap
import time
from pathlib import Path

import pytest

from app.config import load_server_definitions, resolve_service_modes
from app.process import launch_services_async


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


def test_services_launch_in_parallel_and_become_ready(tmp_path):
    async def _scenario():
        fast = _script(
            tmp_path / "fast.py",
            """
            #!/usr/bin/env python3
            import time
            time.sleep(0.15)
            print("ready-fast", flush=True)
            time.sleep(0.5)
            """,
        )
        medium = _script(
            tmp_path / "medium.py",
            """
            #!/usr/bin/env python3
            import time
            time.sleep(0.2)
            print("ready-medium", flush=True)
            time.sleep(0.5)
            """,
        )
        slow = _script(
            tmp_path / "slow.py",
            """
            #!/usr/bin/env python3
            import time
            time.sleep(0.25)
            print("ready-slow", flush=True)
            time.sleep(0.5)
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
                args: ["{fast}"]
                workdir: {tmp_path}
                env: {{}}
                mock:
                  exec: {sys.executable}
                  args: ["{fast}"]
                  workdir: {tmp_path}
              github:
                mode: mock
                kind: python
                exec: {sys.executable}
                args: ["{medium}"]
                workdir: {tmp_path}
                env: {{}}
                mock:
                  exec: {sys.executable}
                  args: ["{medium}"]
                  workdir: {tmp_path}
              drive:
                mode: mock
                kind: python
                exec: {sys.executable}
                args: ["{slow}"]
                workdir: {tmp_path}
                env: {{}}
                mock:
                  exec: {sys.executable}
                  args: ["{slow}"]
                  workdir: {tmp_path}
            """,
        )

        definitions = load_server_definitions(config_path)
        resolved = resolve_service_modes(definitions, force_mock=True, allow_real=True)

        start = time.perf_counter()
        statuses = await launch_services_async(definitions, resolved, readiness_timeout=0.6)
        elapsed = time.perf_counter() - start

        try:
            assert elapsed < 0.4
            assert all(status.ready for status in statuses.values())
        finally:
            await _cleanup(statuses)

    asyncio.run(_scenario())


def test_start_failure_warns_but_other_services_continue(tmp_path, caplog):
    caplog.set_level("WARNING")

    async def _scenario():
        failing = _script(
            tmp_path / "fail.py",
            """
            #!/usr/bin/env python3
            import sys
            sys.stderr.write("boom\n")
            sys.exit(1)
            """,
        )
        ready = _script(
            tmp_path / "ok.py",
            """
            #!/usr/bin/env python3
            import time
            time.sleep(0.05)
            print("ready", flush=True)
            time.sleep(0.5)
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
                args: ["{failing}"]
                workdir: {tmp_path}
                env: {{}}
                mock:
                  exec: {sys.executable}
                  args: ["{failing}"]
                  workdir: {tmp_path}
              github:
                mode: mock
                kind: python
                exec: {sys.executable}
                args: ["{ready}"]
                workdir: {tmp_path}
                env: {{}}
                mock:
                  exec: {sys.executable}
                  args: ["{ready}"]
                  workdir: {tmp_path}
              drive:
                mode: mock
                kind: python
                exec: {sys.executable}
                args: ["{ready}"]
                workdir: {tmp_path}
                env: {{}}
                mock:
                  exec: {sys.executable}
                  args: ["{ready}"]
                  workdir: {tmp_path}
            """,
        )

        definitions = load_server_definitions(config_path)
        resolved = resolve_service_modes(definitions, force_mock=True, allow_real=True)

        statuses = await launch_services_async(definitions, resolved, readiness_timeout=0.3)

        try:
            assert statuses["slack"].ready is False
            assert statuses["slack"].warning is not None
            assert "起動失敗" in statuses["slack"].warning or "exit" in statuses["slack"].warning
            assert statuses["github"].ready is True
            assert statuses["drive"].ready is True
            assert "slack" in caplog.text
        finally:
            await _cleanup(statuses)

    asyncio.run(_scenario())


def test_readiness_timeout_produces_warning(tmp_path, caplog):
    caplog.set_level("WARNING")

    async def _scenario():
        silent = _script(
            tmp_path / "silent.py",
            """
            #!/usr/bin/env python3
            import time
            time.sleep(2)
            """,
        )
        talkative = _script(
            tmp_path / "talkative.py",
            """
            #!/usr/bin/env python3
            import time
            time.sleep(0.05)
            print("ready", flush=True)
            time.sleep(0.5)
            """,
        )

        config_path = _write_config(
            tmp_path,
            f"""
            services:
              github:
                mode: mock
                kind: python
                exec: {sys.executable}
                args: ["{silent}"]
                workdir: {tmp_path}
                env: {{}}
                mock:
                  exec: {sys.executable}
                  args: ["{silent}"]
                  workdir: {tmp_path}
              drive:
                mode: mock
                kind: python
                exec: {sys.executable}
                args: ["{talkative}"]
                workdir: {tmp_path}
                env: {{}}
                mock:
                  exec: {sys.executable}
                  args: ["{talkative}"]
                  workdir: {tmp_path}
            """,
        )

        definitions = load_server_definitions(config_path)
        resolved = resolve_service_modes(definitions, force_mock=True, allow_real=True)

        statuses = await launch_services_async(definitions, resolved, readiness_timeout=0.2)

        try:
            assert statuses["github"].ready is False
            assert statuses["github"].warning is not None
            assert "timeout" in statuses["github"].warning
            assert statuses["drive"].ready is True
            assert "readiness timeout" in caplog.text
        finally:
            await _cleanup(statuses)

    asyncio.run(_scenario())
