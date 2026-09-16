"""Classify terminal agent-CLI failures into operator-facing reasons."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore

from .runtime_signals import hard_signal_labels
from .types import EpisodeResult


# The exact sentence the Claude Code adapter appends when the read-only guard
# rejects an audit fail-closed. The classifier strips it from failure evidence
# so a guard-only rejection stays a round-level problem while any coexisting
# provider failure keeps its terminal classification.
GUARD_REJECTION_MESSAGE = (
    "Auditor workspace read-only guard could not inspect every path; "
    "the audit was rejected fail-closed."
)


_TZ_ABBREVS: dict[str, timezone | timedelta] = {
    "UTC": timezone.utc,
    "GMT": timezone.utc,
    "Z": timezone.utc,
    "PST": timezone(timedelta(hours=-8)),
    "PDT": timezone(timedelta(hours=-7)),
    "EST": timezone(timedelta(hours=-5)),
    "EDT": timezone(timedelta(hours=-4)),
    "CST": timezone(timedelta(hours=-6)),
    "CDT": timezone(timedelta(hours=-5)),
    "MST": timezone(timedelta(hours=-7)),
    "MDT": timezone(timedelta(hours=-6)),
    "CET": timezone(timedelta(hours=1)),
    "CEST": timezone(timedelta(hours=2)),
}

_CLOCK_TIME_PATTERN = re.compile(
    r"(?:resets?|retry|reset|until|limit resets)\s+(?:at\s+)?(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(am|pm)?(?:\s*\(([^)]+)\)|\s+([A-Za-z_/+]+))?",
    re.I,
)

_DURATION_PATTERN = re.compile(
    r"(?:resets?|retry|try again|wait)\s+(?:in|after)\s+((?:\d+\s*(?:h|hr|hours?|m|min|minutes?|s|sec|seconds?)\s*)+)",
    re.I,
)

_ISO_PATTERN = re.compile(
    r"\b(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\b",
    re.I,
)


def _parse_tz(tz_str: str | None) -> timezone | ZoneInfo | None:
    if not tz_str:
        return None
    tz_clean = tz_str.strip()
    if tz_clean.upper() in _TZ_ABBREVS:
        return _TZ_ABBREVS[tz_clean.upper()]
    if ZoneInfo is not None:
        try:
            return ZoneInfo(tz_clean)
        except Exception:
            pass
    return None


def parse_quota_reset_delay(message: str, now: datetime | None = None) -> float | None:
    """Extract quota/rate limit reset delay in seconds from provider error messages.

    Supports clock times with timezones (e.g. `resets 12:20pm (Europe/Prague)`),
    relative durations (e.g. `resets in 45 minutes`), and ISO timestamps.
    Returns delay in seconds if parsed, otherwise None.
    """
    if not message:
        return None

    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # 1. Clock time match (e.g., resets 12:20pm (Europe/Prague))
    clock_match = _CLOCK_TIME_PATTERN.search(message)
    if clock_match:
        hour = int(clock_match.group(1))
        minute = int(clock_match.group(2))
        second = int(clock_match.group(3)) if clock_match.group(3) else 0
        ampm = clock_match.group(4).lower() if clock_match.group(4) else None
        tz_str = clock_match.group(5) or clock_match.group(6)

        if ampm:
            if ampm == "pm" and hour < 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0

        tz = _parse_tz(tz_str)
        now_in_tz = now.astimezone(tz) if tz is not None else now
        target_dt = now_in_tz.replace(hour=hour, minute=minute, second=second, microsecond=0)

        # If target_dt is in the past by > 60s, assume it's tomorrow
        if target_dt <= now_in_tz - timedelta(seconds=60):
            target_dt += timedelta(days=1)

        delay = (target_dt - now_in_tz).total_seconds()
        return max(0.0, delay)

    # 2. Relative duration match (e.g., resets in 45 minutes, retry in 1h20m)
    dur_match = _DURATION_PATTERN.search(message)
    if dur_match:
        dur_str = dur_match.group(1)
        units = re.findall(r"(\d+)\s*(h|hr|hours?|m|min|minutes?|s|sec|seconds?)", dur_str, re.I)
        if units:
            total_seconds = 0.0
            for val_str, unit in units:
                val = float(val_str)
                unit_lower = unit.lower()
                if unit_lower.startswith("h"):
                    total_seconds += val * 3600
                elif unit_lower.startswith("m"):
                    total_seconds += val * 60
                elif unit_lower.startswith("s"):
                    total_seconds += val
            return max(0.0, total_seconds)

    # 3. ISO timestamp match
    iso_match = _ISO_PATTERN.search(message)
    if iso_match:
        iso_str = iso_match.group(1)
        try:
            target_dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
            if target_dt.tzinfo is None:
                target_dt = target_dt.replace(tzinfo=timezone.utc)
            delay = (target_dt - now).total_seconds()
            return max(0.0, delay)
        except Exception:
            pass

    return None


@dataclass(frozen=True)
class AgentRuntimeFailure:
    kind: str
    abort_reason: str
    message: str
    user_message: str
    reset_delay_seconds: float | None = None


_CLASSIFIERS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "model_unavailable",
        re.compile(
            r"(?:model.{0,80}(?:not supported|unsupported|not found|does not exist|unavailable|invalid|not available|access denied|no access)|"
            r"(?:not supported|unsupported|not found|does not exist|unavailable|invalid).{0,80}model|"
            r"模型.{0,40}(?:不支持|不存在|不可用|无权限|无访问权限|无效))",
            re.I | re.S,
        ),
        "模型不可用",
    ),
    (
        "authentication",
        re.compile(
            r"(?:\b401\b|unauthori[sz]ed|not logged in|login required|authentication (?:failed|required)|"
            r"invalid (?:api[ _-]?key|auth|token)|missing (?:api[ _-]?key|auth|token)|oauth.{0,40}(?:expired|invalid)|"
            r"(?:无效|缺少|过期).{0,16}(?:api\s*key|密钥|令牌|凭据)|未登录|需要登录|请.{0,8}登录)",
            re.I | re.S,
        ),
        "Provider 登录或凭据无效",
    ),
    (
        "quota",
        re.compile(
            r"(?:insufficient[_ -]?quota|quota exceeded|credit balance|billing.{0,40}(?:required|disabled|limit)|"
            r"spend limit|usage limit|额度(?:不足|已用尽|超限)|计费.{0,20}(?:限制|禁用))",
            re.I | re.S,
        ),
        "Provider 额度或计费限制",
    ),
    (
        "rate_limit",
        re.compile(r"(?:\b429\b|rate[ _-]?limit|too many requests|overloaded|请求过多|限流|过载)", re.I),
        "Provider 限流或过载",
    ),
    (
        "network",
        re.compile(
            r"(?:connection (?:error|failed|reset|closed)|stream disconnected|network (?:error|unreachable)|"
            r"timed? out|dns|name resolution|tls|certificate)",
            re.I,
        ),
        "Provider 网络连接失败",
    ),
)


def classify_agent_runtime_failure(result: EpisodeResult) -> AgentRuntimeFailure | None:
    """Return a failure only when the agent runtime itself failed.

    Tool commands run by an otherwise healthy Executor may fail as part of the
    task and must remain auditable task evidence.  We therefore require a
    non-success episode status or a normalized hard runtime signal. Local
    episode timeouts are classified separately so the manager can recover;
    genuine provider failures remain terminal at the caller.
    """

    metadata = result.metadata if isinstance(result.metadata, dict) else {}
    hard_signals = hard_signal_labels(metadata.get("runtime_signals"))
    if result.status not in {"error", "timeout"} and not hard_signals:
        return None
    candidates = _failure_messages(result, metadata)
    guard_rejected = bool(metadata.get("verifier_workspace_snapshot_errors"))
    if guard_rejected:
        # The guard's fail-closed rejection sentence is local bookkeeping,
        # not provider evidence; classify only what remains so a coexisting
        # authentication/network/quota failure keeps its terminal kind.
        candidates = _strip_guard_rejection(candidates)
    combined = "\n".join(candidates)
    kind = "timeout" if result.status == "timeout" else "provider_error"
    label = "Agent 执行超时" if kind == "timeout" else "Agent provider 启动或运行失败"
    # A command episode that reaches its harness budget is a local timeout, not
    # evidence that the provider connection failed. In particular, the
    # adapter's own "Episode timed out after ..." message matches the generic
    # network classifier below. Keep the explicit status authoritative so the
    # manager can recover from the real workspace in a later round.
    matched_provider_kind = False
    if result.status != "timeout":
        for candidate_kind, pattern, candidate_label in _CLASSIFIERS:
            if pattern.search(combined):
                kind = candidate_kind
                label = candidate_label
                matched_provider_kind = True
                break
    # Downgrade to a round-level failure only when the failure is proven to
    # be caused solely by the snapshot guard: the guard rejected the audit,
    # nothing matched a provider classifier, no hard runtime signal fired,
    # the episode did not time out, and the episode's own failure channels
    # (actions log, error field) carry nothing beyond the guard rejection.
    # The audit is already rejected fail-closed by the adapter, so the round
    # fails and is retried instead of the whole run aborting over a transient
    # filesystem race, e.g. a build directory churning underneath the walk.
    if (
        guard_rejected
        and not matched_provider_kind
        and not hard_signals
        and result.status != "timeout"
        and not _non_guard_failure_evidence(result)
    ):
        return None
    message = next((item for item in candidates if _specific_message(item)), None)
    message = message or next(iter(candidates), "agent runtime failed")
    message = _clean(message, 1200)
    reset_delay = parse_quota_reset_delay(combined) if kind in {"quota", "rate_limit"} else None
    return AgentRuntimeFailure(
        kind=kind,
        abort_reason=f"provider_{kind}",
        message=message,
        user_message=f"{label}：{message}",
        reset_delay_seconds=reset_delay,
    )


def _strip_guard_rejection(candidates: list[str]) -> list[str]:
    """Remove the guard's own rejection sentence, keeping any other evidence."""

    stripped: list[str] = []
    for item in candidates:
        text = item.replace(GUARD_REJECTION_MESSAGE, " ")
        text = " ".join(text.split())
        if text and text not in stripped:
            stripped.append(text)
    return stripped


