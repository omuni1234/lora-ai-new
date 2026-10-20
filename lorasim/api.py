from __future__ import annotations
import hmac
import json
import os
from pathlib import Path
import time
from fastapi import FastAPI, HTTPException, Request, Depends, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging
import yaml
from .diagnostics import exception_diagnostics, issue
from pydantic import BaseModel, Field
from .config import Profile, load_profile, dump_profile
from .jobs import Store
from .analysis import analyze_run, compare_runs
from .visualization import layout, traces, packet_details, preflight
from .catalog import DEFAULTS
from .registry import inventory, metadata
from .ui_metadata import describe_schema
from .calculator import CalculatorRequest, calculate
from .web_assets import mount_ui
from .tradeoff import ForecastOptions, ForecastError, forecast
from .consultant import configuration as ai_configuration, bounded_context, consult, ConsultantError
from .experience import ExperienceOptions, ExperienceError, experience


class YamlBody(BaseModel):
    yaml: str = Field(max_length=2_000_000)


class SubmitBody(YamlBody):
    experiment_id: str | None = None

class WorkerBody(BaseModel):
    workers: int = Field(ge=1, le=256, strict=True)

class PreviewBody(YamlBody):
    horizon_s: float | None = Field(None,ge=0,le=1e9)
    limit: int = Field(80,ge=1,le=200)
    samples: int = Field(12,ge=2,le=30)

class ForecastBody(YamlBody):
    options: ForecastOptions = Field(default_factory=ForecastOptions)

class ExperienceBody(YamlBody):
    options: ExperienceOptions = Field(default_factory=ExperienceOptions)

class ConsultantPreviewBody(YamlBody):
    job_id: str | None = Field(None,max_length=128)
    forecast_options: ForecastOptions | None = None

class ConsultantAskBody(BaseModel):
    context: dict
    question: str = Field(min_length=1,max_length=6000)

class ExperimentBody(BaseModel):
    name: str = Field(min_length=1,max_length=160)
    description: str = Field(default="",max_length=10000)

from .analysis_modules import Selection as AnalysisSelection

class AnalysisBody(BaseModel):
    modules: list[AnalysisSelection] | None = Field(None,max_length=20)
    experiment_ids: list[str] = Field(min_length=1,max_length=50)
    tx_start: int = Field(default=1,ge=1)
    tx_end: int | None = Field(default=None,ge=1)
    include_mcu: bool = False
    bins: int = Field(default=100,ge=1,le=500)
    ed_count: int | None = Field(default=None,ge=1)


