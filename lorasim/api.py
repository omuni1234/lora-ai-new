from __future__ import annotations
import hmac
import json
import os
from pathlib import Path
import time
from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from .config import Profile, load_profile
from .engine import Simulation
from .jobs import Store


class YamlBody(BaseModel):
    yaml: str = Field(max_length=2_000_000)


def create_app(root=None):
    store = Store(root or os.getenv("LORASIM_DATA","data"))
    app = FastAPI(title="LoRaSim Research",version="0.1.0")
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
        return Profile.model_json_schema()
    @app.post("/api/validate",dependencies=[Depends(auth)])
    def validate(body: YamlBody):
        try:
            p = load_profile(body.yaml,text=True)
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
    def submit(body:YamlBody):
        try:
            return {"id":store.submit(body.yaml)}
        except Exception as exc:
            bad(exc)
    @app.get("/api/jobs",dependencies=[Depends(auth)])
    def jobs():
        return store.list()
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
    static = Path(__file__).with_name("static")
    app.mount("/static",StaticFiles(directory=static),name="static")
    @app.get("/")
    def index():
        return FileResponse(static/"index.html")
    return app
