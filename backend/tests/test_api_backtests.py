from tests.conftest import requires_db, signup

pytestmark = requires_db

RUN = {
    "kind": "backtest", "strategy": "technical_trend", "symbols": ["aapl", "MSFT"],
    "start": "2024-01-01", "end": "2025-01-01", "strategy_params": {},
}


async def test_queue_list_get_delete(api):
    headers = await signup(api)
    r = await api.post("/backtests", headers=headers, json=RUN)
    assert r.status_code == 202, r.text
    run = r.json()
    assert run["status"] == "queued"
    assert run["params"]["symbols"] == ["AAPL", "MSFT"]

    assert [x["id"] for x in (await api.get("/backtests", headers=headers)).json()] == [run["id"]]
    one = await api.get(f"/backtests/{run['id']}", headers=headers)
    assert one.status_code == 200 and one.json()["result"] is None

    bob = await signup(api, email="b@example.com")
    assert (await api.get(f"/backtests/{run['id']}", headers=bob)).status_code == 404
    assert (await api.get("/backtests", headers=bob)).json() == []
    assert (await api.delete(f"/backtests/{run['id']}", headers=headers)).status_code == 204
    assert (await api.get("/backtests", headers=headers)).json() == []


async def test_backtest_validation(api):
    headers = await signup(api)
    bad = [
        {**RUN, "strategy": "astrology"},
        {**RUN, "kind": "moonshot"},
        {**RUN, "start": "2025-01-01", "end": "2024-01-01"},
        {**RUN, "symbols": []},
        {**RUN, "strategy_params": {"no_such_param": 1}},
    ]
    for body in bad:
        r = await api.post("/backtests", headers=headers, json=body)
        assert r.status_code == 422, (body, r.text)
