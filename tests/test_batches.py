import json
import multiprocessing as mp
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from lorasim.api import create_app
from lorasim.batches import Batch, Submission, allocate, prepare, submit
from lorasim.config import Profile, load_profile
from lorasim.jobs import Store, worker


def body(**changes):
    return {
        "yaml": json.dumps({"name": "比較", "duration_s": 8, "seed": 7,
                            "groups": [{"name": "ed", "count": 10}]}),
        "ed_counts": [100, 200, 300],
        "policies": [
            {"name": "Bandit", "definition": {"kind": "bandit", "options": {"epsilon": .2}, "action_space": {}}},
            {"name": "DQN", "definition": {"kind": "dqn", "options": {"hidden_sizes": [4]}, "action_space": {}}},
        ],
    } | changes


def submission(store, **changes):
    value = body(**changes)
    plan, _ = prepare(store, Batch.model_validate(value))
    return Submission.model_validate(value | {"plan_hash": plan["plan_hash"], "request_id": uuid.uuid4().hex})


def test_cartesian_six_runs_two_experiments_and_frozen_profiles(tmp_path):
    c = TestClient(create_app(tmp_path))
    preview = c.post("/api/batches/preview", json=body())
    assert preview.status_code == 200, preview.text
    plan = preview.json()
    assert plan["run_count"] == 6 and plan["new_experiments"] == 2
    assert c.get("/api/jobs").json() == [] and c.get("/api/experiments").json() == []
    data = body() | {"plan_hash": plan["plan_hash"], "request_id": uuid.uuid4().hex}
    result = c.post("/api/batches", json=data)
    assert result.status_code == 201, result.text
    result = result.json()
    assert len(result["jobs"]) == 6
    assert c.post("/api/batches", json=data).json() == result  # response lost: retry safely
    experiments = c.get("/api/experiments").json()
    assert len(experiments) == 2 and {e["runs"] for e in experiments} == {3}
    seen = set()
    for job in result["jobs"]:
        run = c.get("/api/jobs/" + job["id"]).json()
        p = load_profile(run["profile"], text=True)
        seen.add((sum(g.count for g in p.groups), p.component(p.groups[0].policy, "policy").kind))
        assert run["status"] == "queued"
        assert p.seed == 7 and p.duration_s == 8
        assert p.groups[0].policy.ref == job["algorithm"]
        assert p.groups[0].timing == Profile().groups[0].timing
        if job["algorithm"] == "Bandit":
            assert p.component(p.groups[0].policy, "policy").options["epsilon"] == .2
    assert seen == {(n, k) for n in [100, 200, 300] for k in ["bandit", "dqn"]}
    c.post("/api/jobs/" + result["jobs"][0]["id"] + "/cancel")
    assert c.get("/api/jobs/" + result["jobs"][0]["id"]).json()["status"] == "cancelled"


def test_existing_experiment_and_library_snapshot(tmp_path):
    store = Store(tmp_path)
    eid = store.create_experiment("既存Bandit")
    store.save_model("policy", "tuned", {"kind": "bandit", "options": {"epsilon": .4}, "action_space": {}})
    choices = [{"name": "調整済み", "definition": {"ref": "tuned"}, "experiment_id": eid}]
    request = submission(store, ed_counts=[2, 3], policies=choices)
    result = submit(store, request)
    assert result["experiment_ids"] == [eid] and len(store.experiments()) == 1
    store.save_model("policy", "tuned", {"kind": "bandit", "options": {"epsilon": .8}, "action_space": {}})
    p = load_profile(store.get(result["jobs"][0]["id"])["profile"], text=True)
    assert p.component(p.groups[0].policy, "policy").options["epsilon"] == .4
    assert submit(store, request) == result  # idempotent even after catalogue changes
    request = request.model_copy(update={"request_id": uuid.uuid4().hex})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        submit(store, request)
    assert exc.value.status_code == 409 and len(store.list()) == 2


