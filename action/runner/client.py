"""Talk to the PRSentinel server and to GitHub. Standard library only."""
import json
import time
import urllib.error
import urllib.request

RETRY_STATUSES = (502, 503, 504)


class ApiError(Exception):
    """The server (or GitHub) answered with an error we should not retry."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _read_error(err):
    try:
        body = json.loads(err.read().decode("utf-8", errors="replace"))
        if isinstance(body, dict) and body.get("detail"):
            detail = body["detail"]
            return detail if isinstance(detail, str) else json.dumps(detail)
        if isinstance(body, dict) and body.get("message"):
            return str(body["message"])
    except Exception:
        pass
    return "HTTP %s" % err.code


def request_json(method, url, token=None, body=None, headers=None, wait_seconds=0,
                 retry_pause=5, sleep=time.sleep, timeout=30):
    """Send one JSON request. Retries while the server is asleep (502/503/504/network errors)
    for up to `wait_seconds`. Returns the decoded JSON (or None for empty replies).
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    deadline = time.monotonic() + wait_seconds
    while True:
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Accept", "application/json")
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", "Bearer " + token)
        for key, value in (headers or {}).items():
            req.add_header(key, value)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as err:
            if err.code in RETRY_STATUSES and time.monotonic() < deadline:
                sleep(retry_pause)
                continue
            if err.code in RETRY_STATUSES:
                raise ApiError(err.code, "The server did not wake up in time (HTTP %s)." % err.code)
            raise ApiError(err.code, _read_error(err))
        except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as err:
            if time.monotonic() < deadline:
                sleep(retry_pause)
                continue
            raise ApiError(0, "Could not reach the server: %s" % err)


def fetch_oidc_token(env, audience, **kwargs):
    """Ask GitHub for this job's identity token. Needs `permissions: id-token: write`."""
    url = env.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    bearer = env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not bearer:
        raise ApiError(0, "GitHub did not give this job an identity token. Add "
                          "'permissions: id-token: write' to the workflow (or job).")
    sep = "&" if "?" in url else "?"
    reply = request_json("GET", url + sep + "audience=" + audience, token=bearer, **kwargs)
    if not reply or not reply.get("value"):
        raise ApiError(0, "GitHub returned an empty identity token.")
    return reply["value"]
