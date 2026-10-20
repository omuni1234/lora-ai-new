import json
import multiprocessing as mp

import pytest
from fastapi.testclient import TestClient

from lorasim.api import create_app
from lorasim.config import Profile
from lorasim.jobs import Store, worker
from lorasim import tuning as t


def request(**changes):
    profile = Profile.model_validate({
        "duration_s": 60, "area_m": [100, 100], "energy": {"radio": {"enabled": False}},
        "gateways": [{"position": [50, 50, 15]}],
        "groups": [{"name": "devices", "count": 3,
                    "policy": {"kind": "bandit", "action_space": {}},
                    "timing": {"kind": "periodic", "options": {"interval_s": 10, "start": {"mode": "uniform", "min_s": 0, "max_s": 5}}}}],
    })
    return t.StudyRequest.model_validate(dict(
        yaml=profile.model_dump_json(), definition={"kind": "bandit", "action_space": {}},
        groups=["devices"], dimensions=[{"path": "epsilon", "values": [.05, .1, .2, .4]}],
        seeds=[1, 2], validation_seeds=[11, 12], budgets_s=[20, 60], max_candidates=4,
    ) | changes)


def summary(value=.5):
    return {"wall_s": 1, "abstract": {"ed_count": 3, "eligible_ed_count": 3,
            "mean_ed_prr": value, "pooled_prr": value, "uplinks": 30, "censored": 0,
            "fractions": {"Received": value, "Interference": 1-value},
            "fairness": {"jain": .9}, "energy": {"measured": True}},
            "energy": {"radio_j": 1, "mcu_j": .5}}


def finish_waiting(store, sid, score=None):
    data = t.detail(store, sid)
    for trial in data["state"]["waiting"]:
        value = score(trial) if score else .4+.1*trial["candidate"]
        for jid in trial["jobs"]:
            store.finish(jid, "completed", summary(value))


def complete_mock(store, sid, score=None):
    for _ in range(70):
        t.advance(store)
        data = t.detail(store, sid)
        if data["status"] != "running":
            return data
        finish_waiting(store, sid, score)
    pytest.fail("study failed to finish")


@pytest.mark.parametrize("method", ["halving", "grid", "annealing"])
def test_search_rounds_matched_profiles_and_holdout_are_persistent(tmp_path, method):
    store = Store(tmp_path)
    req = request(method=method)
    sid = t.create(store, req)["id"]
    t.advance(store)
    initial = t.detail(store, sid)
    ids = [j["id"] for j in initial["live"]]
    t.advance(Store(tmp_path))
    assert ids == [j["id"] for j in t.detail(store, sid)["live"]]  # no duplicate jobs
    for j in initial["live"]:
        p = store.parse_profile(store.get(j["id"])["profile"])
        assert sum(g.count for g in p.groups) == 3
        assert p.channel == store.parse_profile(req.yaml).channel
        assert p.mac == store.parse_profile(req.yaml).mac
        assert p.seed in req.seeds and p.notifications == []
    data = complete_mock(Store(tmp_path), sid)
    assert data["status"] == "completed", data["state"].get("error")
    state = data["state"]
    confirmations = [r for r in state["history"] if r["phase"] == "validation"]
    assert {r["candidate"] for r in confirmations} == {0, state["winner"]}
    assert all(r["seeds"] == req.validation_seeds and r["duration_s"] == 60 for r in confirmations)
    assert len({j for r in state["history"] for j in r["jobs"]}) <= state["estimate"]["max_runs"]
    if method == "grid":
        rows = [r for r in state["history"] if r["phase"] == "search"]
        assert len(rows) == 4 and all(r["duration_s"] == 60 for r in rows)
    if method == "halving":
        stages = [[r for r in state["history"] if r["phase"] == "search" and r["duration_s"] == d] for d in [20, 60]]
        assert len(stages[1]) < len(stages[0])
        assert all(any(r["candidate"] == 0 for r in stage) for stage in stages)
    if method == "annealing":
        assert any(r.get("accepted") is True for r in state["history"])
        assert any(r["phase"] == "refine" for r in state["history"])


