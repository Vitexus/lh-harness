"""Classification boundaries for terminal agent-CLI failures."""

from datetime import datetime, timezone, timedelta
from lh_harness.provider_errors import classify_agent_runtime_failure, parse_quota_reset_delay
from lh_harness.types import EpisodeResult


def test_parse_quota_reset_delay_clock_time():
    # 2026-08-20 10:00:00 UTC
    now = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
    # Europe/Prague is UTC+2 in summer, so 12:20pm Europe/Prague is 10:20am UTC (1200 seconds later)
    msg = "Provider limit hit: your session limit resets 12:20pm (Europe/Prague)"
    delay = parse_quota_reset_delay(msg, now=now)
    assert delay is not None
    assert abs(delay - 1200.0) < 1.0


def test_parse_quota_reset_delay_relative_duration():
    now = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
    msg = "quota exceeded, resets in 45 minutes"
    delay = parse_quota_reset_delay(msg, now=now)
    assert delay == 2700.0

    msg_h_m = "retry in 1h 20m"
    delay_h_m = parse_quota_reset_delay(msg_h_m, now=now)
    assert delay_h_m == 4800.0


def test_parse_quota_reset_delay_iso():
    now = datetime(2026, 8, 20, 10, 0, 0, tzinfo=timezone.utc)
    msg = "limit reached, resets 2026-08-20T11:00:00Z"
    delay = parse_quota_reset_delay(msg, now=now)
    assert delay == 3600.0


def test_classify_agent_runtime_failure_extracts_reset_delay():
    msg = "Provider 额度或计费限制：You've hit your monthly spend limit · your session limit resets 12:20pm (Europe/Prague)"
    result = EpisodeResult(status="error", error=msg)
    failure = classify_agent_runtime_failure(result)
    assert failure is not None
    assert failure.kind == "quota"
    assert failure.reset_delay_seconds is not None
    assert failure.reset_delay_seconds > 0


def test_guard_snapshot_failure_is_not_a_runtime_failure():
    """A read-only-guard snapshot failure must fail the round, not the run.

    The guard rejects the audit fail-closed when it cannot inspect every
    workspace path (e.g. a build directory changing underneath the walk).
    That is a local audit-validity problem: the episode itself ran to
    completion, so classifying it as a provider failure — and aborting the
    whole run — turns a transient filesystem race into a fatal outcome.
    """

    result = EpisodeResult(
        status="error",
        error=(
            "Auditor workspace read-only guard could not inspect every path; "
            "the audit was rejected fail-closed."
        ),
        metadata={
            "verifier_workspace_snapshot_errors": [
                "target/debug/incremental/x.o: OSError: [Errno 9] Bad file descriptor"
            ]
        },
    )

    assert classify_agent_runtime_failure(result) is None


def test_plain_provider_error_is_still_terminal():
    """Genuine runtime failures keep their terminal classification."""

    result = EpisodeResult(status="error", error="connection reset by peer")

    failure = classify_agent_runtime_failure(result)
    assert failure is not None
    assert failure.abort_reason == "provider_network"


def _guard_rejected_result(**overrides):
    """An episode whose audit the guard rejected fail-closed."""

    from lh_harness.provider_errors import GUARD_REJECTION_MESSAGE

    fields = {
        "status": "error",
        "error": GUARD_REJECTION_MESSAGE,
        "metadata": {
            "verifier_workspace_snapshot_errors": [
                "target/debug/incremental/x.o: OSError: [Errno 9] Bad file descriptor"
            ]
        },
    }
    metadata_overrides = overrides.pop("metadata", {})
    fields.update(overrides)
    fields["metadata"] = {**fields["metadata"], **metadata_overrides}
    return EpisodeResult(**fields)


def test_guard_rejection_with_stderr_noise_is_still_round_level():
    """Harmless stderr from a healthy episode must not resurrect the abort."""

    result = _guard_rejected_result(
        metadata={"stderr_tail": "warning: unused variable `x`"}
    )

    assert classify_agent_runtime_failure(result) is None


def test_guard_rejection_does_not_hide_authentication_failure():
    """A terminal provider failure coexisting with snapshot errors stays terminal.

    Hiding it would make the manager retry until the round budget is
    exhausted instead of surfacing the real authentication problem.
    """

    from lh_harness.provider_errors import GUARD_REJECTION_MESSAGE

    result = _guard_rejected_result(
        error=f"401 Unauthorized: invalid api key\n{GUARD_REJECTION_MESSAGE}",
    )

    failure = classify_agent_runtime_failure(result)
    assert failure is not None
    assert failure.abort_reason == "provider_authentication"


def test_guard_rejection_does_not_hide_network_failure_in_stderr():
    result = _guard_rejected_result(
        metadata={"stderr_tail": "connection reset by peer"}
    )

    failure = classify_agent_runtime_failure(result)
    assert failure is not None
    assert failure.abort_reason == "provider_network"


def test_guard_rejection_does_not_hide_hard_runtime_signal():
    result = _guard_rejected_result(
        metadata={
            "runtime_signals": [
                {"signal": "AGENT_EXIT=1", "evidence": "AGENT_EXIT=1"}
            ]
        },
    )

    failure = classify_agent_runtime_failure(result)
    assert failure is not None
    assert failure.abort_reason == "provider_provider_error"


def test_guard_rejection_does_not_hide_episode_timeout():
    """A timed-out audited episode stays a timeout for manager recovery."""

    result = _guard_rejected_result(
        status="timeout",
        error="Episode timed out after 1800s",
    )

    failure = classify_agent_runtime_failure(result)
    assert failure is not None
    assert failure.abort_reason == "provider_timeout"


def test_guard_rejection_does_not_hide_agent_error_records():
    """Failure records in the actions log count as real evidence."""

    import json as _json

    result = _guard_rejected_result(
        actions_log=_json.dumps(
            {"type": "turn.failed", "error": {"message": "AGENT_TURN_FAILED"}}
        ),
    )

    failure = classify_agent_runtime_failure(result)
    assert failure is not None
    assert failure.abort_reason == "provider_provider_error"
