from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Mapping

from app.config import ResolvedService, RunMode


class SmokeProbeError(RuntimeError):
    """Raised when a smoke probe fails."""


@dataclass
class SmokeServiceResult:
    name: str
    ok: bool
    detail: str
    dm_hit: bool | None = None

    @property
    def status(self) -> str:
        return "ok" if self.ok else "failed"

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["status"] = self.status
        return payload


def _http_json_request(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    data: bytes | None = None,
    timeout: float = 10.0,
) -> dict:
    request = urllib.request.Request(url, data=data, method=method)
    for key, value in (headers or {}).items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read()
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        # surface API error details to the caller instead of hiding them in the traceback
        detail = ""
        try:
            detail = exc.read().decode("utf-8")  # type: ignore[assignment]
        except Exception:  # noqa: BLE001
            detail = "<no response body>"
        raise SmokeProbeError(f"{exc.code} {exc.reason} for {url}: {detail}") from exc

    try:
        return json.loads(body.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise SmokeProbeError(f"invalid JSON response from {url}: {exc}") from exc


def _is_dm_hit(match: Mapping[str, object]) -> bool:
    channel = match.get("channel") or {}
    if not isinstance(channel, Mapping):
        return False
    return bool(channel.get("is_im") or channel.get("is_group") or channel.get("is_private"))


def slack_probe(
    *,
    token: str | None = None,
    query: str | None = None,
    http_request: Callable[..., dict] = _http_json_request,
) -> SmokeServiceResult:
    token = token or os.getenv("SLACK_USER_TOKEN")
    if not token:
        raise SmokeProbeError("SLACK_USER_TOKEN missing")

    query = query or os.getenv("SLACK_SMOKE_QUERY", "smoke")
    params = urllib.parse.urlencode({"query": query, "count": 5, "sort": "timestamp", "highlight": 0})
    url = f"https://slack.com/api/search.messages?{params}"
    resp = http_request(url, headers={"Authorization": f"Bearer {token}"})

    if not resp.get("ok"):
        raise SmokeProbeError(f"Slack API error: {resp.get('error', 'unknown')}")

    matches = resp.get("messages", {}).get("matches") or []
    if not matches:
        raise SmokeProbeError("Slack search returned 0 hits")

    dm_hit = any(_is_dm_hit(match) for match in matches)
    if not dm_hit:
        raise SmokeProbeError("DM/Private hit not found")

    return SmokeServiceResult(name="slack", ok=True, detail=f"{len(matches)} hits", dm_hit=True)


def github_probe(
    *,
    token: str | None = None,
    repo: str | None = None,
    query: str | None = None,
    http_request: Callable[..., dict] = _http_json_request,
) -> SmokeServiceResult:
    token = token or os.getenv("GITHUB_TOKEN")
    if not token:
        raise SmokeProbeError("GITHUB_TOKEN missing")

    repo = repo or os.getenv("GITHUB_SMOKE_REPO")
    if not repo:
        raise SmokeProbeError("GITHUB_SMOKE_REPO missing (owner/repo)")

    query = query or os.getenv("GITHUB_SMOKE_QUERY", "smoke")
    # normalise the query so we don't accidentally URL-encode literal '+' separators
    query = query.strip().strip('"').strip("'")
    q = f"repo:{repo} {query.replace('+', ' ')}"
    params = urllib.parse.urlencode({"q": q, "per_page": 1})
    url = f"https://api.github.com/search/issues?{params}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "mcp-workspace-finder-smoke",
    }

    resp = http_request(url, headers=headers)
    if int(resp.get("total_count", 0)) < 1:
        raise SmokeProbeError("GitHub search returned 0 hits")

    return SmokeServiceResult(name="github", ok=True, detail="1+ hits")


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def _parse_expiry(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        # google tokens store RFC3339, include timezone.
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _needs_refresh(expiry: str | None, now: datetime) -> bool:
    parsed = _parse_expiry(expiry)
    if not parsed:
        return False
    return parsed <= now + timedelta(seconds=60)


def _refresh_drive_token(
    token: Mapping[str, object],
    credentials_path: Path,
    http_request: Callable[..., dict],
    now: datetime,
) -> dict:
    credentials = _load_json(credentials_path)
    client_info = credentials.get("installed") or credentials.get("web") or {}
    client_id = client_info.get("client_id")
    client_secret = client_info.get("client_secret")
    refresh_token = token.get("refresh_token")

    if not all([client_id, client_secret, refresh_token]):
        raise SmokeProbeError("Drive refresh token or client secrets missing")

    body = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
    ).encode()

    resp = http_request(
        "https://oauth2.googleapis.com/token",
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data=body,
    )

    access_token = resp.get("access_token")
    if not access_token:
        raise SmokeProbeError("failed to refresh Google Drive token")

    expires_in = int(resp.get("expires_in", 0))
    expiry_time = now + timedelta(seconds=expires_in)
    updated = dict(token)
    updated.update(
        {
            "access_token": access_token,
            "expiry": expiry_time.astimezone(timezone.utc).isoformat(),
        }
    )
    return updated


