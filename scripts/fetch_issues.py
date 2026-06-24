#!/usr/bin/env python3
"""Fetch raw GitHub issue-search deltas into a cache directory."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

API_VERSION = "2022-11-28"
DEFAULT_API_URL = "https://api.github.com"
LINK_PATTERN = re.compile(r'<([^>]+)>;\s*rel="([^"]+)"')


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_duration(value: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([smhd])", value)
    if not match:
        raise argparse.ArgumentTypeError("duration must look like 30m, 1h, or 7d")
    amount = int(match.group(1))
    unit = match.group(2)
    argument = {
        "s": "seconds",
        "m": "minutes",
        "h": "hours",
        "d": "days",
    }[unit]
    return timedelta(**{argument: amount})


def format_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_timestamp(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def read_state(cache_dir: Path) -> dict[str, Any] | None:
    path = cache_dir / "state.json"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as file:
        state = json.load(file)
    if state.get("version") != 1:
        raise RuntimeError(f"unsupported cache state in {path}")
    return state


def write_json_atomic(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(value, file, indent=2, sort_keys=True)
        file.write("\n")
    os.replace(temporary, path)


def search_url(api_url: str, repository: str, start: datetime, end: datetime) -> str:
    query = " ".join(
        (
            f"repo:{repository}",
            "is:issue",
            f"updated:{format_timestamp(start)}..{format_timestamp(end)}",
        )
    )
    parameters = urllib.parse.urlencode(
        {"q": query, "sort": "updated", "order": "asc", "per_page": 100}
    )
    return f"{api_url.rstrip('/')}/search/issues?{parameters}"


def next_link(headers: Any) -> str | None:
    links = {
        relation: url
        for url, relation in LINK_PATTERN.findall(headers.get("Link", ""))
    }
    return links.get("next")


def fetch_pages(
    url: str,
    destination: Path,
    token: str,
    opener: Any = urllib.request.urlopen,
) -> tuple[int, int]:
    page = 0
    total_count = 0
    while url:
        page += 1
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": "zaniebot-automations",
                "X-GitHub-Api-Version": API_VERSION,
            },
        )
        with opener(request) as response:
            body = response.read()
            (destination / f"page-{page:03d}.json").write_bytes(body)
            payload = json.loads(body)
            if page == 1:
                total_count = int(payload["total_count"])
                if total_count > 1000:
                    raise RuntimeError(
                        "delta contains more than GitHub Search's 1,000-result limit; "
                        "reduce the schedule interval or initial lookback"
                    )
            url = next_link(response.headers)
    return page, total_count


def collect(
    repository: str,
    cache_dir: Path,
    token: str,
    api_url: str,
    initial_lookback: timedelta,
    overlap: timedelta,
    now: datetime | None = None,
    opener: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    end = (now or utc_now()).astimezone(timezone.utc).replace(microsecond=0)
    state = read_state(cache_dir)
    if state and state.get("repository") != repository:
        raise RuntimeError(
            f"cache belongs to {state.get('repository')}, not {repository}"
        )
    start = (
        parse_timestamp(state["through"]) - overlap
        if state
        else end - initial_lookback
    )
    if start >= end:
        raise RuntimeError("delta start must be earlier than its end")

    run_id = end.strftime("%Y%m%dT%H%M%SZ")
    day = end.strftime("%Y/%m/%d")
    final_dir = cache_dir / "responses" / day / run_id
    temporary_dir = final_dir.with_name(f".{run_id}.tmp")
    if final_dir.exists() or temporary_dir.exists():
        raise RuntimeError(f"response directory already exists for {run_id}")

    temporary_dir.mkdir(parents=True)
    url = search_url(api_url, repository, start, end)
    try:
        pages, total_count = fetch_pages(url, temporary_dir, token, opener)
        manifest = {
            "api_version": API_VERSION,
            "fetched_at": format_timestamp(end),
            "pages": pages,
            "query_url": url,
            "repository": repository,
            "total_count": total_count,
            "window": {
                "end": format_timestamp(end),
                "start": format_timestamp(start),
            },
        }
        write_json_atomic(temporary_dir / "manifest.json", manifest)
        temporary_dir.rename(final_dir)
        write_json_atomic(
            cache_dir / "state.json",
            {
                "repository": repository,
                "through": format_timestamp(end),
                "version": 1,
            },
        )
    except Exception:
        shutil.rmtree(temporary_dir, ignore_errors=True)
        raise
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repository", required=True, help="owner/name to query")
    result.add_argument(
        "--cache-dir", type=Path, default=Path(".cache/github-issues")
    )
    result.add_argument(
        "--api-url", default=os.environ.get("GITHUB_API_URL", DEFAULT_API_URL)
    )
    result.add_argument("--initial-lookback", type=parse_duration, default="7d")
    result.add_argument("--overlap", type=parse_duration, default="1h")
    return result


def main() -> int:
    args = parser().parse_args()
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("GITHUB_TOKEN is required", file=sys.stderr)
        return 2
    try:
        manifest = collect(
            repository=args.repository,
            cache_dir=args.cache_dir,
            token=token,
            api_url=args.api_url,
            initial_lookback=args.initial_lookback,
            overlap=args.overlap,
        )
    except (RuntimeError, urllib.error.HTTPError, urllib.error.URLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(
        f"fetched {manifest['total_count']} issues in {manifest['pages']} page(s) "
        f"through {manifest['window']['end']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
