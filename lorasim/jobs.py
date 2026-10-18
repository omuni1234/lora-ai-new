from __future__ import annotations
import contextlib
import fcntl
import json
import multiprocessing as mp
import os
from pathlib import Path
import queue
import signal
import sqlite3
import subprocess
import threading
import time
import traceback
import uuid
from .config import load_profile, dump_profile, Component
from .engine import Simulation
from .tracking import atomic_json
from .diagnostics import validate_options, configuration_warnings, exception_diagnostics, issue

TERMINAL = {"completed","failed","cancelled","interrupted"}


class Store:
    def __init__(self,root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True,exist_ok=True)
        (self.root/"runs").mkdir(exist_ok=True)
        with self.connect() as db:
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, profile TEXT NOT NULL,
              status TEXT NOT NULL, created REAL NOT NULL, started REAL, finished REAL,
              progress REAL NOT NULL DEFAULT 0, sim_time REAL NOT NULL DEFAULT 0,
              heartbeat REAL, cancel INTEGER NOT NULL DEFAULT 0, error TEXT, summary TEXT
            );
            CREATE TABLE IF NOT EXISTS experiments (id TEXT PRIMARY KEY, name TEXT NOT NULL,
              description TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS models (category TEXT NOT NULL, name TEXT NOT NULL,
              definition TEXT NOT NULL, updated REAL NOT NULL, PRIMARY KEY(category,name));
            CREATE TABLE IF NOT EXISTS profiles (name TEXT PRIMARY KEY, yaml TEXT NOT NULL, updated REAL NOT NULL);
            """)
            # API and worker may start together against the same old database.
            db.execute("BEGIN IMMEDIATE")
            columns = {x[1] for x in db.execute("PRAGMA table_info(jobs)")}
            if "diagnostics" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN diagnostics TEXT")
            if "experiment_id" not in columns:
                db.execute("ALTER TABLE jobs ADD COLUMN experiment_id TEXT")
            for row in db.execute("SELECT id,name,created FROM jobs WHERE experiment_id IS NULL").fetchall():
                db.execute("INSERT OR IGNORE INTO experiments(id,name,created) VALUES(?,?,?)",tuple(row))
                db.execute("UPDATE jobs SET experiment_id=id WHERE id=?",(row['id'],))
    def connect(self):
        db = sqlite3.connect(self.root/"jobs.sqlite",timeout=30)
        db.row_factory = sqlite3.Row
        return db
    def parse_profile(self,text):
        return load_profile(text,text=True,catalog=self.catalog())
    def check_profile(self, text, *, construct=True):
        p = self.parse_profile(text)
        validate_options(p)
        if construct:
            Simulation(p, self.root/"_validation")
        return p, configuration_warnings(p)

    def create_experiment(self,name,description=""):
        if not name.strip() or len(name)>160 or len(description)>10000:
            raise ValueError("invalid experiment name or description")
        eid = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("INSERT INTO experiments VALUES(?,?,?,?)",(eid,name,description,time.time()))
        return eid
    def experiments(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute("""SELECT e.*,count(j.id) AS runs,
                sum(CASE WHEN j.status='completed' THEN 1 ELSE 0 END) AS completed
                FROM experiments e LEFT JOIN jobs j ON j.experiment_id=e.id
                GROUP BY e.id ORDER BY e.created DESC LIMIT 1000""")]
    def catalog(self):
        result = {}
        with self.connect() as db:
            for row in db.execute("SELECT * FROM models ORDER BY category,name"):
                result.setdefault(row['category'],{})[row['name']] = json.loads(row['definition'])
        return result
    def experiment_info(self,eid):
        """Small hover abstract; no Parquet scan or simulation construction."""
        with self.connect() as db:
            row = db.execute('SELECT * FROM experiments WHERE id=?',(eid,)).fetchone()
            if row is None:
                raise KeyError(eid)
            result = dict(row)
            result['statuses'] = {x['status']:x['n'] for x in db.execute('SELECT status,count(*) n FROM jobs WHERE experiment_id=? GROUP BY status',(eid,))}
            result.update(dict(db.execute('SELECT min(started) first_started,max(finished) last_finished,sum(CASE WHEN started IS NOT NULL AND finished IS NOT NULL THEN finished-started END) wall_total_s FROM jobs WHERE experiment_id=?',(eid,)).fetchone()))
            latest = db.execute('SELECT id,profile,summary,started,finished FROM jobs WHERE experiment_id=? ORDER BY COALESCE(started,created) DESC LIMIT 1',(eid,)).fetchone()
            completed = db.execute("SELECT id FROM jobs WHERE experiment_id=? AND status IN ('completed','cancelled') AND started IS NOT NULL ORDER BY finished DESC LIMIT 1",(eid,)).fetchone()
        if latest:
            p = load_profile(latest['profile'],text=True)
            result['latest_run'] = {'id':latest['id'],'ed_count':sum(g.count for g in p.groups),'gw_count':len(p.gateways),
                'duration_s':p.duration_s,'seed':p.seed,'started':latest['started'],'finished':latest['finished'],
                'models':{g.name:{k:(getattr(g,k).ref or getattr(g,k).kind) for k in ('policy','timing','mobility')} for g in p.groups[:20]}}
        result['environment'] = None
        if completed:
            path = self.root/'runs'/completed['id']/'manifest.json'
            if path.exists():
                manifest = json.loads(path.read_text())
                result['environment'] = {k:manifest.get(k) for k in ('platform','python','numpy','dependencies','simulator_version')}
                result['environment_run_id'] = completed['id']
        return result
    def save_model(self,category,name,definition):
        if category not in {'policy','mobility','timing'} or not name.strip() or len(name)>120:
            raise ValueError("invalid category or model name")
        component = Component.model_validate(definition)
        if component.ref:
            raise ValueError("catalog entries must specify an implementation, not a reference")
        from .components import make
        import numpy as np
        context = {'rng':np.random.default_rng(0)}
        if category=='policy' and component.action_space is not None:
            # Library definitions have no experiment-wide bounds yet. Validate
            # their own candidates now; the Profile enforces subset membership.
            from .config import ActionSpace
            context['action_space']=ActionSpace(**component.action_space.model_dump(exclude_none=True))
        if category=='mobility':
            context.update(initial=(0.,0.,1.5),area=(2000.,2000.))
        make(component,category,**context)
        with self.connect() as db:
            db.execute("INSERT INTO models VALUES(?,?,?,?) ON CONFLICT(category,name) DO UPDATE SET definition=excluded.definition,updated=excluded.updated",
                       (category,name,component.model_dump_json(),time.time()))
    def submit(self,text,experiment_id=None):
        p, diagnostics = self.check_profile(text)
        jid = uuid.uuid4().hex
        with self.connect() as db:
            if experiment_id is None:
                experiment_id = uuid.uuid4().hex
                db.execute("INSERT INTO experiments(id,name,created) VALUES(?,?,?)",(experiment_id,p.name,time.time()))
            elif not db.execute("SELECT 1 FROM experiments WHERE id=?",(experiment_id,)).fetchone():
                raise ValueError("unknown Experiment")
            db.execute("INSERT INTO jobs(id,name,profile,status,created,experiment_id,diagnostics) VALUES(?,?,?,'queued',?,?,?)",(jid,p.name,dump_profile(p),time.time(),experiment_id,json.dumps(diagnostics,ensure_ascii=False)))
        return jid
    def get(self,jid):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?",(jid,)).fetchone()
        if row is None:
            raise KeyError(jid)
        result = dict(row)
        if result["summary"]:
            result["summary"] = json.loads(result["summary"])
        result["diagnostics"] = json.loads(result["diagnostics"]) if result.get("diagnostics") else []
        if result.get("error") and not any(d["severity"] == "error" for d in result["diagnostics"]):
            result["diagnostics"].extend(exception_diagnostics(result["error"]))
        return result
    def list(self,limit=100,experiment_id=None):
        with self.connect() as db:
            rows = db.execute("SELECT id,name,status,created,started,finished,progress,sim_time,error,experiment_id FROM jobs WHERE (? IS NULL OR experiment_id=?) ORDER BY created DESC LIMIT ?",(experiment_id,experiment_id,limit)).fetchall()
        return [dict(x) for x in rows]
    def claim(self):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT id FROM jobs WHERE status='queued' AND cancel=0 ORDER BY created LIMIT 1").fetchone()
            if not row:
                return None
            jid = row["id"]
            db.execute("UPDATE jobs SET status='running',started=?,heartbeat=? WHERE id=?",(time.time(),time.time(),jid))
            return jid
    def update_progress(self,jid,value):
        with self.connect() as db:
            db.execute("UPDATE jobs SET progress=?,sim_time=?,heartbeat=? WHERE id=?",(value["progress"],value["sim_time_s"],time.time(),jid))
    def finish(self,jid,status,summary=None,error=None,diagnostics=None):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if error:
                row = db.execute("SELECT diagnostics FROM jobs WHERE id=?", (jid,)).fetchone()
                existing = json.loads(row[0]) if row and row[0] else []
                value = [d for d in existing if d["severity"] != "error"] + (diagnostics or exception_diagnostics(error))
                db.execute("UPDATE jobs SET diagnostics=? WHERE id=?", (json.dumps(value,ensure_ascii=False),jid))
            db.execute("UPDATE jobs SET status=?,finished=?,summary=?,error=? WHERE id=?",(status,time.time(),json.dumps(summary) if summary else None,error,jid))
    def record_diagnostic(self,jid,message):
        # Coalesce repeated failures by code/path; do not grow with notification count.
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT diagnostics FROM jobs WHERE id=?", (jid,)).fetchone()
            values = json.loads(row[0]) if row and row[0] else []
            values = [d for d in values if (d['code'],d['path']) != (message['code'],message['path'])]
            values.append(message)
            db.execute("UPDATE jobs SET diagnostics=? WHERE id=?", (json.dumps(values,ensure_ascii=False),jid))

    def cancel(self,jid):
        item = self.get(jid)
        with self.connect() as db:
            db.execute("UPDATE jobs SET cancel=1 WHERE id=?",(jid,))
            db.execute("UPDATE jobs SET status='cancelled',finished=? WHERE id=? AND status='queued'",(time.time(),jid))
        return item
    def is_cancelled(self,jid):
        # Progress checks do not deserialize stored warning/error payloads.
        with self.connect() as db:
            row = db.execute("SELECT cancel FROM jobs WHERE id=?", (jid,)).fetchone()
        if row is None:
            raise KeyError(jid)
        return bool(row[0])
    def save_profile(self,name,text):
        if not name.strip() or len(name)>120:
            raise ValueError("profile name must have 1..120 characters")
        p, diagnostics = self.check_profile(text)
        with self.connect() as db:
            db.execute("INSERT INTO profiles VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET yaml=excluded.yaml,updated=excluded.updated",(name,text,time.time()))
    def profiles(self):
        with self.connect() as db:
            return [dict(x) for x in db.execute("SELECT * FROM profiles ORDER BY name")]


class Notifier:
    """curl runs off the event loop, without a shell; bounded/coalesced progress queue."""
    def __init__(self,configs,root,jid,diagnostic_sink=None):
        self.configs,self.root,self.jid = configs,Path(root),jid
        self.diagnostic_sink = diagnostic_sink or (lambda message: None)
        self.pending = queue.Queue(maxsize=1)
        self.last = [-1.]*len(configs)
        self.thread = threading.Thread(target=self._run,daemon=True)
        self.thread.start()
    def push(self,value):
        try:
            self.pending.put_nowait(value.copy())
        except queue.Full:
            with contextlib.suppress(queue.Empty):
                self.pending.get_nowait()
            with contextlib.suppress(queue.Full):
                self.pending.put_nowait(value.copy())
    def close(self):
        self.pending.put(None)
        self.thread.join(timeout=sum(c.timeout_s+2 for c in self.configs)+5)
    def _run(self):
        while True:
            value = self.pending.get()
            if value is None:
                break
            percent = 100*value["progress"]
            for i,c in enumerate(self.configs):
                if self.last[i]>=0 and percent-self.last[i]<c.every_percent and value["status"] not in TERMINAL:
                    continue
                self.last[i] = percent
                payload = json.dumps({"job_id":self.jid,**value})
                try:
                    result = subprocess.run(["curl","--silent","--show-error","--fail","--max-time",str(c.timeout_s),
                                             "--request","POST","--header","Content-Type: application/json",
                                             "--data-binary","@-","--url",c.url],input=payload,text=True,
                                             stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=c.timeout_s+1)
                    code,error = result.returncode,result.stderr[-1000:]
                except Exception as exc:
                    code,error = -1,str(exc)
                if code:
                    self.diagnostic_sink(issue('NOTIFICATION_FAILED', '進捗通知のHTTP送信に失敗しました。シミュレーション本体は継続します。',
                        '通知先URL、受信サービス、ネットワーク、curlの有無とタイムアウトを確認してください。技術詳細は最後の失敗です。',
                        severity='warning',path=f'notifications.{i}.url',detail=f'curl exit code: {code}; {error}'))
                try:
                    with (self.root/"notifications.jsonl").open("a") as log:
                        log.write(json.dumps({"job_id":self.jid,"time":time.time(),"status":value["status"],"percent":percent,"exit_code":code,"error":error})+"\n")
                except OSError as exc:
                    self.diagnostic_sink(issue('NOTIFICATION_LOG', '進捗通知のログを保存できませんでした。',
                        '出力ディレクトリの空き容量と書き込み権限を確認してください。シミュレーション結果の保存状態も確認してください。',
                        severity='warning',path='notifications',detail=f'{type(exc).__name__}: {exc}'))


def execute_job(root,jid):
    store = Store(root)
    item = store.get(jid)
    output = store.root/"runs"/jid
    notifier = None
    stop = threading.Event()
    def heartbeat():
        while not stop.wait(5):
            with store.connect() as db:
                db.execute("UPDATE jobs SET heartbeat=? WHERE id=?",(time.time(),jid))
    thread = threading.Thread(target=heartbeat,daemon=True)
    thread.start()
    # SIGTERM asks the event loop to finish and flush already generated records.
    requested = threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:requested.set())
    try:
        p = load_profile(item["profile"],text=True)
        # No file is written until the first progress callback, after run setup.
        notifier = Notifier(p.notifications,output,jid,lambda message:store.record_diagnostic(jid,message)) if p.notifications else None
        def progress(value):
            store.update_progress(jid,value)
            if notifier:
                notifier.push(value)
        sim = Simulation(p,output,progress=progress,cancelled=lambda:requested.is_set() or store.is_cancelled(jid))
        result = sim.run()
        store.finish(jid,result["status"],result)
    except BaseException as exc:
        # Persist the cause before writing a log: disk/permission errors must not
        # mask the original failure when the output directory is unavailable.
        store.finish(jid,"failed",error=f"{type(exc).__name__}: {exc}",diagnostics=exception_diagnostics(exc))
        with contextlib.suppress(OSError):
            output.mkdir(exist_ok=True)
            (output/"error.log").write_text(traceback.format_exc())
        if notifier:
            row = store.get(jid)
            notifier.push({"status":"failed","progress":row["progress"],"error":str(exc)})
    finally:
        stop.set()
        thread.join(timeout=1)
        if notifier:
            notifier.close()


def worker(root,workers=1,once=False):
    if workers<1:
        raise ValueError("workers must be >=1")
    store = Store(root)
    # One supervisor per storage directory, potentially many independent processes.
    lock = (store.root/"worker.lock").open("w")
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        raise RuntimeError("a worker supervisor already owns this data directory")
    lock.write(str(os.getpid()))
    lock.flush()
    with store.connect() as db:
        # A dead worker may leave a live child. A recent heartbeat must not be stolen.
        stale = time.time()-30
        db.execute("UPDATE jobs SET status='interrupted',finished=?,error='Worker stopped; resubmit profile to restart' WHERE status='running' AND heartbeat<?",(time.time(),stale))
    ctx = mp.get_context("spawn")
    children = {}
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    oldterm,oldint = signal.signal(signal.SIGTERM,stop),signal.signal(signal.SIGINT,stop)
    try:
        while True:
            for jid,process in tuple(children.items()):
                if not process.is_alive():
                    process.join()
                    if store.get(jid)["status"]=="running":
                        store.finish(jid,"failed",error=f"worker process exited with code {process.exitcode}")
                    del children[jid]
            with store.connect() as db:
                db.execute("UPDATE jobs SET status='interrupted',finished=?,error='Worker heartbeat expired; resubmit to restart' WHERE status='running' AND heartbeat<?",(time.time(),time.time()-30))
            if stopping:
                for process in children.values():
                    process.terminate()
                for process in children.values():
                    process.join(timeout=15)
                    if process.is_alive():
                        process.kill()
                        process.join()
                break
            while len(children)<workers:
                jid = store.claim()
                if jid is None:
                    break
                process = ctx.Process(target=execute_job,args=(str(store.root),jid))
                process.start()
                children[jid] = process
            atomic_json(store.root/"worker.json",{"pid":os.getpid(),"heartbeat":time.time(),"workers":workers,"active":list(children)})
            if once and not children:
                break
            time.sleep(.2)
    finally:
        signal.signal(signal.SIGTERM,oldterm)
        signal.signal(signal.SIGINT,oldint)
        lock.close()