def drive_probe(
    *,
    token_path: str | None = None,
    credentials_path: str | None = None,
    query: str | None = None,
    http_request: Callable[..., dict] = _http_json_request,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> SmokeServiceResult:
    token_path = token_path or os.getenv("DRIVE_TOKEN_PATH")
    credentials_path = credentials_path or os.getenv("GOOGLE_CREDENTIALS_PATH")

    if not token_path:
        raise SmokeProbeError("DRIVE_TOKEN_PATH missing")

    token_file = Path(token_path)
    token = _load_json(token_file)
    if not token:
        raise SmokeProbeError("token.json missing or empty")

    access_token = token.get("access_token")
    if not access_token or _needs_refresh(str(token.get("expiry")), now()):
        if not credentials_path:
            raise SmokeProbeError("Drive credentials missing")
        token = _refresh_drive_token(token, Path(credentials_path), http_request, now())
        access_token = token.get("access_token")
        _write_json(token_file, token)

    query = query or os.getenv("DRIVE_SMOKE_QUERY", "name contains 'smoke'")
    params = urllib.parse.urlencode({"q": query, "pageSize": 1, "fields": "files(id,name)"})
    resp = http_request(
        f"https://www.googleapis.com/drive/v3/files?{params}",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    files = resp.get("files") or []
    if not files:
        raise SmokeProbeError("Drive search returned 0 files")

    return SmokeServiceResult(name="drive", ok=True, detail=f"{len(files)} hits")


DEFAULT_PROBES: dict[str, Callable[[], SmokeServiceResult]] = {
    "slack": slack_probe,
    "github": github_probe,
    "drive": drive_probe,
}

def _default_probes() -> dict[str, Callable[[], SmokeServiceResult]]:
    """Return fresh mapping so monkeypatching works in tests."""
    return {
        "slack": slack_probe,
        "github": github_probe,
        "drive": drive_probe,
    }


def run_smoke_checks(
    resolved: Mapping[str, ResolvedService],
    probes: Mapping[str, Callable[[], SmokeServiceResult]] | None = None,
) -> dict[str, SmokeServiceResult]:
    probes = probes or _default_probes()
    results: dict[str, SmokeServiceResult] = {}

    for name, decision in resolved.items():
        if decision.selected_mode is not RunMode.REAL:
            results[name] = SmokeServiceResult(name=name, ok=False, detail="skipped (mock mode)")
            continue

        probe = probes.get(name)
        if not probe:
            results[name] = SmokeServiceResult(name=name, ok=False, detail="no probe defined")
            continue

        try:
            result = probe()
        except SmokeProbeError as exc:
            results[name] = SmokeServiceResult(name=name, ok=False, detail=str(exc))
            continue

        # ensure correct name
        if result.name != name:
            result.name = name
        results[name] = result

    return results


def summarise_results(results: Mapping[str, SmokeServiceResult]) -> dict[str, object]:
    if not results:
        return {"status": "skipped", "detail": "no services"}
    status = "passed" if all(result.ok for result in results.values()) else "failed"
    return {"status": status, "checked": list(results.keys())}


def write_report(path: Path, results: Mapping[str, SmokeServiceResult]) -> None:
    payload = {
        "summary": summarise_results(results),
        "services": {name: result.to_dict() for name, result in results.items()},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False))


def format_result_line(result: SmokeServiceResult) -> str:
    marker = "ok" if result.ok else "failed"
    extra = " (dm hit)" if result.dm_hit else ""
    return f"{result.name}: {marker} - {result.detail}{extra}"
