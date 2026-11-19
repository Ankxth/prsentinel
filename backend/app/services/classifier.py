"""Labels and the rule classifier for generated tests (feature D). Plain Python, no AI.

Ported from the frozen v1 code (`test_runner.label_for`, `classifier.rule_classify`) with the
same decisions, so the v1 behaviour carries over.

Two separate ideas:

* A LABEL says what a test did on the old and the new code (CATCHES_CHANGE, ...).
* A DIAGNOSIS says why a test that fails on the new code failed (REAL_BUG, BAD_TEST, FLAKY).

Test outcomes arrive from the runner, which is not trusted: anything unknown is treated as
an error rather than a pass.
"""

# Per-test results the runner reports.
PASSED = "passed"
FAILED = "failed"
TEST_ERROR = "error"
SKIPPED = "skipped"
RESULTS = (PASSED, FAILED, TEST_ERROR, SKIPPED)

# Labels (before = old code, after = new code).
CATCHES_CHANGE = "CATCHES_CHANGE"  # passes on old, fails on new
TEST_WRONG_ON_BEFORE = "TEST_WRONG_ON_BEFORE"  # fails on both
NO_SIGNAL = "NO_SIGNAL"  # passes on both
ODD = "ODD"  # fails on old, passes on new
LABELS = (CATCHES_CHANGE, TEST_WRONG_ON_BEFORE, NO_SIGNAL, ODD)

# Diagnoses.
REAL_BUG = "REAL_BUG"
BAD_TEST = "BAD_TEST"
FLAKY = "FLAKY"
DIAGNOSES = (REAL_BUG, BAD_TEST, FLAKY)

_FAILURES = (FAILED, TEST_ERROR)


def normalize_result(value: object) -> str:
    """Anything that is not a known result counts as an error, never as a pass."""
    return value if value in RESULTS else TEST_ERROR


def label_for(before_result: str, after_result: str) -> str:
    """An error counts as a failure; a skipped test never counts as one."""
    before_failed = normalize_result(before_result) in _FAILURES
    after_failed = normalize_result(after_result) in _FAILURES
    if before_failed and after_failed:
        return TEST_WRONG_ON_BEFORE
    if before_failed:
        return ODD
    if after_failed:
        return CATCHES_CHANGE
    return NO_SIGNAL


def fails_on_new_code(after_result: str) -> bool:
    return normalize_result(after_result) in _FAILURES


def rule_classify(
    before_result: str, after_result: str, rerun_passes: int = 0, rerun_fails: int = 0
) -> dict:
    """Decide REAL_BUG / BAD_TEST / FLAKY from the results alone. Returns {"diagnosis", "reason"}.

    Order matters: a result that changes between identical runs says nothing else, so flakiness
    is checked first and wins.
    """
    before = normalize_result(before_result)
    after = normalize_result(after_result)

    if rerun_passes and rerun_fails:
        return {
            "diagnosis": FLAKY,
            "reason": (
                f"Running it again gave {rerun_passes} passes and {rerun_fails} failures, "
                "so the result depends on chance rather than on the code."
            ),
        }
    if before in _FAILURES:
        return {
            "diagnosis": BAD_TEST,
            "reason": (
                f"It failed on the old code as well ({before}), so the test is not agreeing "
                "with code that was already working."
            ),
        }
    if before == PASSED and after in _FAILURES:
        return {
            "diagnosis": REAL_BUG,
            "reason": (
                f"It passed on the old code and fails on the new one ({after}), "
                "so the change is what broke it."
            ),
        }
    return {
        "diagnosis": BAD_TEST,
        "reason": (
            f"Nothing in the results was conclusive (before={before}, after={after}), "
            "so this falls back to looking at the test."
        ),
    }
