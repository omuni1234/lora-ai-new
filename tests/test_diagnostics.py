"""Boundary diagnostics must preserve constraints and unknown failure evidence."""
import errno
import signal
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from lorasim.api import create_app
from lorasim.config import Profile
from lorasim.diagnostics import configuration_warnings, exception_diagnostics
from lorasim.jobs import Store, execute_job


def assert_japanese(d):
    assert d['severity'] in {'error','warning'}
    assert d['code'] and d['hint']
    assert any('\u3040' <= ch <= '\u9fff' for ch in d['message'])
    assert any('\u3040' <= ch <= '\u9fff' for ch in d['hint'])


@pytest.mark.parametrize('text,code,path',[
    ('duration_s: -1','VALIDATION_GREATER_THAN','duration_s'),
    ('groups: [{count: 0}]','VALIDATION_GREATER_THAN_EQUAL','groups.0.count'),
    ('energy: {radio: {voltage_v: .nan}}','VALIDATION_FINITE_NUMBER','energy.radio.voltage_v'),
    ('unexpected: 1','VALIDATION_EXTRA_FORBIDDEN','unexpected'),
    ('groups: [{timing: {kind: periodic, options: {interval_s: -1}}}]','VALIDATION_GREATER_THAN','groups.0.timing.options.interval_s'),
    ('groups: [{timing: {kind: periodic, options: {start: {min_s: 20, max_s: 10}}}}]','START_RANGE','groups.0.timing.options.start'),
    ('groups: [{policy: {kind: dqn, options: {batch_size: 40, replay_capacity: 10}}}]','REPLAY_BATCH','groups.0.policy.options'),
    ('mac: {rx2_delay_s: 1}','RX_WINDOWS','mac'),
    ('groups: [{policy: {kind: no_such_policy}}]','UNKNOWN_MODEL',''),
    ('groups: [{mobility: {kind: missing_mobility}}]','UNKNOWN_MODEL','groups.0.mobility.kind'),
    ('groups: [','YAML_SYNTAX',''),
    ('- 1\n- 2','PROFILE_MAPPING',''),
])
def test_validation_error_paths_and_hints(tmp_path,text,code,path):
    c=TestClient(create_app(tmp_path))
    response=c.post('/api/validate?report=true',json={'yaml':text})
    assert response.status_code==422,response.text
    d=response.json()['diagnostics'][0]
    assert_japanese(d)
    assert d['code']==code and d['path']==path
    assert d['detail']


def test_default_has_no_warnings_and_legacy_api_shape(tmp_path):
    c=TestClient(create_app(tmp_path))
    r=c.post('/api/validate',json={'yaml':'name: demo'})
    assert r.json()['name']=='demo'
    r=c.post('/api/validate?report=true',json={'yaml':'name: demo'})
    assert r.json()['profile']['name']=='demo' and r.json()['diagnostics']==[]
    assert configuration_warnings(Profile())==[]


def test_quick_check_does_not_construct_or_run_simulation(tmp_path,monkeypatch):
    monkeypatch.setattr('lorasim.jobs.Simulation',lambda *a,**kw:pytest.fail('must not allocate models'))
    c=TestClient(create_app(tmp_path))
    response=c.post('/api/diagnostics',json={'yaml':'groups: [{count: 1000000}]'})
    assert response.status_code==200 and response.json()['scope']=='configuration'
    assert not list((tmp_path/'runs').iterdir())


