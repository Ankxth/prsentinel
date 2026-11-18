"""Keep the server's code map of this repository up to date (feature C, step C2b).

Three calls, all described in docs/api-contract.md:
  1. manifest: send {path: hash} for every Python file; the server says which files it needs
  2. files:    send facts (never source bodies) for just those files, in small batches
  3. commit:   tell the server the map is complete

Standard library only. Errors are raised as client.ApiError and handled by the caller, which
must never let a map problem fail the user's run.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import client  # noqa: E402
import facts  # noqa: E402

MAX_BATCH_BYTES = 1_500_000  # the server accepts 2 MB per request; stay well below


def _batches(items, max_files):
    """Group fact dicts so a batch has at most max_files files and about MAX_BATCH_BYTES of JSON."""
    batch, size = [], 0
    for item in items:
        item_size = len(json.dumps(item))
        if batch and (len(batch) >= max_files or size + item_size > MAX_BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(item)
        size += item_size
    if batch:
        yield batch


def sync(api_url, run_id, run_token, root, **kwargs):
    """Update the map. Returns a short plain sentence describing what happened."""
    base = "%s/v1/runs/%s/context" % (api_url, run_id)
    scan = facts.scan_repo(root)
    reply = client.request_json(
        "POST", base + "/manifest", token=run_token,
        body={"files": scan.manifest, "total_files": scan.total_files,
              "skipped_files": len(scan.skipped)}, **kwargs)
    need = list(reply.get("need") or [])
    max_files = int(reply.get("max_batch_files") or 100)

    uploaded = 0
    for batch in _batches(facts.collect_facts(root, need), max_files):
        client.request_json("POST", base + "/files", token=run_token,
                            body={"files": batch}, **kwargs)
        uploaded += len(batch)

    done = client.request_json("POST", base + "/commit", token=run_token,
                               body=facts.config_hints(root), **kwargs)
    text = "updated (%d file(s) uploaded, %d unchanged" % (uploaded, reply.get("unchanged", 0))
    if reply.get("removed"):
        text += ", %d removed" % reply["removed"]
    text += ")"
    if done and done.get("partial"):
        text += "; partial: %d file(s) left out because of size limits" % len(scan.skipped)
    return text
