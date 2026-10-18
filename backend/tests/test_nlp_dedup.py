from datetime import UTC, datetime, timedelta

from app.services.nlp import dedup


def test_canonical_url_strips_tracking_and_www():
    u = "HTTPS://www.Reuters.com/markets/apple-beats/?utm_source=x&id=7&fbclid=abc#top"
    assert dedup.canonical_url(u) == "https://reuters.com/markets/apple-beats?id=7"


def test_simhash_near_duplicates_are_close_and_unrelated_far():
    a = dedup.simhash64("Apple beats earnings estimates as iPhone sales surge in China")
    b = dedup.simhash64("Apple beats earnings estimates as iPhone sales surge in China; shares up")
    c = dedup.simhash64("Federal Reserve holds rates steady, signals two cuts later this year")
    assert dedup.hamming(a, b) < dedup.hamming(a, c)
    assert dedup.hamming(a, a) == 0


def test_signed_roundtrip_for_bigint_storage():
    h = (1 << 63) + 12345
    s = dedup.to_signed64(h)
    assert s < 0
    assert dedup.to_unsigned64(s) == h


def test_find_cluster_respects_window_and_distance():
    now = datetime(2026, 1, 5, tzinfo=UTC)
    h = dedup.simhash64("Nvidia unveils new AI chip at GTC keynote in San Jose")
    recent = [
        dedup.ClusterCandidate(item_id=1, cluster_id=1, simhash=h ^ 0b11, available_at=now),
        dedup.ClusterCandidate(
            item_id=2, cluster_id=2, simhash=h, available_at=now - timedelta(hours=49)
        ),
    ]
    assert dedup.find_cluster(h, recent, now) == 1
    assert dedup.find_cluster(h ^ 0xFFFF, recent, now) is None
    assert dedup.find_cluster(h, recent[1:], now) is None  # outside 48h
