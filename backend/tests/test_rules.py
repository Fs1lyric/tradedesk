import pytest
from pydantic import ValidationError

from app.services.rules.dsl import Rule, evaluate

RULE = {
    "name": "Positive earnings news momentum",
    "universe": {"watchlist_id": "w1"},
    "when": {"all": [
        {"feature": "sentiment_feature", "op": ">", "value": 0.5},
        {"feature": "news.event_type", "op": "in", "value": ["earnings", "guidance"]},
        {"any": [
            {"feature": "trend", "op": ">", "value": 0},
            {"feature": "rec.score", "op": ">=", "value": 0.8},
        ]},
    ]},
    "action": {"side": "buy", "sizing": "atr_risk", "risk_per_trade": 0.005,
               "order": "bracket", "stop_atr": 2.0, "target_atr": 3.0},
    "cooldown_minutes": 240,
    "mode": "approve",
}


def ctx(**over):
    base = {"sentiment_feature": 0.7, "news": {"event_type": "earnings"}, "trend": -0.1,
            "rec": {"score": 0.9}}
    base.update(over)
    return base


def test_rule_parses_and_matches():
    r = Rule.model_validate(RULE)
    assert evaluate(r.when, ctx())
    assert r.action.order == "bracket"


def test_rule_fails_each_branch():
    r = Rule.model_validate(RULE)
    assert not evaluate(r.when, ctx(sentiment_feature=0.2))
    assert not evaluate(r.when, ctx(news={"event_type": "macro"}))
    assert not evaluate(r.when, ctx(rec={"score": 0.1}))


def test_missing_feature_is_false_not_error():
    r = Rule.model_validate(RULE)
    assert not evaluate(r.when, {"trend": 1})


def test_validation_rejects_bad_ops_and_values():
    bad = {**RULE, "when": {"all": [{"feature": "x", "op": "~", "value": 1}]}}
    with pytest.raises(ValidationError):
        Rule.model_validate(bad)
    bad2 = {**RULE, "action": {**RULE["action"], "risk_per_trade": 0.5}}
    with pytest.raises(ValidationError):
        Rule.model_validate(bad2)
    bad3 = {**RULE, "when": {"all": [{"feature": "x", "op": "in", "value": 3}]}}
    with pytest.raises(ValidationError):
        Rule.model_validate(bad3)


def test_operators():
    from app.services.rules.dsl import Condition
    c = lambda op, v: Condition(feature="x", op=op, value=v)  # noqa: E731
    assert evaluate(c("<", 2), {"x": 1})
    assert evaluate(c("<=", 1), {"x": 1})
    assert evaluate(c("==", "a"), {"x": "a"})
    assert evaluate(c("!=", "a"), {"x": "b"})
    assert evaluate(c("not_in", ["a"]), {"x": "b"})
    assert evaluate(c("between", [0, 2]), {"x": 1})