def test_holdout_does_not_reselect_winner(tmp_path):
    store = Store(tmp_path); sid = t.create(store, request(method="grid"))["id"]
    data = complete_mock(store, sid, lambda r: (.99 if r["candidate"] == 0 else .1) if r["phase"] == "validation" else .4+.1*r["candidate"])
    assert data["state"]["winner"] == 3  # baseline wins holdout, selection stays frozen
    assert data["status"] == "completed"


def test_export_is_new_definition_preserves_action_space_and_never_weights(tmp_path):
    store = Store(tmp_path); req = request(method="grid")
    sid = t.create(store, req)["id"]; data = complete_mock(store, sid); cid = data["state"]["winner"]
    result = t.save_candidate(store, sid, cid, "bandit-tuned")
    definition = store.catalog()["policy"]["bandit-tuned"]
    assert definition["options"]["epsilon"] == .4 and definition["action_space"] == result["definition"]["action_space"]
    assert set(definition) == {"kind", "ref", "options", "action_space"}
    with pytest.raises(t.TuningError, match="同名"):
        t.save_candidate(store, sid, cid, "bandit-tuned")
    with store.connect() as db:
        assert db.execute("SELECT candidate FROM tuning_exports").fetchone()[0] == cid


def test_cancel_does_not_touch_unrelated_jobs(tmp_path):
    store = Store(tmp_path); other = store.submit("duration_s: 10")
    sid = t.create(store, request())["id"]; t.advance(store)
    ids = [j["id"] for j in t.detail(store, sid)["live"]]
    t.cancel(store, sid); t.advance(store)
    assert t.detail(store, sid)["status"] == "cancelled"
    assert all(store.get(j)["status"] == "cancelled" for j in ids)
    assert store.get(other)["status"] == "queued"


def test_invalid_candidates_record_reasons_without_stopping_valid_ones(tmp_path):
    store = Store(tmp_path)
    sid = t.create(store, request(method="grid", dimensions=[{"path": "epsilon", "values": [-.1, .1, 2]}]))["id"]
    data = complete_mock(store, sid)
    assert data["status"] == "completed"
    failed = [r for r in data["state"]["history"] if not r["result"]["valid"]]
    assert len(failed) == 2 and all(not r["jobs"] and r["result"]["errors"] for r in failed)


def test_failed_run_is_not_a_successful_partial_seed_mean(tmp_path):
    store = Store(tmp_path); sid = t.create(store, request())["id"]
    t.advance(store)
    for j in t.detail(store, sid)["live"]:
        store.finish(j["id"], "failed", error="test")
    t.advance(store)
    data = t.detail(store, sid)
    assert data["status"] == "failed"
    assert all(not r["result"]["valid"] for r in data["state"]["history"])


def test_full_ed_objective_penalizes_inactive_and_energy_is_not_zero_when_disabled():
    req = request(min_coverage=.5)
    s = summary(.6); s["abstract"]["eligible_ed_count"] = 2
    m = t.metric(s, req)
    assert m["prr"] == pytest.approx(.4) and m["jain"] == pytest.approx(.6)
    s["abstract"]["energy"]["measured"] = False
    assert t.metric(s, req)["energy_j_per_success"] is None
    with pytest.raises(t.TuningError):
        t.metric(s, request())  # minimum coverage
    with pytest.raises(t.TuningError):
        t.metric(summary(0), request(weights={"energy": 1}))
    w = t.metric(summary(.5), request(weights={"interference": .2, "fairness": .1, "energy": .3, "include_mcu": True}))
    assert w["score"] == pytest.approx(.5-.2*.5+.1*.9-.3*1.5/15)


def test_same_budget_reuses_runs_only_within_the_study(tmp_path):
    store = Store(tmp_path); sid = t.create(store, request(method="annealing", budgets_s=[60]))["id"]
    data = complete_mock(store, sid)
    refinements = [r for r in data["state"]["history"] if r["phase"] == "refine"]
    assert refinements and all(r["reused"] for r in refinements)
    assert not any(r.get("reused") for r in data["state"]["history"] if r["phase"] == "validation")


def test_annealing_accepts_worse_at_high_temperature(tmp_path):
    store = Store(tmp_path); sid = t.create(store, request(method="annealing", temperature=100, cooling=1))["id"]
    data = complete_mock(store, sid, lambda r: .6 if r["candidate"] == 0 else .59)
    assert any(r.get("accepted") for r in data["state"]["history"] if r["candidate"] != 0)


