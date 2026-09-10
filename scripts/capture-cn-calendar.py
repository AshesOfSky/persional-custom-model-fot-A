#!/usr/bin/env python3
"""Capture and admit official 2026 A-share cash-market calendars."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
from html import unescape
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request, urlopen


SCRIPT_VERSION = "cn-calendar-capture-2"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "calendar" / "cn-cash"
MAX_SOURCE_BYTES = 2_000_000
PUBLISHED_AT = "2025-12-22T23:59:59+08:00"


@dataclass(frozen=True)
class SourceSpec:
    exchange: str
    host: str
    url: str
    revision: str


@dataclass(frozen=True)
class SourceCapture:
    spec: SourceSpec
    body: bytes
    content_type: str
    final_url: str


SOURCES = (
    SourceSpec(
        exchange="SSE",
        host="www.sse.com.cn",
        url=(
            "https://www.sse.com.cn/disclosure/announcement/general/c/"
            "c_20251222_10802507.shtml"
        ),
        revision="\u4e0a\u8bc1\u516c\u544a\u30142025\u301545\u53f7",
    ),
    SourceSpec(
        exchange="SZSE",
        host="www.szse.cn",
        url="https://www.szse.cn/disclosure/notice/t20251222_618087.html",
        revision="\u6df1\u8bc1\u4f1a\u30142025\u3015481\u53f7",
    ),
    SourceSpec(
        exchange="BSE",
        host="www.bse.cn",
        url="https://www.bse.cn/important_news/200027428.html",
        revision="\u5317\u8bc1\u516c\u544a\u30142025\u301558\u53f7",
    ),
)

EXPECTED_CLOSURE_TOKENS = (
    "1\u67081\u65e5",
    "2\u670823\u65e5",
    "4\u67086\u65e5",
    "5\u67085\u65e5",
    "6\u670819\u65e5",
    "9\u670825\u65e5",
    "10\u67087\u65e5",
)

# Weekends are always closed.  These are the weekday closures explicitly
# announced by all three exchanges in their 2025-12-22 annual notices.
CLOSED_WEEKDAYS = frozenset(
    {
        date(2026, 1, 1),
        date(2026, 1, 2),
        date(2026, 2, 16),
        date(2026, 2, 17),
        date(2026, 2, 18),
        date(2026, 2, 19),
        date(2026, 2, 20),
        date(2026, 2, 23),
        date(2026, 4, 6),
        date(2026, 5, 1),
        date(2026, 5, 4),
        date(2026, 5, 5),
        date(2026, 6, 19),
        date(2026, 9, 25),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
        date(2026, 10, 6),
        date(2026, 10, 7),
    }
)


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _decode_body(body: bytes, charset: str | None = None) -> str:
    candidates = tuple(
        dict.fromkeys(item for item in (charset, "utf-8", "gb18030") if item)
    )
    for encoding in candidates:
        try:
            return body.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    raise RuntimeError("official calendar source is not decodable text")


def _visible_compact_text(body: bytes, charset: str | None = None) -> str:
    decoded = unescape(_decode_body(body, charset))
    without_tags = re.sub(r"<[^>]*>", "", decoded)
    return re.sub(r"\s+", "", without_tags)


def _validate_source(
    spec: SourceSpec,
    body: bytes,
    *,
    final_url: str,
    charset: str | None = None,
) -> None:
    parsed = urlparse(final_url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() != spec.host:
        raise RuntimeError(
            f"{spec.exchange} source redirected outside admitted HTTPS host"
        )
    if not body or len(body) > MAX_SOURCE_BYTES:
        raise RuntimeError(f"{spec.exchange} source body size is not admissible")
    compact = _visible_compact_text(body, charset)
    required = ("2026", spec.revision, *EXPECTED_CLOSURE_TOKENS)
    missing = [token for token in required if token not in compact]
    if missing:
        raise RuntimeError(
            f"{spec.exchange} source is missing expected revision/closure markers"
        )


def _fetch_source(spec: SourceSpec, *, timeout: float) -> SourceCapture:
    request = Request(
        spec.url,
        headers={
            "Accept": "text/html,application/xhtml+xml",
            "User-Agent": "custom-model-calendar-capture/1.0",
        },
        method="GET",
    )
    with urlopen(request, timeout=timeout) as response:
        status = int(getattr(response, "status", response.getcode()))
        if status != 200:
            raise RuntimeError(f"{spec.exchange} source returned HTTP {status}")
        body = response.read(MAX_SOURCE_BYTES + 1)
        final_url = response.geturl()
        content_type = response.headers.get("Content-Type", "")
        charset = response.headers.get_content_charset()
    _validate_source(spec, body, final_url=final_url, charset=charset)
    return SourceCapture(
        spec=spec,
        body=body,
        content_type=content_type,
        final_url=final_url,
    )


def _trading_days() -> tuple[date, ...]:
    current = date(2026, 1, 1)
    end = date(2026, 12, 31)
    admitted: list[date] = []
    while current <= end:
        if current.weekday() < 5 and current not in CLOSED_WEEKDAYS:
            admitted.append(current)
        current += timedelta(days=1)
    result = tuple(admitted)
    if len(result) != 242:
        raise RuntimeError("2026 admitted calendar must contain exactly 242 sessions")
    return result


def build_snapshot(
    capture: SourceCapture,
    *,
    captured_at: datetime,
) -> dict[str, Any]:
    if captured_at.tzinfo is None or captured_at.utcoffset() is None:
        raise ValueError("captured_at must be timezone-aware")
    captured_utc = captured_at.astimezone(timezone.utc)
    timestamp_id = captured_utc.strftime("%Y%m%dT%H%M%SZ")
    snapshot_id = f"cn-{capture.spec.exchange.lower()}-cash-2026-{timestamp_id}"
    payload: dict[str, Any] = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "market": "CN",
        "exchanges": [capture.spec.exchange],
        "timezone": "Asia/Shanghai",
        "source": capture.spec.url,
        "source_revision": capture.spec.revision,
        "published_at": PUBLISHED_AT,
        "captured_at": captured_utc.isoformat(timespec="seconds"),
        "coverage_start": "2026-01-01",
        "coverage_end": "2026-12-31",
        "coverage_complete": True,
        "point_in_time_reproducible": True,
        "sessions": [
            {
                "name": "morning",
                "opens_at": "09:30:00",
                "closes_at": "11:30:00",
            },
            {
                "name": "afternoon",
                "opens_at": "13:00:00",
                "closes_at": "15:00:00",
            },
        ],
        "trading_days": [item.isoformat() for item in _trading_days()],
    }
    payload["content_sha256"] = _sha256(_canonical_bytes(payload))
    return payload


def _json_file_bytes(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_immutable(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != content:
            raise RuntimeError(f"immutable calendar artifact already differs: {path}")
        return
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            raise RuntimeError(f"calendar artifact appeared during capture: {path}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def persist_capture(
    capture: SourceCapture,
    snapshot: dict[str, Any],
    *,
    output_root: Path,
) -> dict[str, Any]:
    snapshot_id = str(snapshot["snapshot_id"])
    source_root = output_root / "sources"
    raw_path = source_root / f"{snapshot_id}.html"
    snapshot_path = output_root / f"{snapshot_id}.json"
    _write_immutable(raw_path, capture.body)
    _write_immutable(snapshot_path, _json_file_bytes(snapshot))
    return {
        "exchange": capture.spec.exchange,
        "snapshot": str(snapshot_path),
        "snapshot_id": snapshot_id,
        "raw_source": str(raw_path),
        "trading_days": len(snapshot["trading_days"]),
    }


def capture_all(*, output_root: Path, timeout: float) -> dict[str, Any]:
    # Fetch and validate every exchange before writing any artifact.
    captures = tuple(_fetch_source(spec, timeout=timeout) for spec in SOURCES)
    captured_at = datetime.now(timezone.utc).replace(microsecond=0)
    snapshots = tuple(
        build_snapshot(capture, captured_at=captured_at) for capture in captures
    )
    admitted = tuple(
        persist_capture(capture, snapshot, output_root=output_root)
        for capture, snapshot in zip(captures, snapshots, strict=True)
    )
    return {
        "capture_script_version": SCRIPT_VERSION,
        "captured_at": captured_at.isoformat(timespec="seconds"),
        "output_root": str(output_root.resolve()),
        "snapshots": admitted,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
        help="immutable calendar snapshot directory",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    result = capture_all(output_root=args.output_root.resolve(), timeout=args.timeout)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