def test_all_candidates_validated_before_atomic_insert_and_retry_race(tmp_path):
    store = Store(tmp_path)
    value = body(ed_counts=[2, 3])
    value["policies"][1]["definition"]["options"]["batch_size"] = -1
    c = TestClient(create_app(tmp_path))
    assert c.post("/api/batches/preview", json=value).status_code == 422
    assert c.post("/api/batches", json=value | {"request_id": "a"*32, "plan_hash": "a"*64}).status_code == 422
    assert store.list() == [] and store.experiments() == []
    request = submission(store, ed_counts=[2, 3])
    # Simulate a storage failure after two inserts. No partial batch/Experiment survives.
    with store.connect() as db:
        db.execute("""CREATE TRIGGER fail_batch BEFORE INSERT ON jobs
                      WHEN (SELECT count(*) FROM jobs)=2 BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    with pytest.raises(Exception, match="test failure"):
        submit(store, request)
    assert store.list() == [] and store.experiments() == []
    with store.connect() as db:
        db.execute("DROP TRIGGER fail_batch")
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: submit(store, request), range(2)))
    assert results[0] == results[1] and len(store.list()) == 4


def test_proportional_groups_assignment_replacement_and_preserved_settings(tmp_path):
    store = Store(tmp_path)
    p = Profile.model_validate({
        "groups": [{"name": "a", "count": 8, "tags": ["mobile"], "observe_position": True},
                   {"name": "b", "count": 2, "placement": "grid", "payload_bytes": 30}],
        "assignments": [{"groups": ["a"], "policy": {"kind": "fixed"},
                         "timing": {"kind": "periodic", "options": {"interval_s": 123}}}],
    })
    plan, profiles = prepare(store, Batch.model_validate(body(yaml=p.model_dump_json(), ed_counts=[20])))
    for text in profiles:
        q = load_profile(text, text=True)
        assert [g.count for g in q.groups] == [16, 4]
        assert q.groups[0].tags == ["mobile"] and q.groups[0].observe_position
        assert q.groups[1].payload_bytes == 30 and q.groups[1].placement == "grid"
        assert q.assignments[0].policy == q.groups[0].policy
        assert q.assignments[0].timing == p.assignments[0].timing
        assert (q.mac, q.channel, q.action_space, q.energy, q.seed) == (p.mac, p.channel, p.action_space, p.energy, p.seed)
    for counts in ([90, 10], [1, 1, 1000], [8, 2], [1]*7):
        for total in (len(counts), sum(counts), 100):
            assigned = allocate(counts, total)
            assert sum(assigned) == total and min(assigned) >= 1
        assert allocate(counts, sum(counts)) == list(counts)


@pytest.mark.parametrize("profile", [
    {"groups": [{"count": 1, "placement": "explicit", "positions": [[0, 0, 1.5]]}]},
    {"groups": [{"count": 1}], "assignments": [{"ids": [0], "mobility": {"kind": "stationary"}}]},
    {"groups": [{"count": 1, "confirmed": False}]},
])
def test_unsafe_resize_or_algorithm_precondition_never_queues(tmp_path, profile):
    c = TestClient(create_app(tmp_path))
    r = c.post("/api/batches/preview", json=body(yaml=json.dumps(profile), ed_counts=[2]))
    assert r.status_code == 422
    assert any("\u3040" <= x <= "\u9fff" for x in r.json()["detail"])
    assert r.json()["diagnostics"][0]["hint"]
    assert c.get("/api/jobs").json() == []


@pytest.mark.parametrize("counts", [[], [0], [-1], [2, 2], [1.5], [True], ["100"], [1000001]])
def test_invalid_count_lists(tmp_path, counts):
    c = TestClient(create_app(tmp_path))
    assert c.post("/api/batches/preview", json=body(ed_counts=counts)).status_code == 422
    assert c.get("/api/jobs").json() == []


def test_auth_unknown_destinations_and_limits(tmp_path, monkeypatch):
    monkeypatch.setenv("LORASIM_API_TOKEN", "token")
    c = TestClient(create_app(tmp_path))
    assert c.post("/api/batches/preview", json=body()).status_code == 401
    assert c.post("/api/batches", json={}).status_code == 401
    h = {"Authorization": "Bearer token"}
    policies = body()["policies"]
    policies[0]["experiment_id"] = "missing"
    assert c.post("/api/batches/preview", headers=h, json=body(policies=policies)).status_code == 422
    policies[1]["experiment_id"] = "missing"
    assert c.post("/api/batches/preview", headers=h, json=body(policies=policies)).status_code == 422
    policies = body()["policies"] + [{"name": "fixed", "definition": {"kind": "fixed"}}]
    assert c.post("/api/batches/preview", headers=h, json=body(ed_counts=list(range(1, 51)), policies=policies)).status_code == 422


def test_batch_runs_through_existing_worker_and_analysis(tmp_path):
    store = Store(tmp_path)
    result = submit(store, submission(store, ed_counts=[2, 3, 4]))
    process = mp.get_context("spawn").Process(target=worker, args=(str(tmp_path), 2, True))
    process.start()
    process.join(45)
    if process.is_alive():
        process.terminate(); process.join()
        pytest.fail("worker timed out")
    assert process.exitcode == 0
    assert all(store.get(j["id"])["status"] == "completed" for j in result["jobs"])
    c = TestClient(create_app(tmp_path))
    response = c.post("/api/analysis", json={"experiment_ids": result["experiment_ids"], "modules": [{"kind": "abstract"}]})
    assert response.status_code == 200, response.text
    assert len(response.json()["runs"]) == 6
