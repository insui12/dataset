from gbtd_infra.clients.http import HostTokenBucket


def test_token_bucket_wait_is_deterministic_when_empty():
    bucket = HostTokenBucket(rps=10.0, capacity=2)
    first = bucket.consume(1)
    second = bucket.consume(1)
    third = bucket.consume(1)

    assert first == 0.0
    assert second == 0.0
    assert third > 0
