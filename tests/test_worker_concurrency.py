"""Real spawn processes with a deterministic barrier for live limit changes."""
import json
import multiprocessing as mp
import os
from pathlib import Path
import time
import pytest
from fastapi.testclient import TestClient
from lorasim.api import create_app
from lorasim.jobs import Store, worker

TEXT='duration_s: 5\ngroups: [{count: 1}]'


def barrier_job(root,jid):
    store=Store(root)
    (Path(root)/(jid+'.pid')).write_text(str(os.getpid()))
    deadline=time.monotonic()+30
    while not (Path(root)/(jid+'.release')).exists():
        if time.monotonic()>deadline:
            store.finish(jid,'failed',error='test barrier timed out'); return
        store.update_progress(jid,{'progress':.5,'sim_time_s':1})
        time.sleep(.05)
    store.finish(jid,'completed')


def barrier_worker(root):
    import lorasim.jobs as jobs
    jobs.execute_job=barrier_job
    jobs.worker(root,workers=1,once=True)


def until(predicate,timeout=20):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.05)
    pytest.fail('worker state did not reach expected condition')


def test_change_limits_while_two_distinct_processes_run(tmp_path):
    store=Store(tmp_path); ids=[store.submit(TEXT) for _ in range(3)]
    c=TestClient(create_app(tmp_path))
    proc=mp.get_context('spawn').Process(target=barrier_worker,args=(str(tmp_path),));proc.start()
    try:
        until(lambda:(tmp_path/(ids[0]+'.pid')).exists())
        assert store.get(ids[1])['status']=='queued'
        c.put('/api/worker/settings',json={'workers':2})
        until(lambda:(tmp_path/(ids[1]+'.pid')).exists())
        assert store.get(ids[2])['status']=='queued'
        assert (tmp_path/(ids[0]+'.pid')).read_text() != (tmp_path/(ids[1]+'.pid')).read_text()
        c.put('/api/worker/settings',json={'workers':1})
        until(lambda:store.worker_status().get('workers')==1)
        assert all(store.get(j)['status']=='running' for j in ids[:2])
        (tmp_path/(ids[0]+'.release')).touch()
        until(lambda:store.get(ids[0])['status']=='completed')
        time.sleep(.5)
        assert store.get(ids[2])['status']=='queued'  # one occupied slot, no forced termination
        (tmp_path/(ids[1]+'.release')).touch()
        until(lambda:(tmp_path/(ids[2]+'.pid')).exists())
        (tmp_path/(ids[2]+'.release')).touch()
        proc.join(15)
        assert not proc.is_alive() and proc.exitcode==0
        assert all(store.get(j)['status']=='completed' for j in ids)
        assert not store.worker_status()['online']
        assert Store(tmp_path).worker_limit(4)==1  # survives reconnect and restart
        c.delete('/api/worker/settings')
        assert Store(tmp_path).worker_limit(4)==4
    finally:
        for j in ids:(tmp_path/(j+'.release')).touch()
        proc.join(5)
        if proc.is_alive():proc.terminate();proc.join(5)


@pytest.mark.parametrize('workers',[0,257,-1,1.5,True,'2'])
def test_invalid_limits_are_rejected_without_changing_setting(tmp_path,workers):
    store=Store(tmp_path);store.set_worker_limit(3)
    r=TestClient(create_app(tmp_path)).put('/api/worker/settings',json={'workers':workers})
    assert r.status_code==422 and r.json()['diagnostics'][0]['hint']
    assert store.worker_limit(1)==3


def test_settings_are_authenticated(tmp_path,monkeypatch):
    monkeypatch.setenv('LORASIM_API_TOKEN','secret')
    c=TestClient(create_app(tmp_path))
    for method in [c.put,c.delete]:
        assert method('/api/worker/settings').status_code==401
    assert c.put('/api/worker/settings',json={'workers':2},headers={'Authorization':'Bearer secret'}).status_code==200


def test_claim_accounts_for_surviving_jobs(tmp_path):
    s=Store(tmp_path);first,second=[s.submit(TEXT) for _ in range(2)]
    assert s.claim(max_running=1)==first
    assert s.claim(max_running=1) is None
    assert s.claim(max_running=2)==second
