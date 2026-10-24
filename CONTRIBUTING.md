# Working on Sentinel

Start with the [development guide](docs/development.md) and [architecture](docs/architecture.md).

Before proposing a change:

1. Run `python -m pytest -q` and `npm --prefix cloudflare test`.
2. For frontend or replay-asset changes, run `npm --prefix cloudflare run build:assets`.
3. Keep provider calls mocked in tests. New fixtures should document provenance,
   clearly identify synthetic data, and exclude credentials and authorization headers.
4. Preserve replay behavior or explicitly explain the format/behavior change.
   Do not regenerate expected outputs merely to make a failing regression pass.
5. Describe the problem, resulting behavior, validation, and any limitations.

Keep credentials in the server environment. Do not include API keys, raw personal
logs, or secret values in issues or pull requests. Generated assets, local logs,
and environment files remain untracked.

Provider data is subject to its own terms; public source visibility does not
relicense third-party datasets. This repository does not currently specify a
software license.