def test_warnings_nonblocking_and_preserved_on_submission(tmp_path):
    text='''duration_s: 10
mac: {duty_cycle: false}
channel: {speed_m_s: 300, exponent: 8}
energy: {mcu: {enabled: true, mode: measured}}
groups:
 - timing: {kind: periodic, options: {start: {mode: fixed, at_s: 20}}}
   policy: {kind: dqn, options: {learning_rate: 1, epsilon_start: 0, epsilon_end: 0, failure_reward: 2}}
'''
    c=TestClient(create_app(tmp_path))
    result=c.post('/api/validate?report=true',json={'yaml':text})
    assert result.status_code==200,result.text
    ds=result.json()['diagnostics']
    codes={d['code'] for d in ds}
    assert {'DUTY_DISABLED','PROPAGATION_SPEED','PATH_LOSS_EXPONENT','MCU_MEASURED','START_AFTER_END','LARGE_LEARNING_RATE','NO_EXPLORATION','REWARD_ORDER'}<=codes
    for d in ds:assert_japanese(d);assert d['severity']=='warning'
    job=c.post('/api/jobs',json={'yaml':text}).json()
    assert job['diagnostics']==ds
    assert c.get('/api/jobs/'+job['id']).json()['diagnostics']==ds


def test_model_reference_override_and_assignment_warning(tmp_path):
    c=TestClient(create_app(tmp_path))
    text='''models:
  policy:
    learner: {kind: dqn, options: {learning_rate: 0.001}}
groups: [{policy: {ref: learner, options: {learning_rate: 0.5}}}]
assignments: [{timing: {kind: periodic, options: {first_s: 900}}}]
'''
    ds=c.post('/api/diagnostics',json={'yaml':text}).json()['diagnostics']
    assert any(d['path']=='groups.0.policy.options.learning_rate' for d in ds)
    assert any(d['path']=='assignments.0.timing.options' for d in ds)


def test_radio_warning_is_review_not_error(tmp_path):
    c=TestClient(create_app(tmp_path))
    text='groups: [{policy: {kind: fixed, options: {cr: 4, frequency_hz: 869525000}}}]'
    r=c.post('/api/validate?report=true',json={'yaml':text})
    assert r.status_code==200
    assert {d['code'] for d in r.json()['diagnostics']}=={'NONDEFAULT_RADIO','UNLISTENED_CHANNEL'}
    assert r.json()['profile']['groups'][0]['policy']['options']['cr']==4


@pytest.mark.parametrize('method,url,body,status',[
    ('post','/api/analysis',{},422),('post','/api/preview',{'yaml':'{}','limit':0},422),
    ('get','/api/jobs/missing',None,404),('get','/api/validate',None,405),
])
def test_http_and_request_validation_japanese(tmp_path,method,url,body,status):
    c=TestClient(create_app(tmp_path))
    r=c.request(method,url,**({'json':body} if body is not None else {}))
    assert r.status_code==status
    for d in r.json()['diagnostics']:assert_japanese(d)


def test_auth_hint_and_unhandled_server_error(tmp_path,monkeypatch):
    monkeypatch.setenv('LORASIM_API_TOKEN','token')
    c=TestClient(create_app(tmp_path),raise_server_exceptions=False)
    assert c.get('/api/jobs').json()['diagnostics'][0]['code']=='HTTP_401'
    def fail(*a,**kw):raise OSError(errno.ENOSPC,'No space left on device')
    monkeypatch.setattr(Store,'list',fail)
    r=c.get('/api/jobs',headers={'Authorization':'Bearer token'})
    assert r.status_code==500 and r.json()['diagnostics'][0]['code']=='DISK_FULL'


def test_runtime_failure_is_persisted_and_survives_reconnect(tmp_path,monkeypatch):
    store=Store(tmp_path)
    jid=store.submit('duration_s: 3\ngroups: [{policy: {kind: fixed, options: {sf: 12}}, payload_bytes: 100}]')
    assert store.claim()==jid
    # execute_job is normally a child process. Avoid changing pytest's SIGTERM.
    monkeypatch.setattr(signal,'signal',lambda *a:None)
    execute_job(tmp_path,jid)
    r=TestClient(create_app(tmp_path)).get('/api/jobs/'+jid).json()
    assert r['status']=='failed' and 'EU DR limit' in r['error']
    assert r['diagnostics'][0]['code']=='PAYLOAD_LIMIT'
    assert 'ED 0' in r['diagnostics'][0]['detail']
    assert (tmp_path/'runs'/jid/'error.log').is_file()


