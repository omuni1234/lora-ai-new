import json
import multiprocessing as mp
import time
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from lorasim.api import create_app
from lorasim.jobs import Store,worker

TEXT='name: api-test\nduration_s: 5\ngroups:\n - name: ed\n   count: 2\n'

def test_api_save_submit_poll_cancel_and_path(tmp_path):
    c=TestClient(create_app(tmp_path))
    assert c.get('/').status_code==200
    assert c.post('/api/validate',json={'yaml':TEXT}).status_code==200
    assert c.put('/api/profiles/demo',json={'yaml':TEXT}).status_code==200
    assert c.get('/api/profiles').json()[0]['name']=='demo'
    j=c.post('/api/jobs',json={'yaml':TEXT}).json()['id']
    assert c.get('/api/jobs/'+j).json()['status']=='queued'
    assert c.get('/api/jobs/'+j+'/files/..%2F..%2Fjobs.sqlite').status_code==404
    c.post('/api/jobs/'+j+'/cancel')
    assert c.get('/api/jobs/'+j).json()['status']=='cancelled'
    assert c.post('/api/jobs',json={'yaml':'duration_s: -1'}).status_code==422

def test_auth(tmp_path,monkeypatch):
    monkeypatch.setenv('LORASIM_API_TOKEN','test-token')
    c=TestClient(create_app(tmp_path))
    assert c.get('/api/jobs').status_code==401
    assert c.get('/api/jobs',headers={'Authorization':'Bearer test-token'}).status_code==200

def test_worker_runs_after_clients_disconnect_and_parallel_jobs(tmp_path):
    store=Store(tmp_path)
    ids=[store.submit(TEXT) for _ in range(3)]
    proc=mp.get_context('spawn').Process(target=worker,args=(str(tmp_path),2,True))
    proc.start();proc.join(30)
    if proc.is_alive():proc.terminate();proc.join();pytest.fail('worker timed out')
    assert proc.exitcode==0
    assert all(store.get(j)['status']=='completed' for j in ids)
    assert all((tmp_path/'runs'/j/'summary.json').exists() for j in ids)

def test_plugin_disabled(tmp_path,monkeypatch):
    monkeypatch.delenv('LORASIM_ALLOW_PLUGINS',raising=False)
    with pytest.raises(ValueError,match='ALLOW_PLUGINS'):
        Store(tmp_path).submit(TEXT+'   policy: {kind: os:system}\n')


def test_curl_notification_local_receiver(tmp_path,monkeypatch):
    import threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from lorasim.config import Notification
    from lorasim.jobs import Notifier
    # HTTPServer otherwise performs an unrelated reverse-DNS lookup on bind.
    monkeypatch.setattr('socket.getfqdn',lambda name='': 'localhost')
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(204);self.end_headers()
        def log_message(self,*args):pass
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:
        n=Notifier([Notification(url=f'http://127.0.0.1:{http.server_port}')],tmp_path,'test-job')
        n.push({'status':'completed','progress':1.0});n.close()
        assert received==[{'job_id':'test-job','status':'completed','progress':1.0}]
        assert json.loads((tmp_path/'notifications.jsonl').read_text())['exit_code']==0
    finally:
        http.shutdown();http.server_close();thread.join()
