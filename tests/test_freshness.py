"""Staleness must be computed correctly at render time (the inline JS covers
the case where the page itself is old; that path is exercised by the
data-fetched-at attributes asserted in test_render)."""

from datetime import datetime, timedelta, timezone

from minipirineu.render import age_hours, age_label, freshness_badge

NOW = datetime(2026, 7, 14, 18, 0, tzinfo=timezone.utc)


def data_fetched(hours_ago: float) -> dict:
    fetched = NOW - timedelta(hours=hours_ago)
    return {"fetched_at": fetched.isoformat(timespec="seconds")}


def test_age_hours():
    assert age_hours(data_fetched(3.5)["fetched_at"], NOW) == 3.5


def test_age_label_minutes_then_hours():
    assert age_label(0.5) == "hace 30 min"
    assert age_label(7.2) == "hace 7 h"


def test_openmeteo_fresh_within_7h():
    badge = freshness_badge("openmeteo", "Modelos AROME", data_fetched(6.5), NOW)
    assert 'class="badge fresh"' in badge
    assert "hace 6 h" in badge  # visible age, always shown


def test_openmeteo_stale_after_7h():
    badge = freshness_badge("openmeteo", "Modelos AROME", data_fetched(7.5), NOW)
    assert 'class="badge stale"' in badge


def test_meteocat_threshold_is_26h():
    assert 'class="badge fresh"' in freshness_badge("meteocat", "Meteocat", data_fetched(25), NOW)
    assert 'class="badge stale"' in freshness_badge("meteocat", "Meteocat", data_fetched(27), NOW)


def test_missing_source_is_visibly_missing():
    badge = freshness_badge("openmeteo", "Modelos AROME", None, NOW)
    assert 'class="badge missing"' in badge
    assert "sin datos" in badge


def test_badge_carries_attributes_for_clientside_check():
    badge = freshness_badge("openmeteo", "Modelos AROME", data_fetched(1), NOW)
    assert "data-fetched-at=" in badge
    assert 'data-stale-after-h="7"' in badge