def test_failure_when_output_cannot_be_written_preserves_cause(tmp_path,monkeypatch):
    store=Store(tmp_path);jid=store.submit('duration_s: 1');store.claim()
    monkeypatch.setattr(signal,'signal',lambda *a:None)
    def fail(*a,**kw):raise PermissionError(errno.EACCES,'denied')
    monkeypatch.setattr('lorasim.jobs.Simulation',fail)
    monkeypatch.setattr(Path,'write_text',fail)
    execute_job(tmp_path,jid)
    assert store.get(jid)['diagnostics'][0]['code']=='PERMISSION'


def test_unknown_plugin_error_does_not_invent_cause():
    e=RuntimeError('custom model <script>sample()</script> failure')
    d=exception_diagnostics(e)[0]
    assert d['code']=='EXCEPTION' and str(e) in d['detail']
    assert_japanese(d)


def test_existing_database_and_interrupted_run(tmp_path):
    store=Store(tmp_path);jid=store.submit('name: old')
    with store.connect() as db:
        db.execute('UPDATE jobs SET diagnostics=NULL,status=?,error=? WHERE id=?',('interrupted','Worker heartbeat expired; resubmit to restart',jid))
    with store.connect() as db:
        db.execute('ALTER TABLE jobs DROP COLUMN diagnostics')
    again=Store(tmp_path)
    assert again.get(jid)['diagnostics'][0]['code']=='WORKER_HEARTBEAT'


def test_notification_failure_is_nonfatal_visible_and_coalesced(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from lorasim.config import Notification
    from lorasim.jobs import Notifier
    store=Store(tmp_path);jid=store.submit('name: notification-test')
    monkeypatch.setattr('lorasim.jobs.subprocess.run',lambda *a,**kw:SimpleNamespace(returncode=7,stderr='connection refused'))
    n=Notifier([Notification(url='http://127.0.0.1:1',every_percent=1)],tmp_path,jid,lambda d:store.record_diagnostic(jid,d))
    n.push({'status':'running','progress':.1});n.push({'status':'completed','progress':1.});n.close()
    ds=store.get(jid)['diagnostics']
    assert len(ds)==1 and ds[0]['code']=='NOTIFICATION_FAILED'
    assert ds[0]['severity']=='warning' and 'connection refused' in ds[0]['detail']
    assert store.get(jid)['status']=='queued'  # notifier never changes the run status
    store.finish(jid,'failed',error='worker process exited with code -9')
    assert {d['code'] for d in store.get(jid)['diagnostics']}=={'NOTIFICATION_FAILED','WORKER_EXIT'}


def test_hopping_frequency_warning_uses_actual_candidates():
    p=Profile.model_validate({'gateways':[{'position':[0,0,15],'frequencies_hz':[868300000]}],
       'groups':[{'observe_position':True,'policy':{'kind':'linucb_sf','options':{'frequencies_hz':[868300000]}}}]})
    assert not any(d['code']=='UNLISTENED_CHANNEL' for d in configuration_warnings(p))


def test_dqn_divergence_is_actionable():
    d=exception_diagnostics(FloatingPointError('DQN loss is non-finite; review learning rate and RSSI normalization'))[0]
    assert d['code']=='DQN_DIVERGED' and 'RSSI' in d['hint']


def test_concurrent_api_worker_database_migration(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    store=Store(tmp_path)
    jid=store.submit('name: migration')
    with store.connect() as db:
        db.execute('ALTER TABLE jobs DROP COLUMN diagnostics')
    with ThreadPoolExecutor(max_workers=4) as pool:
        stores=list(pool.map(lambda _:Store(tmp_path),range(8)))
    assert all(s.get(jid)['diagnostics']==[] for s in stores)
