# Putting PRSentinel on a live GitHub repository

`repo/` is a tiny Python project made to be scored by PRSentinel, and
`prsentinel-workflow.yml` is the GitHub workflow that goes into its
`.github/workflows/` folder (step 2 copies it there). `changes/`
holds four edits to it, one per pull request. Three of them break something on
purpose and one is a safe refactor that should raise no alarm.

| folder in `changes/` | file | what the change does | what PRSentinel should say |
| --- | --- | --- | --- |
| `1_scores_off_by_one` | `scores.py` | slice starts one too early | tests point to a real bug |
| `2_cart_mutable_default` | `cart.py` | `cart=[]` default, shared between calls | tests point to a real bug, if a test calls it twice |
| `3_offers_wrong_sign` | `offers.py` | `min()` given a negated key, so it picks the biggest discount | tests point to a real bug |
| `4_textutils_safe_refactor` | `textutils.py` | loop rewritten as a one-line `join`, same behaviour | no test points to a real bug |

The tests are written fresh by the model every time, so the exact counts change
from run to run. "Should" above is the expected shape, not a promise.

All commands are for Git Bash, run from inside the PRSentinel folder unless the
step says otherwise. Replace `Ankxth` if your GitHub user name is different.

## 1. Create the demo repository on GitHub

On github.com: **New repository** -> name `prsentinel-demo` -> **Public** ->
leave "Add a README", ".gitignore" and "license" all **off** -> **Create**.

## 2. Push the demo project to it

```bash
cp -r demo_github/repo ../prsentinel-demo
mkdir -p ../prsentinel-demo/.github/workflows
cp demo_github/prsentinel-workflow.yml ../prsentinel-demo/.github/workflows/prsentinel.yml
cd ../prsentinel-demo
git init -b main
git add .
git commit -m "Demo project for PRSentinel"
git remote add origin https://github.com/Ankxth/prsentinel-demo.git
git push -u origin main
```

## 3. Give the demo repository the Groq key

In `prsentinel-demo` on github.com: **Settings -> Secrets and variables ->
Actions**.

- **Secrets** tab -> **New repository secret** -> name `GROQ_API_KEY`, value
  your Groq key.
- Only if your PRSentinel repository is **not** `Ankxth/prsentinel`:
  **Variables** tab -> **New repository variable** -> name `PRSENTINEL_REPO`,
  value `owner/name` of it.
- Only if your PRSentinel repository is **private**: make a fine-grained
  personal access token (GitHub -> Settings -> Developer settings) with
  read-only **Contents** access to that one repository, and save it as a
  secret named `PRSENTINEL_READ_TOKEN`.

## 4. Open the four pull requests

Still inside `prsentinel-demo`:

```bash
git checkout -b simplify-recent-scores
cp ../prsentinel/demo_github/changes/1_scores_off_by_one/scores.py scores.py
git commit -am "Simplify get_recent_scores"
git push -u origin simplify-recent-scores
git checkout main

git checkout -b cart-default-argument
cp ../prsentinel/demo_github/changes/2_cart_mutable_default/cart.py cart.py
git commit -am "Use a list default for the cart"
git push -u origin cart-default-argument
git checkout main

git checkout -b tidy-best-offer
cp ../prsentinel/demo_github/changes/3_offers_wrong_sign/offers.py offers.py
git commit -am "Tidy best_offer"
git push -u origin tidy-best-offer
git checkout main

git checkout -b refactor-initials
cp ../prsentinel/demo_github/changes/4_textutils_safe_refactor/textutils.py textutils.py
git commit -am "Shorter initials()"
git push -u origin refactor-initials
git checkout main
```

Each `git push` prints a line like
`Create a pull request for '...' on GitHub by visiting: https://github.com/...`.
Open each link and press **Create pull request**. (Or, if you have the GitHub
CLI: `gh pr create --fill --head <branch name>`.)

## 5. Watch it work

Open the **Actions** tab of `prsentinel-demo`. One "PRSentinel" run starts per
pull request and takes about 3 to 5 minutes. When it finishes, the pull request
has a comment from `github-actions` that starts with:

> Tests are AI-generated. A catching test is a hint, not proof.

The full reports are attached to the run as an artifact called
`prsentinel-reports-...`.

## 6. The same pull requests in the web app

```bash
cd ../prsentinel
source venv/Scripts/activate
python -m prsentinel.app --port 8000
```

Open http://127.0.0.1:8000, choose **A GitHub pull request or commit...**,
paste for example `https://github.com/Ankxth/prsentinel-demo/pull/1`, and press
Run. The page shows one report per changed file.

## If something goes wrong

| what you see | what it means |
| --- | --- |
| The Actions run fails at "Get PRSentinel" | The PRSentinel repository name is wrong (set `PRSENTINEL_REPO`) or it is private (add `PRSENTINEL_READ_TOKEN`). |
| The run fails at "Run PRSentinel on each file" with a Groq error | The `GROQ_API_KEY` secret is missing or wrong. |
| No comment appears, and the log says "Resource not accessible by integration" | Settings -> Actions -> General -> Workflow permissions -> "Read and write permissions". |
| The web app says GitHub's limit is used up | 60 GitHub API calls an hour without a login, 2 per link. Add a `GITHUB_TOKEN` to `.env`. |
| The web app says a file "could not be scored", or every test errors | That file imports something that is not in the file itself. PRSentinel tests one file at a time. |
