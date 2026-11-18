# API contract: Action (runner) and server

`contract_version`: **1**

This file is the written agreement between the code that runs inside the user's GitHub Action
(`action/`) and the server (`backend/`). Both sides must follow it. Owner: feature B. Features D and E
add their endpoints here when they build them.

Rules
- Additive changes (new optional fields, new endpoints) keep the version. A breaking change bumps
  `contract_version`, and the server answers 400 to an Action that sends an older one.
- The server never trusts the request body for identity. Repository, commit and event come from
  GitHub's signed token.
- All bodies are JSON. All times are UTC (ISO 8601).
- Errors look like `{"detail": "plain sentence"}`. The Action should show `detail` to the user.

Status codes the Action must handle
| Code | Meaning | What the Action does |
|---|---|---|
| 200/201 | ok | continue |
| 400 | contract version mismatch | stop, print "update the Action" |
| 401 | token missing, expired or invalid | stop, print `detail` |
| 403 | repo not added, or not public | stop, print `detail` (tell the user to add the repo) |
| 409 | run already finished | stop quietly |
| 422 | bad body, or event not analysed | stop, print `detail` |
| 502/503/504, network error | server asleep or busy (free hosting wakes in about a minute) | retry with waiting, up to about 2 minutes |

## Authentication

Two kinds of token, both sent as `Authorization: Bearer <token>`.

1. **GitHub identity token** (only for `POST /v1/runs`). The Action requests it from GitHub with the
   audience `prsentinel`. The workflow needs `permissions: id-token: write`. The server checks the
   signature, issuer `https://token.actions.githubusercontent.com`, audience, expiry, and that
   `repository_visibility` is `public`. It finds the repo by `repository_id`.
2. **Run token** (every call after the run starts). Returned by `POST /v1/runs`, valid for one run,
   `expires_in` seconds (default 3600).

## Endpoints

### `POST /v1/runs` (auth: GitHub identity token)

Starts a run. Calling it again for the same GitHub run and attempt returns the same `run_id` with
status 200 and a fresh token.

Request
```json
{
  "contract_version": 1,
  "changed_files": 2,
  "changed_functions": [
    {"file": "app/a.py", "name": "add", "change_type": "modified"},
    {"file": "app/b.py", "name": "Cart.total", "change_type": "added"}
  ]
}
```
- `change_type`: `added` | `modified` | `deleted`
- at most 500 functions; `limits.max_functions` in the response says how many the server wants
  tested (the Action should send everything it found up to 500 and test no more than the limit)

Response `201` (or `200` if it already existed)
```json
{
  "contract_version": 1,
  "run_id": "uuid",
  "run_token": "jwt",
  "expires_in": 3600,
  "repo": "owner/name",
  "limits": {"max_functions": 50}
}
```
Only these events are analysed: `push`, `pull_request`, `workflow_dispatch`. Anything else is 422.
Which branches trigger is decided in the user's workflow file, not by the server.

### `POST /v1/runs/{run_id}/finish` (auth: run token)

Ends the run and returns the PR comment text.

Request
```json
{"status": "completed", "reason": null}
```
- `status`: `completed` | `failed` | `could_not_run`
- `reason`: short plain sentence, required in practice for `failed` and `could_not_run` (max 300 chars)
- `could_not_run` means nothing was checked (for example dependency install failed). It is **never**
  shown as a pass.

Response
```json
{
  "status": "completed",
  "comment_marker": "<!-- prsentinel-comment -->",
  "comment_markdown": "<!-- prsentinel-comment -->\n### PRSentinel\n..."
}
```
The Action posts `comment_markdown` on the pull request. If a comment containing `comment_marker`
already exists, it **updates that comment** instead of adding a new one.

### Reserved for other features (not built yet)

| Endpoint | Owner | Purpose |
|---|---|---|
| `POST /v1/runs/{run_id}/generate` | D | tests for one function (context-aware) |
| `POST /v1/runs/{run_id}/results` | D, E | upload test results and scanner output |
| `POST /v1/runs/{run_id}/findings/{fid}/agent/step` | E | proof-agent ping-pong |

All of them authenticate with `Depends(current_run)` (see `backend/app/core/run_token.py`), which
returns the `Run` row only if the caller holds that run's token. Add each endpoint to this file with
its request and response shapes when you build it.

## Limits

- A run token lasts one hour. The Action should finish a run well before that.
- Request bodies are small by design. Upload source text only for the functions being tested, not
  whole repositories.

## Dashboard endpoints (browser only, not for the Action)

`GET /v1/repos/{id}/runs` (latest 50) and `GET /v1/runs/{id}`. Auth is the user's Supabase sign-in,
and only the owner's runs are visible (others get 404).
