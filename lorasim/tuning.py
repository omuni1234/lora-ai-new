"""Persistent, budgeted model search. Evaluations are ordinary isolated simulator jobs.

Only the supervisor advances a study. Scheduling jobs and saving search state share
one SQLite transaction, so reconnects/restarts cannot duplicate a generation.
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import random
import statistics
import time
import uuid
from typing import Literal

from pydantic import Field, model_validator

from .config import Component, Model, Profile, dump_profile
from .diagnostics import validate_options
from .registry import constructor, metadata
from . import __version__


class TuningError(ValueError):
    pass


class Dimension(Model):
    path: str = Field(min_length=1, max_length=200)
    values: list = Field(min_length=2, max_length=64)

    @model_validator(mode="after")
    def valid(self):
        if any(not p or p in {"__proto__", "constructor", "prototype"} for p in self.path.split(".")):
            raise ValueError("探索項目にはoptions内の有効なパスを指定してください。")
        keys = [json.dumps(v, sort_keys=True, allow_nan=False) for v in self.values]
        if len(set(keys)) != len(keys):
            raise ValueError("候補値が重複しています。同じ値は1回だけ登録してください。")
        return self


class Weights(Model):
    interference: float = Field(0, ge=0, le=100)
    gateway_tx: float = Field(0, ge=0, le=100)
    sensitivity: float = Field(0, ge=0, le=100)
    fairness: float = Field(0, ge=0, le=100)
    energy: float = Field(0, ge=0, le=100)
    include_mcu: bool = False


class StudyRequest(Model):
    yaml: str = Field(max_length=2_000_000)
    name: str = Field("モデル調整", min_length=1, max_length=120)
    category: Literal["policy", "mobility", "timing"] = "policy"
    definition: Component
    groups: list[str] = Field(min_length=1)
    dimensions: list[Dimension] = Field(min_length=1, max_length=12)
    method: Literal["halving", "annealing", "grid"] = "halving"
    max_candidates: int = Field(12, ge=2, le=128)
    budgets_s: list[float] = Field(default_factory=lambda: [600, 1800, 5400], min_length=1, max_length=6)
    seeds: list[int] = Field(default_factory=lambda: [1, 2], min_length=1, max_length=8)
    validation_seeds: list[int] = Field(default_factory=lambda: [101, 102], min_length=1, max_length=8)
    search_seed: int = Field(7, ge=0, le=2**32-1)
    reduction: int = Field(3, ge=2, le=8)
    finalists: int = Field(3, ge=1, le=16)
    temperature: float = Field(.05, gt=0, le=100)
    cooling: float = Field(.9, gt=0, le=1)
    restart_probability: float = Field(.15, ge=0, le=1)
    min_coverage: float = Field(.9, gt=0, le=1)
    min_uplinks_per_ed: float = Field(1, ge=0, le=1e9)
    weights: Weights = Field(default_factory=Weights)

    @model_validator(mode="after")
    def valid(self):
        if not self.name.strip():
            raise ValueError("探索名を入力してください。")
        if any(not math.isfinite(x) or x <= 0 for x in self.budgets_s) or self.budgets_s != sorted(set(self.budgets_s)):
            raise ValueError("評価時間は正の秒数を小さい順に、重複なく指定してください。")
        for seeds in (self.seeds, self.validation_seeds):
            if len(set(seeds)) != len(seeds) or any(not 0 <= s < 2**32 for s in seeds):
                raise ValueError("seedには0〜4294967295の異なる整数を指定してください。")
        if set(self.seeds) & set(self.validation_seeds):
            raise ValueError("探索用seedと確認用seedは分離してください。")
        paths = [d.path for d in self.dimensions]
        if len(set(paths)) != len(paths) or any(a != b and b.startswith(a+".") for a in paths for b in paths):
            raise ValueError("探索項目の重複、親項目と子項目の同時指定はできません。")
        if len(set(self.groups)) != len(self.groups):
            raise ValueError("対象グループが重複しています。")
        if self.definition.ref:
            raise ValueError("参照を解決したモデル定義（kindとoptions）を選んでください。")
        return self


def _key(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _set(options, path, value):
    parts = path.split(".")
    node = options
    try:
        for part in parts[:-1]:
            node = node[int(part)] if isinstance(node, list) else node[part]
        last = parts[-1]
        if isinstance(node, list):
            node[int(last)] = deepcopy(value)
        elif isinstance(node, dict):
            node[last] = deepcopy(value)
        else:
            raise KeyError(last)
    except (KeyError, IndexError, ValueError, TypeError):
        raise TuningError(f"探索項目 {path} の親項目がありません。モデル定義に先に設定してください。") from None


def _get(options, path):
    for part in path.split("."):
        options = options[int(part)] if isinstance(options, list) else options[part]
    return options


def definition_for(req, params):
    definition = req.definition.model_copy(deep=True)
    cls = constructor(req.category, definition.kind)
    if getattr(cls, "Options", None):
        definition.options = cls.Options.model_validate(definition.options).model_dump(mode="json")
    for path, value in params.items():
        _set(definition.options, path, value)
    if getattr(cls, "Options", None):
        definition.options = cls.Options.model_validate(definition.options).model_dump(mode="json")
    return definition


def candidate_profile(base, req, params, duration, seed):
    data = base.model_dump(mode="json")
    definition = definition_for(req, params).model_dump(mode="json")
    for group in data["groups"]:
        if group["name"] in req.groups:
            group[req.category] = deepcopy(definition)
    # No sampling, policy-sharing, traffic compression or PHY approximations.
    data.update(duration_s=duration, seed=seed, notifications=[])
    profile = Profile.model_validate(data)
    validate_options(profile)
    return profile


def suggestions(category, definition):
    meta = metadata(category, definition.kind)
    schema, result = meta["schema"], []
    values = deepcopy(meta["defaults"])
    values.update(definition.options)
    bound = definition.action_space is not None or meta["uses_action_space"]
    blocked = set(meta["space_options"]) if category == "policy" and bound else set()
    visibility = meta.get("option_visibility")

    def walk(s, value, path=""):
        s = dict(s)
        for _ in range(8):
            if "$ref" in s:
                ref = s.pop("$ref")
                s = {**schema.get("$defs", {}).get(ref.split("/")[-1], {}), **s}
            elif "anyOf" in s:
                branches = s.pop("anyOf")
                s = {**s, **next((v for v in branches if v.get("type") != "null"), {})}
            else:
                break
        if path.count(".") > 8:
            return
        if s.get("type") == "object" or isinstance(value, dict):
            if path and not isinstance(value, dict):
                return
            for k, child in s.get("properties", {}).items():
                if not path and (k in blocked or (visibility and k not in visibility["values"].get(values.get(visibility["field"]), s.get("properties", {})))):
                    continue
                walk(child, value.get(k) if isinstance(value, dict) else None, (path+"."+k).lstrip("."))
            return
        choices = s.get("enum")
        if choices is None and s.get("type") == "boolean":
            choices = [False, True]
        if choices is None and isinstance(value, (float, int)) and not isinstance(value, bool):
            choices = [value*.5, value, value*2] if value else [0, .1, 1]
            if s.get("type") == "integer":
                choices = [round(v) for v in choices]
            choices = [v for v in choices if
                       v >= s.get("minimum", -math.inf) and v <= s.get("maximum", math.inf) and
                       v > s.get("exclusiveMinimum", -math.inf) and v < s.get("exclusiveMaximum", math.inf)]
        if choices:
            choices = list({_key(v): v for v in choices}.values())
            if all(type(v) in (int, float) for v in choices):
                choices.sort()
            if len(choices) > 1:
                result.append(dict(path=path, label=s.get("title", path),
                                   description=s.get("description", ""), current=value, values=choices))
    walk(schema, values)
    return {"parameters": result, "label": meta["label"],
            "hint": "候補は型・範囲に基づくたたき台です。モデルの推奨値や最適値ではありません。独自Optionsも同じ方法で読み取ります。"}


def _coordinates(req, index):
    coord = []
    for d in reversed(req.dimensions):
        coord.append(index % len(d.values))
        index //= len(d.values)
    return list(reversed(coord))


def _params(req, coord):
    return {d.path: d.values[i] for d, i in zip(req.dimensions, coord)}


def prepare(store, req):
    base = store.parse_profile(req.yaml)
    if max(req.budgets_s) > base.duration_s:
        raise TuningError("最長評価時間が元プロファイルを超えています。評価時間または実験時間を見直してください。")
    if set(req.groups) - {g.name for g in base.groups}:
        raise TuningError("対象グループがプロファイルにありません。現在の条件を取り込み直してください。")
    # An ED-level assignment must not silently override the candidate being tuned.
    offset = 0
    for g in base.groups:
        if g.name in req.groups:
            for a in base.assignments:
                applies = ((not a.groups or g.name in a.groups) and
                           (not a.tags or set(a.tags) <= set(g.tags)) and
                           (not a.ids or any(offset <= i < offset+g.count for i in a.ids)))
                if getattr(a, req.category) is not None and applies:
                    raise TuningError(f"{g.name} は条件による割り当てでモデルが上書きされます。対象カテゴリの割り当てを外してから探索してください。")
        offset += g.count
    if req.weights.energy and not base.energy.radio.enabled:
        raise TuningError("エネルギーを評価に使うには、プロファイルの無線消費電力モデルを有効にしてください。")
    definition = definition_for(req, {})
    meta = metadata(req.category, definition.kind)
    if req.category == "policy" and (definition.action_space is not None or meta["uses_action_space"]):
        if any(d.path.split(".")[0] in meta["space_options"] for d in req.dimensions):
            raise TuningError("指定項目は共通行動空間で上書きされるため探索できません。学習の設定項目を選んでください。")
    try:
        originals = {d.path: _get(definition.options, d.path) for d in req.dimensions}
    except (KeyError, IndexError, TypeError, ValueError):
        raise TuningError("探索項目がモデル定義にありません。options内のパスと既定値を確認してください。") from None
    total = math.prod(len(d.values) for d in req.dimensions)
    if total > 10**12:
        raise TuningError("候補直積が大きすぎます。探索項目または候補値を減らしてください。")
    if req.method == "grid" and total > req.max_candidates:
        raise TuningError(f"網羅探索は {total} 候補です。候補上限を増やすか、候補値を減らしてください（最大128）。")
    # Validate joint configurations per candidate: testing one value in isolation
    # would incorrectly reject valid pairs such as coupled min/max thresholds.
    candidate_profile(base, req, {}, req.budgets_s[0], req.seeds[0])
    nearest = [min(range(len(d.values)), key=lambda i: abs(d.values[i]-originals[d.path])
               if type(d.values[i]) in (float, int) and type(originals[d.path]) in (float, int)
               else (0 if d.values[i] == originals[d.path] else 1)) for d in req.dimensions]
    state = {"phase": "search", "round": 0, "stage": 0, "candidates": [
        {"id": 0, "params": originals, "coord": nearest, "baseline": True}],
        "active": [0], "waiting": [], "history": [], "current": 0, "step": 0, "winner": None,
        "simulator_version": __version__}
    if req.method != "annealing":
        indices = range(total) if req.method == "grid" else random.Random(req.search_seed).sample(range(total), min(total, req.max_candidates))
        for i in indices:
            coord = _coordinates(req, i)
            params = _params(req, coord)
            if _key(params) != _key(originals):
                cid = len(state["candidates"])
                state["candidates"].append(dict(id=cid, params=params, coord=coord, baseline=False))
                state["active"].append(cid)
    # Upper bound, including the separate unchanged reference and holdout runs.
    count = min(total, req.max_candidates)+1
    if req.method == "grid":
        seconds = count*req.budgets_s[-1]*len(req.seeds)
        runs = count*len(req.seeds)
    elif req.method == "annealing":
        seconds = count*req.budgets_s[0]*len(req.seeds)
        seconds += (min(req.finalists, count)+1)*req.budgets_s[-1]*len(req.seeds)
        runs = (count+min(req.finalists, count)+1)*len(req.seeds)
    else:
        seconds, runs = 0, 0
        for duration in req.budgets_s:
            seconds += count*duration*len(req.seeds)
            runs += count*len(req.seeds)
            count = math.ceil((count-1)/req.reduction)+1
    seconds += 2*req.budgets_s[-1]*len(req.validation_seeds)
    runs += 2*len(req.validation_seeds)
    warnings = [
        "短時間の順位は長時間の順位と一致する保証がありません。遅い学習・移動周期・送信開始時刻を覆う評価時間を指定してください。",
        "各評価は時刻0・未学習の独立エージェントから開始します。段階間で学習状態は引き継ぎません。",
        "同じseed集合を全候補に使いますが、モデルが乱数を消費する順序によって後続の乱数対応は変わる場合があります。",
    ]
    first = req.budgets_s[-1] if req.method == "grid" else req.budgets_s[0]
    warmup = max([3]+[definition.options[k] for k in ("batch_size", "history_range") if type(definition.options.get(k)) in (int, float)])
    for d in req.dimensions:
        if d.path in ("batch_size", "history_range"):
            warmup = max([warmup]+[v for v in d.values if type(v) in (int, float)])
    for group in base.groups:
        if group.name not in req.groups:
            continue
        timing = definition if req.category == "timing" else base.component(group.timing, "timing")
        opts = timing.options
        interval = opts.get("interval_s", 60) if timing.kind == "periodic" else opts.get("mean_s", 60) if timing.kind == "poisson" else None
        start = opts.get("start") or {}
        if interval:
            estimate = max(0, first-max(opts.get("first_s", 0), start.get("max_s", 0), start.get("at_s", 0)))/interval
            if estimate < warmup:
                warnings.append(f"{group.name}: 初期評価での生成数の目安はEDあたり {estimate:.1f} 回で、学習・履歴確認の目安 {warmup:g} 回より少ないです。初期評価時間を延長してください。MAC待機を含めると実送信はさらに少ない場合があります。")
    if req.category != "policy":
        warnings.append("移動・送信タイミングを調整すると実験条件自体が変わります。アルゴリズムの同条件比較とは区別してください。")
    if len(req.seeds) < 2 or len(req.validation_seeds) < 2:
        warnings.append("seedが1個の段階は、seed間のばらつきを評価できません。")
    if req.budgets_s[-1] < base.duration_s:
        warnings.append("最終確認も元実験より短時間です。保存したモデルは元の実験時間でも再評価してください。")
    if req.method == "annealing":
        warnings.append("局所探索の隣接関係は候補値の登録順です。数値は小さい順に並べてください。候補の再訪評価は省略します。")
    if any((g.mcu or base.energy.mcu).enabled and (g.mcu or base.energy.mcu).mode == "measured" for g in base.groups):
        warnings.append("MCUの実時間測定はCPU負荷・並列数に依存します。消費電力を探索指標にするときは一定の実行環境または固定時間モデルを使ってください。")
    return base, state, dict(grid_size=total, max_runs=runs, simulated_seconds_upper=seconds,
                            full_run_equivalents=seconds/base.duration_s, ed_count=sum(g.count for g in base.groups),
                            warnings=warnings)


def create(store, req):
    base, state, estimate = prepare(store, req)
    sid, eid = uuid.uuid4().hex, uuid.uuid4().hex
    config = req.model_dump(mode="json")
    config["yaml"] = dump_profile(base)  # freeze references and full environment
    state["estimate"] = estimate
    state["implementation"] = metadata(req.category, req.definition.kind)["module"]
    with store.connect() as db:
        db.execute("INSERT INTO experiments VALUES(?,?,?,?)", (eid, "[調整] "+req.name, "ハイパーパラメータ探索。通常実験と分けて比較してください。", time.time()))
        db.execute("INSERT INTO tuning_studies(id,name,config,state,status,created,experiment_id) VALUES(?,?,?,?,'running',?,?)",
                   (sid, req.name, _key(config), _key(state), time.time(), eid))
    return detail(store, sid)


def metric(summary, req):
    a = (summary or {}).get("abstract", {})
    n, eligible = a.get("ed_count", 0), a.get("eligible_ed_count", 0)
    if not n or a.get("mean_ed_prr") is None or not a.get("uplinks"):
        raise TuningError("判定済みULがありません。評価時間を延長してください。")
    coverage = eligible/n
    if coverage < req.min_coverage or a["uplinks"]/n < req.min_uplinks_per_ed:
        raise TuningError("送信を観測できたED割合または平均UL数が下限未満です。評価時間・送信開始時刻を確認してください。")
    # Inactive devices count as zero rather than silently improving the objective.
    prr = a["mean_ed_prr"]*coverage
    fractions = {k: a.get("fractions", {}).get(k, 0)*coverage for k in ("Interference", "LostBecauseTx", "UnderSensitivity")}
    jain = a.get("fairness", {}).get("jain")
    jain = jain*coverage if jain is not None else 0.
    energy = (summary or {}).get("energy", {})
    received = a["uplinks"]*a.get("pooled_prr", 0)
    per_success = (energy.get("radio_j", 0)+(energy.get("mcu_j", 0) if req.weights.include_mcu else 0))/received if received and a.get("energy", {}).get("measured") else None
    if req.weights.energy and per_success is None:
        raise TuningError("受信成功が0件のため、成功ULあたりの消費エネルギーを評価できません。")
    w = req.weights
    score = prr-w.interference*fractions["Interference"]-w.gateway_tx*fractions["LostBecauseTx"]-w.sensitivity*fractions["UnderSensitivity"]+w.fairness*jain-w.energy*(per_success or 0)
    if not math.isfinite(score):
        raise TuningError("評価値が有限値ではありません。設定と実験結果を確認してください。")
    return dict(score=score, prr=prr, coverage=coverage, uplinks_per_ed=a["uplinks"]/n,
                jain=jain, energy_j_per_success=per_success, fractions=fractions,
                censored=a.get("censored", 0), wall_s=summary.get("wall_s"))


def _aggregate(store, req, trial):
    items, errors = [], []
    for jid in trial["jobs"]:
        job = store.get(jid)
        if job["status"] != "completed":
            errors.append(f"{jid[:8]}: {job['error'] or job['status']}")
            continue
        try:
            items.append(metric(job["summary"], req))
        except TuningError as exc:
            errors.append(str(exc))
    if errors or len(items) != len(trial["seeds"]):
        return dict(valid=False, errors=errors, samples=items)
    for item, seed in zip(items, trial["seeds"]):
        item["seed"] = seed
    scores = [x["score"] for x in items]
    return dict(valid=True, mean=statistics.mean(scores), std=statistics.stdev(scores) if len(scores)>1 else None,
                samples=items, min=min(scores), max=max(scores))


def _rank(state, round_no):
    rows = [r for r in state["history"] if r["round"] == round_no and r["result"].get("valid")]
    return sorted(rows, key=lambda r: (-r["result"]["mean"], r["candidate"]))


def _propose(req, state):
    seen = {_key(c["params"]) for c in state["candidates"]}
    total = math.prod(len(d.values) for d in req.dimensions)
    rng = random.Random(req.search_seed+state["step"]*104729)
    coord = state["candidates"][state["current"]]["coord"]
    near = []
    for axis, d in enumerate(req.dimensions):
        for delta in (-1, 1):
            c = coord.copy()
            c[axis] += delta
            if 0 <= c[axis] < len(d.values) and _key(_params(req, c)) not in seen:
                near.append(c)
    if near and rng.random() >= req.restart_probability:
        return rng.choice(near)
    # Deterministic fallback scan with a random start; no giant Cartesian list.
    start = rng.randrange(total)
    for offset in range(min(total, len(seen)+1)):
        c = _coordinates(req, (start+offset) % total)
        if _key(_params(req, c)) not in seen:
            return c
    return None


def _after_round(req, state):
    ranked = _rank(state, state["round"])
    if state["phase"] == "validation":
        state["phase"] = "done"
        return
    if req.method == "annealing" and state["phase"] == "search":
        if state["step"]:
            current = next((r for r in state["history"] if r["phase"] == "search" and r["candidate"] == state["current"] and r["result"].get("valid")), None)
            proposed = ranked[0] if ranked else None
            if proposed:
                delta = proposed["result"]["mean"]-current["result"]["mean"] if current else math.inf
                temp = max(1e-12, req.temperature*req.cooling**(state["step"]-1))
                accepted = delta >= 0 or random.Random(req.search_seed+state["step"]).random() < math.exp(delta/temp)
                proposed["accepted"], proposed["temperature"] = accepted, temp
                if accepted:
                    state["current"] = proposed["candidate"]
        state["step"] += 1
        coord = _propose(req, state) if state["step"] <= req.max_candidates else None
        if coord is not None:
            cid = len(state["candidates"])
            state["candidates"].append(dict(id=cid, coord=coord, params=_params(req, coord), baseline=False))
            state["active"] = [cid]
        else:
            rows = sorted((r for r in state["history"] if r["result"].get("valid") and r["phase"] == "search"), key=lambda r: (-r["result"]["mean"], r["candidate"]))
            if not rows:
                raise TuningError("有効な候補がありません。評価時間・設定・各Runのエラーを確認してください。")
            state["active"] = list(dict.fromkeys([0]+[r["candidate"] for r in rows[:req.finalists]]))
            state["phase"] = "refine"
    elif req.method == "halving" and state["stage"] < len(req.budgets_s)-1:
        if not ranked:
            raise TuningError("この段階に有効な候補がありません。最初の評価時間・観測下限・候補値を見直してください。")
        competitors = [r["candidate"] for r in ranked if r["candidate"] != 0]
        state["active"] = [0]+competitors[:max(1, math.ceil(len(competitors)/req.reduction))]
        state["stage"] += 1
    else:
        if not ranked:
            raise TuningError("最長評価時間で有効な候補がありません。各Runのエラーと評価時間を確認してください。")
        state["winner"] = ranked[0]["candidate"]
        state["active"] = list(dict.fromkeys([0, state["winner"]]))
        state["phase"] = "validation"
    state["round"] += 1


def _schedule(store, db, row, req, state):
    base = store.parse_profile(req.yaml)
    duration = req.budgets_s[-1] if state["phase"] != "search" or req.method == "grid" else req.budgets_s[state["stage"]]
    seeds = req.validation_seeds if state["phase"] == "validation" else req.seeds
    waiting = []
    for cid in state["active"]:
        candidate = state["candidates"][cid]
        trial = dict(candidate=cid, round=state["round"], phase=state["phase"], duration_s=duration, seeds=seeds, jobs=[])
        cached = next((t for t in state["history"] if t["candidate"] == cid and
                       t["duration_s"] == duration and t["seeds"] == seeds and t["result"].get("valid")), None)
        if cached:
            trial.update(jobs=cached["jobs"], result=deepcopy(cached["result"]), reused=True)
            state["history"].append(trial)
            continue
        try:
            p = candidate_profile(base, req, candidate["params"], duration, seeds[0])
        except Exception as exc:
            trial["result"] = dict(valid=False, errors=[f"候補の設定が不正です: {exc}"], samples=[])
            state["history"].append(trial)
            continue
        for seed in seeds:
            p.seed = seed
            p.name = f"{req.name} / C{cid} / {state['phase']} / {duration:g}s / seed {seed}"
            jid = uuid.uuid4().hex
            db.execute("INSERT INTO jobs(id,name,profile,status,created,experiment_id,diagnostics) VALUES(?,?,?,'queued',?,?,?)",
                       (jid, p.name, dump_profile(p), time.time(), row["experiment_id"], "[]"))
            trial["jobs"].append(jid)
        waiting.append(trial)
    state["waiting"] = waiting
    state["scheduled"] = True


def advance(store):
    """Called by the existing supervisor, not by browser requests."""
    with store.connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM tuning_studies WHERE status='running'")]
    for sid in ids:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM tuning_studies WHERE id=?", (sid,)).fetchone()
            if row["status"] != "running":
                continue
            state, req = json.loads(row["state"]), StudyRequest.model_validate_json(row["config"])
            try:
                if state.get("simulator_version") != __version__:
                    raise TuningError("探索作成時とworkerのバージョンが異なります。同じバージョンで新しい探索を作成してください。")
                jobs = [j for t in state["waiting"] for j in t["jobs"]]
                if any(db.execute("SELECT status FROM jobs WHERE id=?", (j,)).fetchone()[0] in ("queued", "running") for j in jobs):
                    continue
                if state.get("scheduled"):
                    for trial in state["waiting"]:
                        trial["result"] = _aggregate(store, req, trial)
                        state["history"].append(trial)
                    state["waiting"] = []
                    _after_round(req, state)
                if state["phase"] == "done":
                    ok = all(r["result"].get("valid") for r in state["history"] if r["phase"] == "validation")
                    db.execute("UPDATE tuning_studies SET status=?,finished=? WHERE id=?", ("completed" if ok else "failed", time.time(), sid))
                    if not ok:
                        state["error"] = "確認用seedで評価できませんでした。確認段階のRunを点検してください。"
                else:
                    _schedule(store, db, row, req, state)
            except Exception as exc:
                state["error"] = f"探索を継続できませんでした: {exc}"
                db.execute("UPDATE tuning_studies SET status='failed',finished=? WHERE id=?", (time.time(), sid))
            db.execute("UPDATE tuning_studies SET state=? WHERE id=?", (_key(state), sid))


def listing(store):
    with store.connect() as db:
        return [dict(r) for r in db.execute("SELECT id,name,status,created,finished,experiment_id FROM tuning_studies ORDER BY created DESC LIMIT 200")]


def detail(store, sid):
    with store.connect() as db:
        row = db.execute("SELECT * FROM tuning_studies WHERE id=?", (sid,)).fetchone()
    if row is None:
        raise KeyError(sid)
    result = dict(row)
    result["config"], result["state"] = json.loads(row["config"]), json.loads(row["state"])
    with store.connect() as db:
        result["exports"] = [dict(r) for r in db.execute("SELECT candidate,category,name,created FROM tuning_exports WHERE study_id=? ORDER BY created", (sid,))]
    # Include bounded live progress, never packet logs or learned weights.
    result["live"] = []
    for trial in result["state"]["waiting"]:
        for jid in trial["jobs"]:
            j = store.get(jid)
            result["live"].append({k: j[k] for k in ("id", "name", "status", "progress", "error")})
    return result


def cancel(store, sid):
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM tuning_studies WHERE id=?", (sid,)).fetchone()
        if row is None:
            raise KeyError(sid)
        if row["status"] == "running":
            for trial in json.loads(row["state"])["waiting"]:
                for jid in trial["jobs"]:
                    db.execute("UPDATE jobs SET cancel=1 WHERE id=? AND status IN ('queued','running')", (jid,))
                    db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE id=? AND status='queued'", (time.time(), jid))
            db.execute("UPDATE tuning_studies SET status='cancelled',finished=? WHERE id=?", (time.time(), sid))
    return detail(store, sid)


def save_candidate(store, sid, cid, name):
    study = detail(store, sid)
    state, req = study["state"], StudyRequest.model_validate(study["config"])
    if not any(t["candidate"] == cid and t["result"].get("valid") for t in state["history"]):
        raise TuningError("正常に評価できた候補を選択してください。未評価・失敗候補は保存できません。")
    candidate = next(c for c in state["candidates"] if c["id"] == cid)
    definition = definition_for(req, candidate["params"])
    # Preserve the usual factory validation and atomically refuse name collisions.
    from .components import make
    import numpy as np
    p = store.parse_profile(req.yaml)
    context = dict(rng=np.random.default_rng(0))
    if req.category == "policy":
        context["action_space"] = p.action_space
    if req.category == "mobility":
        context.update(initial=(0., 0., 1.5), area=p.area_m)
    make(definition, req.category, **context)
    if not name.strip() or len(name) > 120:
        raise TuningError("保存名を1〜120文字で入力してください。")
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM models WHERE category=? AND name=?", (req.category, name)).fetchone():
            raise TuningError("同名のモデルが存在します。新しい保存名を指定してください。")
        db.execute("INSERT INTO models VALUES(?,?,?,?)", (req.category, name, definition.model_dump_json(), time.time()))
        db.execute("INSERT INTO tuning_exports VALUES(?,?,?,?,?)", (sid, cid, req.category, name, time.time()))
    return dict(saved=name, category=req.category, definition=definition.model_dump(mode="json"), study_id=sid, candidate=cid)


class SuggestionRequest(Model):
    category: Literal["policy", "mobility", "timing"]
    definition: Component


class SaveRequest(Model):
    candidate: int = Field(ge=0)
    name: str = Field(min_length=1, max_length=120)


def mount_tuning(app, store, dependencies):
    from fastapi import HTTPException
    from .diagnostics import exception_diagnostics, issue

    def invoke(fn, *args):
        try:
            return fn(*args)
        except KeyError:
            raise HTTPException(404, "指定した探索がありません。") from None
        except TuningError as exc:
            raise HTTPException(422, [issue("TUNING", str(exc), "対象モデル、評価時間、候補値と各Runの結果を確認してください。")]) from None
        except ValueError as exc:
            raise HTTPException(422, exception_diagnostics(exc)) from None

    @app.post("/api/tuning/suggestions", dependencies=dependencies)
    def parameters(body: SuggestionRequest):
        return invoke(suggestions, body.category, body.definition)

    @app.post("/api/tuning/preview", dependencies=dependencies)
    def preview(body: StudyRequest):
        return invoke(prepare, store, body)[2]

    @app.post("/api/tuning", dependencies=dependencies)
    def start(body: StudyRequest):
        return invoke(create, store, body)

    @app.get("/api/tuning", dependencies=dependencies)
    def studies():
        return listing(store)

    @app.get("/api/tuning/{sid}", dependencies=dependencies)
    def study(sid: str):
        return invoke(detail, store, sid)

    @app.post("/api/tuning/{sid}/cancel", dependencies=dependencies)
    def stop(sid: str):
        return invoke(cancel, store, sid)

    @app.post("/api/tuning/{sid}/save", dependencies=dependencies)
    def save(sid: str, body: SaveRequest):
        return invoke(save_candidate, store, sid, body.candidate, body.name)