@pytest.mark.parametrize("changes,match", [
    ({"method": "grid", "max_candidates": 2}, "網羅"),
    ({"budgets_s": [61]}, "超え"),
    ({"groups": ["missing"]}, "グループ"),
    ({"weights": {"energy": 1}}, "消費電力"),
    ({"dimensions": [{"path": "unknown", "values": [1, 2]}]}, "項目"),
])
def test_invalid_plan_is_rejected_before_scheduling(tmp_path, changes, match):
    store = Store(tmp_path)
    with pytest.raises(t.TuningError, match=match):
        t.create(store, request(**changes))
    assert not store.list() and not t.listing(store)


@pytest.mark.parametrize("changes", [
    {"seeds": [1, 1]}, {"validation_seeds": [1, 2]}, {"budgets_s": [10, 5]},
    {"dimensions": [{"path": "epsilon", "values": [.1, .1]}]},
    {"dimensions": [{"path": "a", "values": [1, 2]}, {"path": "a.b", "values": [1, 2]}]},
])
def test_invalid_request(changes):
    with pytest.raises(ValueError):
        request(**changes)


def test_assignment_override_is_reported(tmp_path):
    req = request(); p = json.loads(req.yaml)
    p["assignments"] = [{"ids": [0], "policy": {"kind": "fixed"}}]
    with pytest.raises(t.TuningError, match="上書き"):
        t.prepare(Store(tmp_path), req.model_copy(update={"yaml": json.dumps(p)}))


def test_shadowed_radio_options_are_not_suggested_or_silently_tuned(tmp_path):
    definition = {"kind": "fixed", "action_space": {}}
    paths = [x["path"] for x in t.suggestions("policy", t.Component.model_validate(definition))["parameters"]]
    assert "sf" not in paths
    with pytest.raises(t.TuningError, match="共通行動空間"):
        t.prepare(Store(tmp_path), request(definition=definition, dimensions=[{"path": "sf", "values": [7, 8]}]))


def test_nested_timing_and_joint_mobility_constraints(tmp_path):
    store = Store(tmp_path)
    definition = {"kind": "periodic", "options": {"start": {"mode": "uniform", "max_s": 5}}}
    suggestions = t.suggestions("timing", t.Component.model_validate(definition))
    assert "start.max_s" in {x["path"] for x in suggestions["parameters"]}
    req = request(category="timing", definition=definition, method="grid",
                  dimensions=[{"path": "start.max_s", "values": [2, 5]}])
    data = complete_mock(store, t.create(store, req)["id"])
    t.save_candidate(store, data["id"], data["state"]["winner"], "timing-tuned")
    assert "timing-tuned" in store.catalog()["timing"]
    req = request(category="mobility", method="grid", definition={"kind": "waypoints"},
                  dimensions=[{"path": "speed_min_m_s", "values": [.2, 2]}, {"path": "speed_max_m_s", "values": [1, 3]}])
    data = complete_mock(store, t.create(store, req)["id"])
    trial = next(r for r in data["state"]["history"] if data["state"]["candidates"][r["candidate"]]["params"] == {"speed_min_m_s": 2, "speed_max_m_s": 3})
    assert trial["result"]["valid"]  # valid jointly even if min=2 alone violated baseline max
    t.save_candidate(store, data["id"], trial["candidate"], "mobility-tuned")
    assert "mobility-tuned" in store.catalog()["mobility"]


def test_custom_options_get_nested_candidates(tmp_path, monkeypatch):
    (tmp_path/"custom_tuning.py").write_text('''
from pydantic import Field
from lorasim.interfaces import Options
from lorasim.plugins.selector.bandit import Bandit
class Inner(Options):
    scale: float = Field(1., gt=0, title="独自尺度")
class Settings(Options):
    inner: Inner = Field(default_factory=Inner)
class Custom(Bandit):
    Options = Settings
    def __init__(self, rng, inner, action_space=None):
        super().__init__(rng, epsilon=.1, action_space=action_space)
''')
    monkeypatch.syspath_prepend(str(tmp_path)); monkeypatch.setenv("LORASIM_ALLOW_PLUGINS", "1")
    spec = t.Component(kind="custom_tuning:Custom")
    assert "inner.scale" in [x["path"] for x in t.suggestions("policy", spec)["parameters"]]
    store = Store(tmp_path/"store")
    req = request(definition=spec.model_dump(), method="grid", dimensions=[{"path": "inner.scale", "values": [.5, 1]}])
    data = complete_mock(store, t.create(store, req)["id"])
    t.save_candidate(store, data["id"], data["state"]["winner"], "custom-tuned")
    assert "custom-tuned" in store.catalog()["policy"]


