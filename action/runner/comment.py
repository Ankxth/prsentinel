"""Post or update the PRSentinel comment on a pull request. Standard library only."""
import client


def _comments_url(env, number):
    api = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    return "%s/repos/%s/issues/%s/comments" % (api, env["GITHUB_REPOSITORY"], number)


def post_or_update(env, token, number, marker, markdown):
    """Update the comment that contains `marker`, or create one. Returns 'created'/'updated'."""
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    base = _comments_url(env, number)
    page = 1
    while page <= 10:
        comments = client.request_json(
            "GET", "%s?per_page=100&page=%d" % (base, page), token=token, headers=headers,
            wait_seconds=10,
        ) or []
        for item in comments:
            if marker in (item.get("body") or ""):
                api = env.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
                url = "%s/repos/%s/issues/comments/%s" % (api, env["GITHUB_REPOSITORY"], item["id"])
                client.request_json("PATCH", url, token=token, body={"body": markdown},
                                    headers=headers, wait_seconds=10)
                return "updated"
        if len(comments) < 100:
            break
        page += 1
    client.request_json("POST", base, token=token, body={"body": markdown},
                        headers=headers, wait_seconds=10)
    return "created"
