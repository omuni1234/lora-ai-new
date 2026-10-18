from __future__ import annotations
import hmac
import json
import os
from pathlib import Path
import time
from fastapi import FastAPI, HTTPException, Request, Depends, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import Profile, load_profile, dump_profile
from .engine import Simulation
from .jobs import Store
from .analysis import analyze_run, compare_runs
from .visualization import layout, traces, packet_details, preflight
from .catalog import DEFAULTS
from .registry import inventory, metadata
from .ui_metadata import describe_schema


class YamlBody(BaseModel):
    yaml: str = Field(max_length=2_000_000)


class SubmitBody(YamlBody):
    experiment_id: str | None = None

class PreviewBody(YamlBody):
    horizon_s: float | None = Field(None,ge=0,le=1e9)
    limit: int = Field(80,ge=1,le=200)
    samples: int = Field(12,ge=2,le=30)

class ExperimentBody(BaseModel):
    name: str = Field(min_length=1,max_length=160)
    description: str = Field(default="",max_length=10000)

class AnalysisBody(BaseModel):
    experiment_ids: list[str] = Field(min_length=1,max_length=50)
    tx_start: int = Field(default=1,ge=1)
    tx_end: int | None = Field(default=None,ge=1)
    include_mcu: bool = False
    bins: int = Field(default=100,ge=1,le=500)
    ed_count: int | None = Field(default=None,ge=1)


