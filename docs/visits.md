# Privacy-safe visit analytics

[← Project overview](../README.md) · [Deployment](deployment.md)

## View the dashboard

1. In Render, open **sentinel-live → Environment**. Add `SENTINEL_ADMIN_TOKEN`
   with a long random value from your password manager (at least 32 random bytes
   recommended). Save and redeploy. Never commit the token.
2. Open [the visits page](https://sentinel-live-2nt0.onrender.com/admin/visits).
3. Paste the same value into **Admin token** and click **Load / refresh**.

The login shell is public; analytics are fetched only after a constant-time token
check at `/admin/visits/data`. With no configured token, access is disabled.
The token is sent as `Authorization: Bearer ...`, never in a URL, cookie, or
local/session storage. It stays in the current password field until the page is
closed or reloaded. Responses use `Cache-Control: no-store`. Reload the page to
clear the dashboard. Refresh is manual, not background polling. Rotate the server
environment value to revoke the old token.

## What is recorded

One structured JSON line with `event: "visit_session"` is emitted at the end of
each accepted WebSocket session, including sessions immediately rejected by the
budget or connection cap. It contains:

| Field | Meaning |
| --- | --- |
| `started_at` | ISO timestamp in `America/Los_Angeles`, including the PST/PDT offset |
| `duration_seconds` | Monotonic elapsed connection time |
| `bytes_sent` | Successfully handed-off uncompressed UTF-8 application payload bytes, including app pongs; not TLS, framing, or HTTP bytes |
| `end_reason` | `normal_close`, `hidden_tab`, `idle_timeout`, `session_cap`, `daily_budget`, `connection_limit`, `connection_lost`, `client_stale_timeout`, `client_protocol_error`, `resume_expired`, or `server_shutdown` |
| `referrer` | Coarse label derived from the **page load's HTTP Referer** |
| `client_type` | `desktop_browser`, `mobile_browser`, `known_bot_crawler`, or `other_client` |
| `day` | Pacific session-start date used for daily totals |

The budget reserves bytes before writes; analytics count only successful writes,
so totals may differ after a failed send. Delivery to the remote browser cannot
be proven merely because the server write completed. Daily totals count completed
sessions, bytes, and duration, assigning the whole session to its start day.
Reconnects are separate sessions, **not unique visitors**. Active sessions appear
when they end. A force-killed process cannot emit a final record; there is no claim
of complete crash-proof accounting.

Where available, `close_code` and an allowlisted `transport_error` exception class
are recorded to diagnose disconnects. No raw exception message or close reason is stored.

The browser uses distinct close codes for hidden-tab and idle pauses. Those
reasons are client-reported and spoofable; an abrupt network/browser failure may
appear as `connection_lost`. Server-enforced idle and session deadlines are also
recorded. User-agent classification is heuristic, not bot authentication.

## Referrer privacy and limits

The page handler reduces the header to an allowlisted label (`linkedin.com`,
`github.com`, `google.com`, `bing.com`, `duckduckgo.com`, or `t.co`), `pdf_document`,
`other_website`, or `direct_or_unavailable`/`unavailable`. It never stores URL paths,
queries, arbitrary hostnames, or raw headers. A `.pdf` path can yield the PDF label,
but many PDF viewers and privacy settings suppress referrers: **direct and an
unreported PDF referral cannot reliably be distinguished**.

A signed label is embedded in that page and carried in the socket's `ref` parameter.
It is shared by all pages with the same label in a process, not a visitor ID or
secret admin token. Each tab keeps its own original attribution across reconnects.
Unsigned, invalid, or old-process labels become `direct_or_unavailable`. This does
not prove that a visit genuinely originated from that source: HTTP headers can be
spoofed. No cookies, IP addresses, salted IP hashes, device fingerprints, full user
agents, or analytics visitor identifiers are created. A separate, random reconnect token
is held in tab session storage and bounded server memory for up to about 31 minutes
to preserve idle/session deadlines across socket reconnects; it is never included
in visit records. It is not an analytics identity or a cross-tab fingerprint. There is no third-party analytics
script or external analytics request.

Normal Uvicorn HTTP access lines and WebSocket handshake lines are suppressed
because they include client addresses. This does not control Render's own network
logs or other infrastructure logs. Keep server debug logging off.

## Storage and Render logs

The dashboard keeps the last **50 completed sessions** and **90 daily buckets**.
It atomically saves them to `visits.json` beside the raw log. Optionally set
`SENTINEL_VISITS_PATH` to choose a different local path. It restores that file
when available; storage failure does not interrupt the live map or structured
logging. Run one worker, as with Sentinel's collector and daily byte budget.

Render Free has an ephemeral filesystem and does not support persistent disks;
local data can disappear on redeploy, restart, or spin-down. Its free Postgres
option expires after 30 days, so this feature does not add a temporary database
or billing dependency. [Render free-service storage limits](https://render.com/docs/free).

For records outside the current instance, open **sentinel-live → Logs** in Render
and search for **`visit_session`**. Structured stdout logs remain available according
to the workspace's log-retention policy; they are not an indefinite archive and
are not automatically re-imported into the dashboard.
[Render log explorer and retention](https://render.com/docs/logging).
