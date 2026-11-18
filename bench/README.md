# bench

Everything used to measure PRSentinel.

- `v1-capstone/` is the whole v1 project, frozen. Its tests guard the evaluation
  evidence (frozen prompts, the held-back set that was scored once, the saved
  reports), so do not edit its data or prompts. Read its `README.md`, `RESULTS.md`
  and `PROMPT_LOG.md` for what was measured and how.
- The backend must never read the held-back cases. Keep new benchmark data here,
  not in `backend/`.
