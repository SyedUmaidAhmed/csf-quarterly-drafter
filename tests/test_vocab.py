"""The vocabularies must match the CSF extract section 3 exactly.

These tests look tautological, and that is the point: they are a tripwire. If
someone "tidies up" a value here, the Power BI model stops matching and the
failure is silent everywhere else.
"""

from app import vocab


def test_traffic_light_vocabulary_is_closed_and_anchored():
    assert list(vocab.TRAFFIC_LIGHTS) == ["Red", "Amber", "Green", "Blue"]
    assert vocab.TRAFFIC_LIGHTS["Red"] == (
        "Significant risk; unlikely to deliver without intervention"
    )
    assert vocab.TRAFFIC_LIGHTS["Amber"] == "Some challenges; may need support"
    assert vocab.TRAFFIC_LIGHTS["Green"] == "On track"
    assert vocab.TRAFFIC_LIGHTS["Blue"] == "Achieved / complete"


def test_support_from_vocabulary():
    assert vocab.SUPPORT_FROM == (
        "ILT",
        "BDU",
        "Finance",
        "HR",
        "IDDT",
        "Communications",
        "Other",
    )


def test_source_vocabulary_includes_the_personal_layer_value():
    assert vocab.SOURCES == ("Director", "Team-Approved", "Substrate-Drafted")
    assert vocab.SUBSTRATE_DRAFTED in vocab.SOURCES


def test_quarter_pattern():
    for good in ("2026-Q1", "2026-Q3", "2027-Q4"):
        assert vocab.QUARTER_PATTERN.match(good)
    for bad in ("2026-Q5", "26-Q1", "2026Q1", "2026-q1", "Q1-2026"):
        assert not vocab.QUARTER_PATTERN.match(bad)


def test_narrative_limit_is_200_characters():
    assert vocab.NARRATIVE_MAX_CHARS == 200


def test_trend_is_recorded_as_calculated_downstream():
    assert "Trend_vs_Prior_Quarter" in vocab.CALCULATED_DOWNSTREAM


def test_anchors_render_for_prompts():
    rendered = vocab.traffic_light_anchors()
    for value, anchor in vocab.TRAFFIC_LIGHTS.items():
        assert f"- {value}: {anchor}" in rendered