def create_app(root=None):
    store = Store(root or os.getenv("LORASIM_DATA","data"))
    app = FastAPI(title="LoRaSim Research",version="0.11.0")
    token = os.getenv("LORASIM_API_TOKEN","")
    def auth(request: Request):
        if token and not hmac.compare_digest(request.headers.get("Authorization",""),f"Bearer {token}"):
            raise HTTPException(401,"Authentication required")
    def response_error(exc, status=422):
        diagnostics = exception_diagnostics(exc)
        return JSONResponse(status_code=status, content={"detail":diagnostics[0]["message"],"diagnostics":diagnostics})
    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        return response_error(exc)
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request, exc):
        messages = {
            401:("APIの認証に失敗しました。", "右上の接続設定でBearer tokenを確認してください。"),
            403:("この操作は許可されていません。", "実行ユーザーとアクセス権限を確認してください。"),
            404:("指定したデータまたはページが見つかりません。", "一覧を更新して選び直してください。パケットは書き出し済みの記録だけを参照できます。"),
            409:("現在の状態ではこのデータを取得できません。", "アブストラクトは対応する記録形式のRunが終了した後に表示できます。"),
            405:("この操作方法には対応していません。", "画面を再読み込みし、対応する操作ボタンを使用してください。"),
        }
        message, hint = messages.get(exc.status_code, ("リクエストを処理できませんでした。", "入力内容と技術詳細を確認してください。"))
        ds = exc.detail if isinstance(exc.detail, list) else [issue('HTTP_'+str(exc.status_code),message,hint,detail=exc.detail)]
        return JSONResponse(status_code=exc.status_code,content={"detail":ds[0]["message"],"diagnostics":ds})
    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logging.getLogger(__name__).error("API processing failed",exc_info=exc)
        return response_error(exc,500)
    def bad(exc):
        status = 422 if isinstance(exc,(ValueError, yaml.YAMLError)) else 500
        if status == 500:
            logging.getLogger(__name__).exception("API operation failed")
        raise HTTPException(status,exception_diagnostics(exc)) from exc
    @app.get("/api/health")
    def health():
        return {"status":"ok"}
    @app.get("/api/worker",dependencies=[Depends(auth)])
    def status():
        return store.worker_status()
    @app.put("/api/worker/settings",dependencies=[Depends(auth)])
    def worker_settings(body: WorkerBody):
        store.set_worker_limit(body.workers)
        return store.worker_status()
    @app.delete("/api/worker/settings",dependencies=[Depends(auth)])
    def reset_worker_settings():
        store.reset_worker_limit()
        return store.worker_status()
    @app.get("/api/schema",dependencies=[Depends(auth)])
    def schema():
        return describe_schema(Profile.model_json_schema())
    @app.post("/api/validate",dependencies=[Depends(auth)])
    def validate(body: YamlBody, report: bool=False):
        try:
            p, diagnostics = store.check_profile(body.yaml)
            value = p.model_dump(mode="json")
            return {"profile":value,"diagnostics":diagnostics} if report else value
        except Exception as exc:
            bad(exc)
    @app.post("/api/diagnostics",dependencies=[Depends(auth)])
    def diagnostics(body: YamlBody):
        try:
            p, messages = store.check_profile(body.yaml,construct=False)
            return {"diagnostics":messages,"scope":"configuration"}
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
            jid = store.submit(body.yaml,body.experiment_id)
            return {"id":jid,"diagnostics":store.get(jid)["diagnostics"]}
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
        p=Profile.model_validate({"groups":[{"name":"devices","count":10,"policy":{"kind":"fixed","action_space":{"spreading_factors":[7],"frequencies_hz":[868100000],"coding_rates":[1],"powers_dbm":[14]}}}]})
        return {"profile":p.model_dump(mode="json"),"components":DEFAULTS,"plugins":inventory()}
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
    @app.get("/api/analysis/modules",dependencies=[Depends(auth)])
    def analysis_modules():
        from .analysis_modules import inventory
        return inventory()
    @app.get("/api/analysis/modules/schema",dependencies=[Depends(auth)])
    def analysis_module_schema(kind:str):
        from .analysis_modules import metadata
        try: return metadata(kind)
        except Exception as exc: bad(exc)
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
            result = compare_runs(runs,body.tx_start,body.tx_end,body.include_mcu,body.bins,body.ed_count,body.modules)
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
            p, diagnostics = store.check_profile(body.yaml,construct=False)
            return {**preflight(p,body.horizon_s,body.limit,body.samples),"diagnostics":diagnostics}
        except Exception as exc:
            bad(exc)
    def planning_error(exc, code):
        raise HTTPException(422,[issue(code,str(exc),"画面の説明に従って入力・接続設定を確認してください。")]) from exc
    @app.post("/api/calculator/forecast",dependencies=[Depends(auth)])
    def forecast_profile(body:ForecastBody):
        try:
            return forecast(store.parse_profile(body.yaml),body.options)
        except ForecastError as exc: planning_error(exc,'FORECAST')
        except Exception as exc: bad(exc)
    @app.get("/api/consultant/config",dependencies=[Depends(auth)])
    def consultant_config():
        return ai_configuration()
    @app.post("/api/calculator/experience",dependencies=[Depends(auth)])
    def experience_profile(body:ExperienceBody):
        try: return experience(store.parse_profile(body.yaml),body.options)
        except ExperienceError as exc: planning_error(exc,'EXPERIENCE')
        except Exception as exc: bad(exc)
    @app.post("/api/consultant/preview",dependencies=[Depends(auth)])
    def consultant_preview(body:ConsultantPreviewBody):
        try:
            item=job(body.job_id) if body.job_id else None
            p=store.parse_profile(item['profile'] if item else body.yaml)
            context={'source':'saved_run' if item else 'current_profile','profile':p.model_dump(mode='json')}
            if item:
                context['run']={k:item[k] for k in ('id','name','status','created','started','finished','summary','diagnostics') if k in item}
            if body.forecast_options is not None:
                prediction=forecast(p,body.forecast_options)
                prediction.pop('wall_s',None)
                context['approximate_forecast']=prediction
            return {'context':bounded_context(context),'connection':ai_configuration()}
        except (ConsultantError,ForecastError) as exc: planning_error(exc,'CONSULTANT')
        except HTTPException: raise
        except Exception as exc: bad(exc)
    @app.post("/api/consultant/ask",dependencies=[Depends(auth)])
    def consultant_ask(body:ConsultantAskBody):
        try: return consult(body.context,body.question)
        except ConsultantError as exc: planning_error(exc,'CONSULTANT')
        except Exception as exc: bad(exc)
    @app.post("/api/calculator",dependencies=[Depends(auth)])
    def calculator(body:CalculatorRequest):
        try:
            return calculate(body)
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
    mount_ui(app, Path(__file__).with_name("static"))
    return app