def _non_guard_failure_evidence(result: EpisodeResult) -> bool:
    """True when the episode's failure channels carry more than the guard.

    Looks only at channels that are silent on a successful episode (failure
    records in the actions log and the episode error field), so stderr noise
    from a healthy run cannot escalate a guard-only rejection back into a
    terminal provider failure.
    """

    values: list[str] = []
    for record in _json_records(result.actions_log):
        record_type = str(record.get("type") or "")
        if record_type == "turn.failed":
            error = record.get("error")
            _append(values, error.get("message") if isinstance(error, dict) else error)
        elif record_type == "error":
            _append(values, record.get("message") or record.get("error"))
        elif record_type == "result" and record.get("is_error"):
            _append(values, record.get("result") or record.get("error") or record.get("subtype"))
    _append(values, result.error)
    for value in values:
        text = _clean(value, 2000).replace(GUARD_REJECTION_MESSAGE, " ").strip()
        if text:
            return True
    return False


def _failure_messages(result: EpisodeResult, metadata: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for record in _json_records(result.actions_log):
        record_type = str(record.get("type") or "")
        if record_type == "turn.failed":
            error = record.get("error")
            _append(values, error.get("message") if isinstance(error, dict) else error)
        elif record_type == "error":
            _append(values, record.get("message") or record.get("error"))
        elif record_type == "result" and record.get("is_error"):
            _append(values, record.get("result") or record.get("error") or record.get("subtype"))
    _append(values, result.error)
    _append(values, metadata.get("stderr_tail"))
    signals = metadata.get("runtime_signals")
    if isinstance(signals, list):
        for item in signals:
            if isinstance(item, dict):
                _append(values, item.get("evidence") or item.get("signal"))
            else:
                _append(values, item)
    deduped: list[str] = []
    for value in values:
        cleaned = _clean(value, 2000)
        if cleaned and cleaned not in deduped:
            deduped.append(cleaned)
    return deduped


def _json_records(raw: str):
    for line in str(raw or "").splitlines():
        if not line.lstrip().startswith("{"):
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _append(target: list[str], value: object) -> None:
    message = _unwrap_provider_message(value)
    if message:
        target.append(message)


def _unwrap_provider_message(value: object, *, depth: int = 0) -> str:
    """Extract the useful sentence from provider errors wrapped as JSON strings."""

    if depth >= 5 or value is None:
        return ""
    if isinstance(value, dict):
        # Providers wrap the readable sentence in different keys: OpenAI/Claude
        # use {"error": {"message": ...}}, OpenCode uses {"name": ..., "data":
        # {"message": ...}}, FastAPI uses {"detail": ...}.
        for key in ("message", "detail", "data", "error"):
            if key in value:
                message = _unwrap_provider_message(value.get(key), depth=depth + 1)
                if message:
                    return message
        return ""
    if isinstance(value, list):
        for item in value:
            message = _unwrap_provider_message(item, depth=depth + 1)
            if message:
                return message
        return ""
    if not isinstance(value, str):
        return str(value).strip()

    text = value.strip()
    if text[:1] in {"{", "["}:
        try:
            decoded = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            message = _unwrap_provider_message(decoded, depth=depth + 1)
            if message:
                return message
    return text


def _specific_message(value: str) -> bool:
    text = value.strip()
    return bool(text) and text not in {"AGENT_TURN_FAILED", "response.failed", "Connection error."}


_SECRET_VALUE = re.compile(
    r"(?i)(\b(?:api[_-]?key|auth[_-]?token|access[_-]?token|password|secret)\s*[=:]\s*)\S+"
)


def _clean(value: object, limit: int) -> str:
    text = " ".join(_unwrap_provider_message(value).split())
    text = _SECRET_VALUE.sub(r"\1***REDACTED***", text)
    return text[:limit]