def create_app(root=None):
    store = Store(root or os.getenv("LORASIM_DATA","data"))
    app = FastAPI(title="LoRaSim Research",version="0.3.0")
    token = os.getenv("LORASIM_API_TOKEN","")
    def auth(request: Request):
        if token and not hmac.compare_digest(request.headers.get("Authorization",""),f"Bearer {token}"):
            raise HTTPException(401,"Authentication required")
    def bad(exc):
        raise HTTPException(422,str(exc))
    @app.get("/api/health")
    def health():
        return {"status":"ok"}
    @app.get("/api/worker",dependencies=[Depends(auth)])
    def status():
        path = store.root/"worker.json"
        value = json.loads(path.read_text()) if path.exists() else {}
        value["online"] = time.time()-value.get("heartbeat",0)<10
        return value
    @app.get("/api/schema",dependencies=[Depends(auth)])
    def schema():
        return describe_schema(Profile.model_json_schema())
    @app.post("/api/validate",dependencies=[Depends(auth)])
    def validate(body: YamlBody):
        try:
            p = store.parse_profile(body.yaml)
            Simulation(p,store.root/"_validation")
            return p.model_dump(mode="json")
        except Exception as exc:
            bad(exc)
    @app.get("/api/profiles",dependencies=[Depends(auth)])
    def profiles():
        return store.profiles()
    @app.put("/api/profiles/{name}",dependencies=[Depends(auth)])
    def save(name:str,body:YamlBody):
        try:
            store.save_profile(name,body.yaml)
            return {"saved":name}
        except Exception as exc:
            bad(exc)
    @app.post("/api/jobs",status_code=201,dependencies=[Depends(auth)])
    def submit(body:SubmitBody):
        try:
            return {"id":store.submit(body.yaml,body.experiment_id)}
        except Exception as exc:
            bad(exc)
    @app.get("/api/jobs",dependencies=[Depends(auth)])
    def jobs(experiment_id:str | None=None):
        return store.list(500,experiment_id)
    def job(jid):
        try:
            return store.get(jid)
        except KeyError:
            raise HTTPException(404,"Job not found")
    @app.get("/api/jobs/{jid}",dependencies=[Depends(auth)])
    def get(jid:str):
        return job(jid)
    @app.post("/api/jobs/{jid}/cancel",dependencies=[Depends(auth)])
    def cancel(jid:str):
        job(jid)
        store.cancel(jid)
        return {"cancel_requested":True}
    @app.get("/api/jobs/{jid}/files",dependencies=[Depends(auth)])
    def files(jid:str):
        job(jid)
        root = store.root/"runs"/jid
        return [{"name":str(p.relative_to(root)),"bytes":p.stat().st_size} for p in sorted(root.rglob("*")) if p.is_file() and p.suffix!=".tmp"]
    @app.get("/api/jobs/{jid}/files/{name:path}",dependencies=[Depends(auth)])
    def file(jid:str,name:str):
        job(jid)
        root = (store.root/"runs"/jid).resolve()
        target = (root/name).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise HTTPException(404,"File not found")
        return FileResponse(target,filename=target.name)
    @app.post("/api/profile-yaml",dependencies=[Depends(auth)])
    def profile_yaml(body:YamlBody):
        try:
            return {"yaml":dump_profile(store.parse_profile(body.yaml))}
        except Exception as exc:
            bad(exc)
    @app.get("/api/defaults",dependencies=[Depends(auth)])
    def defaults():
        return {"profile":Profile().model_dump(mode="json"),"components":DEFAULTS,"plugins":inventory()}
    @app.get("/api/plugins/schema",dependencies=[Depends(auth)])
    def plugin_schema(category:str,kind:str):
        try:
            return metadata(category,kind)
        except Exception as exc:
            bad(exc)
    @app.get("/api/models",dependencies=[Depends(auth)])
    def models():
        return store.catalog()
    @app.put("/api/models/{category}/{name}",dependencies=[Depends(auth)])
    def model(category:str,name:str,definition:dict):
        try:
            store.save_model(category,name,definition)
            return {"saved":name}
        except Exception as exc:
            bad(exc)
    @app.get("/api/experiments",dependencies=[Depends(auth)])
    def experiments():
        return store.experiments()
    @app.get("/api/experiments/{eid}/info",dependencies=[Depends(auth)])
    def experiment_info(eid:str):
        try:
            return store.experiment_info(eid)
        except KeyError:
            raise HTTPException(404,"Experiment not found")
    @app.post("/api/experiments",status_code=201,dependencies=[Depends(auth)])
    def experiment(body:ExperimentBody):
        try:
            return {"id":store.create_experiment(body.name,body.description)}
        except Exception as exc:
            bad(exc)
    @app.post("/api/analysis",dependencies=[Depends(auth)])
    def analysis(body:AnalysisBody):
        try:
            runs, skipped = [], []
            with store.connect() as db:
                for eid in dict.fromkeys(body.experiment_ids):
                    experiment = db.execute("SELECT name FROM experiments WHERE id=?",(eid,)).fetchone()
                    if experiment is None:
                        raise ValueError("unknown Experiment")
                    for row in db.execute("SELECT id,status FROM jobs WHERE experiment_id=? ORDER BY created",(eid,)):
                        if row['status']=='cancelled' and not (store.root/'runs'/row['id']/'profile.yaml').exists():
                            skipped.append({'id':row['id'],'reason':'cancelled before simulation started'})
                            continue
                        if row['status'] in {'completed','cancelled'}:
                            runs.append({"id":row['id'],"experiment_id":eid,"experiment_name":experiment['name'],"root":store.root/'runs'/row['id']})
            if len(runs)>200:
                raise ValueError("Select at most 200 runs per analysis request")
            result = compare_runs(runs,body.tx_start,body.tx_end,body.include_mcu,body.bins,body.ed_count)
            result["skipped_runs"] = skipped
            return result
        except Exception as exc:
            bad(exc)
    @app.get("/api/jobs/{jid}/packets/{packet_id}",dependencies=[Depends(auth)])
    def packet(jid:str,packet_id:int):
        job(jid)
        value = packet_details(store.root/'runs'/jid,packet_id)
        if not value['transmissions']:
            raise HTTPException(404,"Packet is not in committed tracking parts")
        return value
    @app.get("/api/jobs/{jid}/abstract",dependencies=[Depends(auth)])
    def abstract(jid:str):
        item = job(jid)
        if item['summary'] and 'abstract' in item['summary']:
            return item['summary']['abstract']
        raise HTTPException(409,"Abstract is available after a schema-v2 run finishes")
    @app.post("/api/preview",dependencies=[Depends(auth)])
    def preview(body:PreviewBody):
        try:
            return preflight(store.parse_profile(body.yaml),body.horizon_s,body.limit,body.samples)
        except Exception as exc:
            bad(exc)
    @app.get("/api/jobs/{jid}/visualization",dependencies=[Depends(auth)])
    def visualization(jid:str,at_s:float | None=Query(None,ge=0),limit:int=Query(500,ge=1,le=2000),traffic:bool=True):
        item = job(jid)
        p = load_profile(item['profile'],text=True)
        when = min(p.duration_s,item['sim_time'] if at_s is None else at_s)
        value = layout(p,when,limit)
        if traffic:
            value.update(traces(store.root/'runs'/jid,limit=min(2000,limit*2),before_s=when))
        return value
    static = Path(__file__).with_name("static")
    app.mount("/static",StaticFiles(directory=static),name="static")
    @app.get("/")
    def index():
        return FileResponse(static/"index.html")
    return app