def test_low_budget_warns_about_learning_warmup(tmp_path):
    req = request(definition={"kind": "dqn", "action_space": {}},
                  dimensions=[{"path": "learning_rate", "values": [.001, .01]}])
    estimate = t.prepare(Store(tmp_path), req)[2]
    assert any("学習・履歴" in w for w in estimate["warnings"])


def test_version_mismatch_fails_without_scheduling(tmp_path):
    store = Store(tmp_path); sid = t.create(store, request())["id"]
    data = t.detail(store, sid); data["state"]["simulator_version"] = "old"
    with store.connect() as db:
        db.execute("UPDATE tuning_studies SET state=? WHERE id=?", (json.dumps(data["state"]), sid))
    t.advance(store)
    assert t.detail(store, sid)["status"] == "failed" and not store.list()


def test_library_reference_is_snapshotted_before_later_edit(tmp_path):
    store = Store(tmp_path); store.save_model("timing", "traffic", {"kind": "periodic", "options": {"interval_s": 30}})
    req = request(); p = json.loads(req.yaml); p["groups"][0]["timing"] = {"ref": "traffic"}
    sid = t.create(store, req.model_copy(update={"yaml": json.dumps(p)}))["id"]
    store.save_model("timing", "traffic", {"kind": "periodic", "options": {"interval_s": 90}})
    t.advance(store)
    j = t.detail(store, sid)["live"][0]
    p = store.parse_profile(store.get(j["id"])["profile"])
    assert p.component(p.groups[0].timing, "timing").options["interval_s"] == 30


def test_ui_api_suggestions_preview_auth_and_save(tmp_path, monkeypatch):
    monkeypatch.setenv("LORASIM_API_TOKEN", "local-test")
    c = TestClient(create_app(tmp_path))
    assert c.get("/api/tuning").status_code == 401
    c.headers["Authorization"] = "Bearer local-test"
    req = request().model_dump(mode="json")
    r = c.post("/api/tuning/suggestions", json={k: req[k] for k in ("category", "definition")})
    assert r.status_code == 200 and "epsilon" in [p["path"] for p in r.json()["parameters"]]
    r = c.post("/api/tuning/preview", json=req)
    assert r.status_code == 200 and r.json()["max_runs"] > 0
    assert c.get("/api/tuning").json() == []
    r = c.post("/api/tuning", json=req); assert r.status_code == 200
    sid = r.json()["id"]
    assert c.post(f"/api/tuning/{sid}/save", json={"candidate": 0, "name": "x"}).status_code == 422
    assert c.post(f"/api/tuning/{sid}/cancel").status_code == 200
    assert c.get("/api/tuning/missing").status_code == 404
    assert c.get("/static/tuning.js").status_code == 200
    assert "/static/tuning.js?v=" in c.get("/").text


def test_real_spawn_worker_completes_study_without_browser(tmp_path):
    req = request(method="halving", budgets_s=[30, 60], seeds=[1], validation_seeds=[11],
                  dimensions=[{"path": "epsilon", "values": [.1, .2]}], max_candidates=2)
    store = Store(tmp_path); sid = t.create(store, req)["id"]
    proc = mp.get_context("spawn").Process(target=worker, args=(str(tmp_path), 2, True))
    proc.start()
    try:
        proc.join(50)
        assert not proc.is_alive() and proc.exitcode == 0
        data = t.detail(store, sid)
        assert data["status"] == "completed", data["state"]
        assert all(r["result"]["valid"] for r in data["state"]["history"])
        assert all((tmp_path/"runs"/j/"summary.json").exists() for r in data["state"]["history"] for j in r["jobs"])
        t.save_candidate(store, sid, data["state"]["winner"], "real-tuned")
    finally:
        if proc.is_alive():
            proc.terminate(); proc.join(10)
