# PRSentinel Action

Add this to `.github/workflows/prsentinel.yml` in your public Python repository
(after adding the repository on the PRSentinel dashboard):

```yaml
name: prsentinel
on:
  pull_request:
  push:
    branches: [main]

permissions:
  id-token: write
  contents: read
  pull-requests: write

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: Ankxth/prsentinel/action@main
        with:
          api-url: https://YOUR-SERVICE.onrender.com
```

Notes
- Pull requests from forks are skipped (GitHub gives them no identity token).
- By default PRSentinel never fails your workflow; set `fail-on-error: "true"` to change that.
- The runner uses only Python's standard library, so nothing is installed on your runner.
