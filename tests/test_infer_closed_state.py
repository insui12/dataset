from datetime import datetime, timezone

import pytest

from gbtd_infra.adapters.base import infer_closed_state


def _aware_dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "state,resolution,reason,closed_at,filtered,expected_closed,expected_review",
    [
        ("closed", None, None, None, False, True, False),
        ("resolved", None, None, None, False, True, False),
        ("open", None, None, None, False, False, False),
        ("open", "fixed", None, None, False, True, False),
        ("reopened", None, None, None, False, False, False),
        (None, "wontfix", None, None, False, True, False),
        (None, None, "by design", None, False, True, False),
        (None, None, "invalid", None, False, False, True),
        (None, None, None, _aware_dt("2024-01-01T00:00:00"), False, True, False),
    ],
)
def test_infer_closed_state_heuristics(
    state,
    resolution,
    reason,
    closed_at,
    filtered,
    expected_closed,
    expected_review,
):
    result = infer_closed_state(
        state_raw=state,
        resolution_raw=resolution,
        close_reason_raw=reason,
        closed_at=closed_at,
        closed_filter_applied=filtered,
    )

    assert result.is_closed == expected_closed
    assert result.needs_review == expected_review
