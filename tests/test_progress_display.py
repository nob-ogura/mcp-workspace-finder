from rich.console import Console

from app.progress_display import ProgressDisplay


class _FakeStatus:
    def __init__(self):
        self.messages: list[str] = []
        self.entered = 0
        self.exited = 0
        self.initial: str | None = None

    def __enter__(self):
        self.entered += 1
        return self

    def __exit__(self, exc_type, exc, tb):
        self.exited += 1

    def update(self, message: str):
        self.messages.append(message)


def test_progress_display_updates_status_in_order(monkeypatch):
    console = Console(force_terminal=True, color_system=None)
    fake = _FakeStatus()

    def fake_status(message: str, spinner: str = "dots"):
        fake.initial = message
        return fake

    monkeypatch.setattr(console, "status", fake_status)
    display = ProgressDisplay(console)

    display.run(["step1", "step2"], delay=0)

    assert fake.initial == "step1"
    assert fake.messages == ["step1", "step2"]
    assert fake.entered == 1
    assert fake.exited == 1


def test_progress_display_skips_when_no_steps(monkeypatch):
    console = Console()
    calls: list[tuple] = []

    def fake_status(*args, **kwargs):
        calls.append((args, kwargs))
        return _FakeStatus()

    monkeypatch.setattr(console, "status", fake_status)
    display = ProgressDisplay(console)

    display.run([], delay=0.01)

    assert calls == []
