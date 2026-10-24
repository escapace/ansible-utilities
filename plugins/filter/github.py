"""Side-effect-free retry decisions for GitHub GET requests."""

from collections.abc import Mapping
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from math import ceil, isfinite
import re

from ansible.errors import AnsibleFilterError

_TRANSIENT_STATUSES = (408, 500, 502, 503, 504)


def github_metadata_retry(response, policy, state):
    """Classify a uri result without discarding its original diagnostics."""
    state = _validated_state(response, policy, state)
    status = response.get("status")
    if status == 200 and not response.get("failed", False):
        return {"action": "success"}

    rate_limited = status == 429
    if status == 403:
        message = response.get("json", {})
        message = message.get("message", "") if isinstance(message, Mapping) else ""
        rate_limited = (
            "retry_after" in response
            or response.get("x_ratelimit_remaining") == "0"
            or "secondary rate limit" in str(message).lower()
        )
    if not rate_limited and status not in _TRANSIENT_STATUSES:
        return {"action": "fail", "reason": "permanent or unclassified request failure"}

    # GitHub's published primary/secondary rate-limit rules:
    # https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api
    delay = 2 ** state["attempt"]
    try:
        # Server time avoids retrying early when the controller's clock is fast.
        reference_time = state["now_epoch"]
        if "date" in response:
            reference_time = _http_date(response["date"])
        instructed = False
        if "retry_after" in response:
            raw = response["retry_after"]
            if isinstance(raw, str) and re.fullmatch(r"[0-9]+", raw):
                minimum = int(raw)
            else:
                minimum = ceil(max(0, _http_date(raw) - reference_time)) + 1
            delay = max(delay, minimum)
            instructed = True
        if rate_limited and "x_ratelimit_remaining" in response:
            remaining = _unsigned(
                response["x_ratelimit_remaining"], "X-RateLimit-Remaining"
            )
            if remaining == 0:
                reset = _unsigned(
                    response.get("x_ratelimit_reset"), "X-RateLimit-Reset"
                )
                delay = max(delay, ceil(max(0, reset - reference_time)) + 1)
                instructed = True
        if rate_limited and not instructed:
            delay = max(delay, 60 * 2 ** (state["attempt"] - 1))
    except (TypeError, ValueError, OverflowError) as error:
        return {"action": "fail", "reason": "invalid retry headers: %s" % error}

    return _bounded_delay(delay, policy, state)


def github_archive_retry(response, policy, state):
    """get_url lacks retry headers: retry only known transient HTTP failures."""
    state = _validated_state(response, policy, state)
    if not response["failed"] and response.get("status_code") in (None, "", 200, 304):
        return {"action": "success"}
    if response.get("status_code") not in _TRANSIENT_STATUSES:
        return {
            "action": "fail",
            "reason": "permanent or unclassified download failure; get_url does not expose retry headers",
        }
    return _bounded_delay(2 ** state["attempt"], policy, state)


def _bounded_delay(delay, policy, state):
    delay += state["jitter_seconds"]
    if state["attempt"] >= policy["max_attempts"]:
        return {"action": "fail", "reason": "attempt limit reached"}
    remaining = policy["max_wait_seconds"] - state["waited_seconds"]
    if delay > remaining:
        # Do not clamp server-required waits to the remaining budget.
        retry_at = "beyond the supported UTC timestamp range"
        try:
            retry_at = datetime.fromtimestamp(
                state["now_epoch"] + delay, timezone.utc
            ).isoformat()
        except (ValueError, OverflowError, OSError):
            pass  # The explicit failure below still reports the full required wait.
        return {
            "action": "fail",
            "reason": "required wait %s seconds exceeds remaining wait budget %s seconds; retry no earlier than %s"
            % (delay, remaining, retry_at),
        }
    return {"action": "retry", "delay_seconds": delay}


def _http_date(value):
    if not isinstance(value, str):
        raise ValueError("HTTP date must be a string")
    parsed = parsedate_to_datetime(value)
    # The obsolete asctime HTTP-date format has no explicit timezone.
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _unsigned(value, name):
    if not isinstance(value, str) or re.fullmatch(r"[0-9]+", value) is None:
        raise ValueError("%s must contain unsigned decimal seconds/counts" % name)
    return int(value)


def _validated_state(response, policy, state):
    if not all(isinstance(value, Mapping) for value in (response, policy, state)):
        raise AnsibleFilterError(
            "GitHub retry response, policy and state must be mappings"
        )
    if not isinstance(response.get("failed"), bool):
        raise AnsibleFilterError(
            "GitHub retry response must contain a boolean failed field"
        )
    state = dict(state)
    # Core 2.14 serializes computed set_fact counters to strings unless native
    # types are enabled. Validate that representation before converting it.
    if isinstance(state.get("waited_seconds"), str):
        try:
            state["waited_seconds"] = _unsigned(
                state["waited_seconds"], "waited_seconds"
            )
        except ValueError as error:
            raise AnsibleFilterError(str(error)) from error
    for source, key, minimum in (
        (policy, "max_attempts", 1),
        (policy, "max_wait_seconds", 0),
        (state, "attempt", 1),
        (state, "waited_seconds", 0),
        (state, "jitter_seconds", 0),
    ):
        value = source.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise AnsibleFilterError("Invalid GitHub retry %s" % key)
    now = state.get("now_epoch")
    if isinstance(now, bool) or not isinstance(now, (float, int)):
        raise AnsibleFilterError("GitHub retry now_epoch must be a finite timestamp")
    try:
        finite_time = isfinite(now)
    except OverflowError:
        finite_time = False
    if not finite_time:
        raise AnsibleFilterError("GitHub retry now_epoch must be a finite timestamp")
    if state["attempt"] > policy["max_attempts"]:
        raise AnsibleFilterError("GitHub retry attempt exceeds its limit")
    if state["waited_seconds"] > policy["max_wait_seconds"]:
        raise AnsibleFilterError("GitHub retry waited_seconds exceeds its budget")
    if state["jitter_seconds"] not in (0, 1):
        raise AnsibleFilterError("GitHub retry jitter_seconds must be 0 or 1")
    return state


class FilterModule:
    def filters(self):
        return {
            "github_metadata_retry": github_metadata_retry,
            "github_archive_retry": github_archive_retry,
        }
