from gbtd_infra.adapters.base import CountPlan
from gbtd_infra.models import CountMode


def test_count_signature_and_mode_enum():
    cp = CountPlan(mode=CountMode.EXACT, value=10, method="exact", signature="query:a")
    assert cp.mode == CountMode.EXACT
    assert cp.value == 10
