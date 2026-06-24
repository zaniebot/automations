# automations

Small scheduled data-collection jobs.

## GitHub issue deltas

[`cache-issue-deltas.yml`](.github/workflows/cache-issue-deltas.yml) runs every
15 minutes and fetches issues updated in `astral-sh/uv`. Each GitHub Search API
response body is kept byte-for-byte as returned by the API.

The collector restores its previous state from a GitHub Actions cache, fetches
an overlapping update window, and saves the expanded directory under a new
cache key. After a successful save, the workflow removes the superseded cache
entry so each run does not retain another complete copy of the history.

The cache layout is:

```text
.cache/github-issues/
├── state.json
└── responses/
    └── 2026/06/24/20260624T120000Z/
        ├── manifest.json
        ├── page-001.json
        └── page-002.json
```

`state.json` records the end of the last completed window. The next run starts
one hour before that timestamp to tolerate GitHub Search indexing delays.
Consumers should therefore deduplicate items by `id` and `updated_at`.

On an empty cache, the collector seeds the previous seven days. Both values can
be changed with `--initial-lookback` and `--overlap` when running the script.

Run it locally with a GitHub token:

```console
GITHUB_TOKEN=... python3 scripts/fetch_issues.py --repository astral-sh/uv
```

GitHub Actions caches are an optimization mechanism, not durable storage. They
can be evicted by GitHub, in which case the next run starts a fresh seven-day
seed window.
