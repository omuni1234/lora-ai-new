"""Expand ED counts × selectors into ordinary, atomically queued Runs.

This is an API-side authoring feature. Workers need no new job type or profile
fields, and keep using the existing queue and Experiment analysis contracts.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from typing import Annotated

from fastapi import HTTPException
from pydantic import Field, model_validator

from .config import Component, Model, Profile, dump_profile
from .diagnostics import configuration_warnings, exception_diagnostics, issue, validate_options
from .engine import Simulation


class Choice(Model):
    name: str = Field(min_length=1, max_length=120)
    definition: Component
    experiment_id: str | None = Field(None, min_length=1, max_length=128)


class Batch(Model):
    yaml: str = Field(max_length=2_000_000)
    ed_counts: list[Annotated[int, Field(strict=True, ge=1, le=1_000_000)]] = Field(min_length=1, max_length=50)
    policies: list[Choice] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique(self):
        if len(self.ed_counts) != len(set(self.ed_counts)):
            raise ValueError("ED数の候補が重複しています。重複を取り除いてください。")
        names = [p.name.strip() for p in self.policies]
        if not all(names) or len(names) != len(set(names)):
            raise ValueError("アルゴリズム名には空欄・重複のない名前を指定してください。")
        destinations = [p.experiment_id for p in self.policies if p.experiment_id]
        if len(destinations) != len(set(destinations)):
            raise ValueError("異なるアルゴリズムは別のExperimentに割り当ててください。")
        if len(self.ed_counts) * len(self.policies) > 100:
            raise ValueError("一度に登録できる組み合わせは100 Runまでです。候補を分けてください。")
        return self


class Submission(Batch):
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    plan_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def allocate(counts, total):
    """Largest remainders with a minimum of one ED per original group."""
    if total < len(counts):
        raise ValueError("総ED数はEDグループ数以上にしてください。各グループに最低1台を残します。")
    # Integer numerators avoid floating point tie breaking.
    denominator = sum(counts)
    quotas = [n * total for n in counts]
    result = [max(1, q // denominator) for q in quotas]
    while sum(result) > total:
        i = max((i for i, n in enumerate(result) if n > 1),
                key=lambda i: (result[i] * denominator - quotas[i], -i))
        result[i] -= 1
    for i in sorted(range(len(counts)), key=lambda i: (quotas[i] - result[i] * denominator, -i), reverse=True)[:total - sum(result)]:
        result[i] += 1
    return result


def prepare(store, body):
    base = store.parse_profile(body.yaml)
    if not base.groups:
        raise ValueError("EDグループを1つ以上設定してください。")
    rows, profiles, destinations = [], [], []
    notes = [
        "選択したアルゴリズムを全EDグループと選択器の条件付き割り当てに適用します。",
        "ED数は全グループの合計です。複数グループは元の台数比を基に整数配分し、各グループに最低1台を残します。",
        "送信・移動・観測・PHY/MAC・実験時間・seedは元の設定を維持します。ED数が変わると配置やED IDの対応は変わり得ます。",
    ]
    with store.connect() as db:
        for choice in body.policies:
            row = db.execute("SELECT name FROM experiments WHERE id=?", (choice.experiment_id,)).fetchone() if choice.experiment_id else None
            if choice.experiment_id and row is None:
                raise ValueError(f"{choice.name}: 保存先Experimentが見つかりません。一覧を更新してください。")
            destinations.append(dict(id=choice.experiment_id, name=row["name"] if row else f"{base.name} · {choice.name}"[:160]))
    if any(c.experiment_id for c in body.policies):
        notes.append("既存Experimentへの追加では、既存Runと同じ選択器・比較条件かを確認してください。分析ではExperiment内のRunが集計されます。")
    for total in body.ed_counts:
        counts = allocate([g.count for g in base.groups], total)
        changed = any(n != g.count for n, g in zip(counts, base.groups))
        if changed and any(a.ids for a in base.assignments):
            raise ValueError("ED ID指定の割り当てがあるため台数を変更できません。グループ名またはタグによる割り当てへ変更してください。")
        for n, g in zip(counts, base.groups):
            if n != g.count and g.placement == "explicit":
                raise ValueError(f"{g.name}: 明示座標の台数は変更できません。uniform/grid配置を使うか、台数別のプロファイルを用意してください。")
        for index, choice in enumerate(body.policies):
            try:
                raw = base.model_dump(mode="json")
                raw["name"] = f"{total}-{choice.name}-{base.name}"[:160]
                # Resolve now; submitted Runs never follow later library edits.
                policy = base.component(choice.definition, "policy")
                raw["models"].setdefault("policy", {})[choice.name] = policy.model_dump(mode="json")
                for g, n in zip(raw["groups"], counts):
                    g["count"], g["policy"] = n, {"ref": choice.name}
                for assignment in raw["assignments"]:
                    if assignment["policy"] is not None:
                        assignment["policy"] = {"ref": choice.name}
                p = Profile.model_validate(raw)
                validate_options(p)
                # Same initialization check as single-Run submission; no events
                # execute, no tracker opens, no model state is shared with Runs.
                Simulation(p, store.root / "_validation")
                warnings = configuration_warnings(p)
                text = dump_profile(p)
                if len(text) > 2_000_000:
                    raise ValueError("展開後のプロファイルが2 MBを超えています。未使用のモデル定義を減らしてください。")
            except Exception as exc:
                ds = exception_diagnostics(exc)
                for d in ds:
                    d["message"] = f"{total} ED × {choice.name}: {d['message']}"
                raise HTTPException(422, ds) from exc
            profiles.append(text)
            rows.append(dict(name=p.name, ed_count=total, algorithm=choice.name, kind=policy.kind,
                             choice_index=index, experiment=destinations[index],
                             groups=[dict(name=g.name, count=g.count) for g in p.groups],
                             seed=p.seed, duration_s=p.duration_s, diagnostics=warnings))
    plan_hash = digest(dict(profiles=profiles, destinations=destinations, names=[c.name for c in body.policies]))
    return dict(plan_hash=plan_hash, run_count=len(rows), new_experiments=sum(not d["id"] for d in destinations),
                rows=rows, notes=notes, destinations=destinations), profiles


def submit(store, body):
    fingerprint = digest(body.model_dump(mode="json"))

    def previous(db):
        row = db.execute("SELECT fingerprint,response FROM batch_submissions WHERE id=?", (body.request_id,)).fetchone()
        if row:
            if row["fingerprint"] != fingerprint:
                raise HTTPException(409, [issue("BATCH_REUSED", "登録IDが別の一括実行に使用されています。",
                                              "組み合わせを再確認して、新しい登録IDで実行してください。")])
            return json.loads(row["response"])

    with store.connect() as db:
        result = previous(db)
    if result is not None:
        return result
    plan, profiles = prepare(store, body)
    if plan["plan_hash"] != body.plan_hash:
        raise HTTPException(409, [issue("BATCH_CHANGED", "確認後にモデル定義またはExperimentが変更されました。",
                                      "「組み合わせを確認」を再実行してから登録してください。")])
    with store.connect() as db:
        db.execute("BEGIN IMMEDIATE")
        result = previous(db)
        if result is not None:
            return result
        now = time.time()
        ids = []
        for destination in plan["destinations"]:
            eid = destination["id"] or uuid.uuid4().hex
            if destination["id"]:
                if not db.execute("SELECT 1 FROM experiments WHERE id=?", (eid,)).fetchone():
                    raise ValueError("保存先Experimentが見つかりません。一覧を更新してください。")
            else:
                db.execute("INSERT INTO experiments(id,name,description,created) VALUES(?,?,?,?)",
                           (eid, destination["name"], "ED数×選択アルゴリズムの一括比較", now))
            ids.append(eid)
        jobs = []
        for i, (row, profile) in enumerate(zip(plan["rows"], profiles)):
            jid = uuid.uuid4().hex
            eid = ids[row["choice_index"]]
            db.execute("INSERT INTO jobs(id,name,profile,status,created,experiment_id,diagnostics) VALUES(?,?,?,'queued',?,?,?)",
                       (jid, row["name"], profile, now + i * 0.000001, eid, json.dumps(row["diagnostics"], ensure_ascii=False)))
            jobs.append(dict(id=jid, experiment_id=eid, ed_count=row["ed_count"], algorithm=row["algorithm"], name=row["name"]))
        result = dict(request_id=body.request_id, jobs=jobs, experiment_ids=ids, run_count=len(jobs))
        db.execute("INSERT INTO batch_submissions VALUES(?,?,?,?)",
                   (body.request_id, fingerprint, json.dumps(result, ensure_ascii=False), now))
    return result


def mount_batches(app, store, dependencies):
    def failure(exc):
        if isinstance(exc, HTTPException):
            raise exc
        if isinstance(exc, ValueError) and any("\u3040" <= c <= "\u9fff" for c in str(exc)):
            ds = [issue("BATCH_CONFIGURATION", str(exc), "候補一覧と元のプロファイルを確認し、組み合わせを再確認してください。")]
        else:
            ds = exception_diagnostics(exc)
        raise HTTPException(422 if isinstance(exc, ValueError) else 500, ds) from exc

    @app.post("/api/batches/preview", dependencies=dependencies)
    def preview(body: Batch):
        try:
            return prepare(store, body)[0]
        except Exception as exc:
            failure(exc)

    @app.post("/api/batches", status_code=201, dependencies=dependencies)
    def enqueue(body: Submission):
        try:
            return submit(store, body)
        except Exception as exc:
            failure(exc)
