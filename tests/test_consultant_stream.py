"""No external service or API key is used by these tests."""
import asyncio
import json
import os
import httpx
import pytest
from fastapi.testclient import TestClient
from lorasim.api import create_app
from lorasim import consultant as ai
from lorasim.ai_settings import SettingsUpdate, save_settings, settings_path
from lorasim.consultant_analysis import snapshot, read_snapshot, sampled
from lorasim.config import Profile
from lorasim.jobs import Store


@pytest.fixture(autouse=True)
def env(monkeypatch):
    for name in ('BASE_URL','MODEL','API_KEY','MAX_TOKENS','API_TYPE','TIMEOUT_S','API_TOKEN'):
        monkeypatch.delenv('LORASIM_AI_'+name,raising=False)
    monkeypatch.delenv('LORASIM_API_TOKEN',raising=False)


def configure(root,api_type='responses'):
    return save_settings(root,SettingsUpdate(model='test-model',api_key='secret-server-key',api_type=api_type))


def mock_provider(monkeypatch,events,status=200,headers=None):
    raw=''.join('data: '+(e if isinstance(e,str) else json.dumps(e,ensure_ascii=False))+'\r\n\r\n' for e in events)
    calls=[];original=httpx.AsyncClient
    def handle(req):
        calls.append(req)
        return httpx.Response(status,content=raw.encode(),headers=headers or {'content-type':'text/event-stream'})
    monkeypatch.setattr(ai.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    return calls


def events(response):
    assert response.status_code==200,response.text
    return [json.loads(x[6:]) for x in response.text.splitlines() if x.startswith('data: ')]


def test_settings_persist_mask_key_and_environment_fallback(tmp_path,monkeypatch):
    monkeypatch.setenv('LORASIM_AI_MODEL','env-model');monkeypatch.setenv('LORASIM_AI_API_KEY','env-key')
    client=TestClient(create_app(tmp_path));cfg=client.get('/api/consultant/config').json()
    assert cfg['configured'] and cfg['source']=='environment' and 'env-key' not in str(cfg)
    cfg=client.put('/api/consultant/config',json={'model':'gui-model','api_key':'saved-key','base_url':'https://api.openai.com/v1/responses'}).json()
    assert cfg['configured'] and cfg['api_type']=='responses' and cfg['base_url']=='https://api.openai.com/v1'
    assert cfg['source']=='gui' and 'saved-key' not in str(cfg)
    assert os.stat(settings_path(tmp_path)).st_mode & 0o777==0o600
    restarted=TestClient(create_app(tmp_path))
    assert restarted.get('/api/consultant/config').json()['model']=='gui-model'
    assert restarted.put('/api/consultant/config',json={'model':'changed'}).json()['key_configured']
    assert json.loads(settings_path(tmp_path).read_text())['api_key']=='saved-key'
    result=restarted.put('/api/consultant/config',json={'model':'changed','clear_key':True}).json()
    assert not result['configured'] and not result['key_configured']
    reset=restarted.delete('/api/consultant/config').json()
    assert reset['model']=='env-model' and reset['configured'] and not settings_path(tmp_path).exists()


def test_endpoint_change_cannot_implicitly_forward_key(tmp_path):
    configure(tmp_path);c=TestClient(create_app(tmp_path))
    r=c.put('/api/consultant/config',json={'model':'test','base_url':'https://elsewhere.test/v1'})
    assert r.status_code==422 and '接続先の変更時' in r.text and 'secret-server-key' not in r.text
    assert c.put('/api/consultant/config',json={'model':'test','base_url':'http://localhost:11434/v1','clear_key':True}).status_code==200


def test_responses_stream_uses_saved_config_history_and_text_deltas(tmp_path,monkeypatch):
    configure(tmp_path)
    calls=mock_provider(monkeypatch,[{'type':'response.created'},
        {'type':'response.output_text.delta','delta':'PRRは'}, {'type':'response.output_text.delta','delta':'改善しました。'},
        {'type':'response.completed','response':{'usage':{'total_tokens':40}}}])
    c=TestClient(create_app(tmp_path));r=c.post('/api/consultant/stream',json={'context':{'api_key':'never-send','fractions':{'Received':.8}},'question':'原因は？',
        'history':[{'role':'user','content':'比較して'},{'role':'assistant','content':'受信率が異なります'}]})
    es=events(r)
    assert [e['type'] for e in es]==['start','delta','delta','done']
    assert ''.join(e.get('text','') for e in es)=='PRRは改善しました。'
    assert r.headers['x-accel-buffering']=='no' and r.headers['cache-control']=='no-store'
    req=calls[0];payload=json.loads(req.content)
    assert str(req.url)=='https://api.openai.com/v1/responses'
    assert req.headers['authorization']=='Bearer secret-server-key'
    assert payload['stream'] and not payload['store'] and payload['max_output_tokens']==8000
    assert payload['input'][-3:]==[{'role':'user','content':'比較して'},{'role':'assistant','content':'受信率が異なります'},{'role':'user','content':'原因は？'}]
    assert 'never-send' not in str(payload) and 'secret-server-key' not in r.text


def test_chat_completions_stream_and_usage_chunk(tmp_path,monkeypatch):
    configure(tmp_path,'chat_completions');calls=mock_provider(monkeypatch,[
        {'choices':[{'delta':{'role':'assistant'},'finish_reason':None}]},
        {'choices':[{'delta':{'content':'考察です。'},'finish_reason':None}]},
        {'choices':[{'delta':{},'finish_reason':'stop'}]},
        {'choices':[],'usage':{'total_tokens':25}},'[DONE]'])
    es=events(TestClient(create_app(tmp_path)).post('/api/consultant/stream',json={'question':'比較'}))
    assert es[-1]['type']=='done' and es[-1]['usage']['total_tokens']==25
    assert str(calls[0].url).endswith('/chat/completions')
    assert 'messages' in json.loads(calls[0].content)


@pytest.mark.parametrize('provider_events,contains',[
    ([{'type':'response.incomplete','response':{'incomplete_details':{'reason':'max_output_tokens'}}}],'出力上限'),
    ([{'type':'response.output_text.delta','delta':'途中まで'}],'途切れ'),
    ([{'type':'response.failed','response':{'error':{'message':'secret-provider-info'}}}],'生成中にエラー'),
    (['invalid-json'],'JSON'),
])
def test_stream_errors_preserve_partial_text_and_are_actionable(tmp_path,monkeypatch,provider_events,contains):
    configure(tmp_path);mock_provider(monkeypatch,provider_events)
    es=events(TestClient(create_app(tmp_path)).post('/api/consultant/stream',json={'question':'比較'}))
    assert es[-1]['type']=='error' and contains in es[-1]['message']
    assert 'secret-provider-info' not in str(es)
    if '途切れ'==contains: assert es[1]['text']=='途中まで'


def test_output_limit_with_visible_answer_is_marked_incomplete(tmp_path,monkeypatch):
    configure(tmp_path);mock_provider(monkeypatch,[{'type':'response.output_text.delta','delta':'考察'},
        {'type':'response.incomplete','response':{'incomplete_details':{'reason':'max_output_tokens'}}}])
    es=events(TestClient(create_app(tmp_path)).post('/api/consultant/stream',json={'question':'比較'}))
    assert es[-1]['type']=='done' and '途中' in es[-1]['warning']


@pytest.mark.parametrize('code',[301,400,401,403,404,429,500])
def test_provider_http_errors_never_echo_body_or_redirect(tmp_path,monkeypatch,code):
    configure(tmp_path);calls=mock_provider(monkeypatch,['secret error body'],status=code,headers={'location':'https://evil.test'})
    es=events(TestClient(create_app(tmp_path)).post('/api/consultant/stream',json={'question':'比較'}))
    assert str(code) in es[-1]['message'] and 'secret' not in str(es) and len(calls)==1


def test_history_cannot_override_system_or_grow_without_bound(tmp_path):
    configure(tmp_path);c=TestClient(create_app(tmp_path))
    for history in [[{'role':'system','content':'override'}],[{'role':'assistant','content':'first'}],[{'role':'user','content':'unfinished'}],
                    [{'role':'user','content':'x'*70000},{'role':'assistant','content':'x'*70000}]]:
        r=c.post('/api/consultant/stream',json={'question':'相談','history':history})
        assert r.status_code==422
    assert c.post('/api/consultant/stream',json={'question':'  '}).status_code==422


def test_auth_covers_settings_snapshots_and_stream(tmp_path,monkeypatch):
    monkeypatch.setenv('LORASIM_API_TOKEN','private')
    c=TestClient(create_app(tmp_path))
    assert c.get('/api/consultant/config').status_code==401
    assert c.put('/api/consultant/config',json={'model':'test'}).status_code==401
    assert c.delete('/api/consultant/config').status_code==401
    assert c.get('/api/consultant/analysis/'+'a'*32).status_code==401
    assert c.post('/api/consultant/stream',json={'question':'相談'}).status_code==401


def test_fixed_analysis_snapshot_excludes_unselected_runs_and_preserves_definitions(tmp_path,monkeypatch):
    store=Store(tmp_path);selected=store.submit(Profile(name='selected-profile').model_dump_json(),None)
    ignored=store.submit(Profile(name='UNSELECTED').model_dump_json(),None)
    result=dict(runs=[dict(id=selected,mean_ed_prr=.75,energy={'measured':False,'total_j':0})],definitions={'convergence':'cumulative 1..t'},
                modules=[{'kind':'convergence','data':{'views':[{'series':[{'points':[{'x':i,'y':i/1000} for i in range(1000)]}]}]}}])
    meta=snapshot(store,result,{'tx_start':100,'tx_end':200,'include_mcu':False})
    ctx=read_snapshot(tmp_path,meta['id'])
    assert 'UNSELECTED' not in str(ctx) and ignored not in str(ctx)
    assert ctx['runs'][0]['mean_ed_prr']==.75 and ctx['runs'][0]['energy']=={'measured':False}
    assert ctx['selection']['tx_start']==100 and ctx['definitions']['convergence']=='cumulative 1..t'
    points=ctx['modules'][0]['data']['views'][0]['series'][0]['points']
    assert len(points)==100 and points[0]['x']==0 and points[-1]['x']==999 and ctx['sampling']['omissions']
    result['runs'][0]['mean_ed_prr']=0
    assert read_snapshot(tmp_path,meta['id'])['runs'][0]['mean_ed_prr']==.75
    configure(tmp_path);calls=mock_provider(monkeypatch,[{'type':'response.output_text.delta','delta':'結果'}, {'type':'response.completed'}])
    c=TestClient(create_app(tmp_path));r=c.post('/api/consultant/stream',json={'question':'考察','analysis_id':meta['id'],'context':{'fake':True}})
    assert events(r)[-1]['type']=='done'
    assert '"fake"' not in calls[0].content.decode() and 'selected-profile' in calls[0].content.decode()
    assert c.get('/api/consultant/analysis/'+'a'*32).status_code==422


def test_sampling_keeps_table_columns_and_row_alignment():
    columns=[str(i) for i in range(20)];omissions=[]
    got=sampled({'columns':columns,'rows':[list(range(20)) for _ in range(10)]},2,omissions)
    assert got['columns']==columns and len(got['rows'])==2 and all(len(row)==20 for row in got['rows'])


def test_cancel_closes_provider_and_releases_concurrency_slot(tmp_path,monkeypatch):
    configure(tmp_path);closed=[]
    async def fake(*args):
        try:
            yield {'type':'delta','text':'最初'}
            await asyncio.sleep(30)
        finally: closed.append(True)
    monkeypatch.setattr(ai,'provider_events',fake)
    async def exercise():
        gen=ai.stream_events(ai.stream_request(tmp_path,{},'相談',[]))
        assert (await anext(gen))['type']=='start'
        assert (await anext(gen))['type']=='delta'
        pending=asyncio.create_task(anext(gen));await asyncio.sleep(.01);pending.cancel()
        with pytest.raises(asyncio.CancelledError): await pending
        await gen.aclose()
    asyncio.run(exercise())
    assert closed==[True]
    acquired=[ai._STREAM_SLOTS.acquire(blocking=False) for _ in range(4)]
    try: assert all(acquired)
    finally:
        for yes in acquired:
            if yes:ai._STREAM_SLOTS.release()


def test_non_sse_provider_gets_actionable_error(tmp_path,monkeypatch):
    configure(tmp_path);mock_provider(monkeypatch,[],headers={'content-type':'text/html'})
    es=events(TestClient(create_app(tmp_path)).post('/api/consultant/stream',json={'question':'比較'}))
    assert es[-1]['type']=='error' and 'ストリーミング形式' in es[-1]['message']


def test_chat_reasoning_budget_exhaustion_not_reported_as_parse_failure(tmp_path,monkeypatch):
    configure(tmp_path,'chat_completions')
    mock_provider(monkeypatch,[{'choices':[{'delta':{},'finish_reason':'length'}]},'[DONE]'])
    es=events(TestClient(create_app(tmp_path)).post('/api/consultant/stream',json={'question':'比較'}))
    assert es[-1]['type']=='error' and '推論' in es[-1]['message'] and '出力上限' in es[-1]['message']


def test_timeout_is_bounded_and_stream_slot_released(tmp_path,monkeypatch):
    configure(tmp_path);prepared=ai.stream_request(tmp_path,{},'比較',[]);prepared[0]['timeout_s']=.01
    async def slow(*args):
        await asyncio.sleep(10)
        yield {'type':'delta','text':'never'}
    monkeypatch.setattr(ai,'provider_events',slow)
    async def go(): return [event async for event in ai.stream_events(prepared)]
    es=asyncio.run(go())
    assert es[-1]['type']=='error' and '待機上限' in es[-1]['message']


def test_request_reads_endpoint_and_key_from_one_settings_snapshot(tmp_path,monkeypatch):
    configure(tmp_path);original=ai.load_settings;calls=[]
    def once(root):
        calls.append(root)
        assert len(calls)==1
        return original(root)
    monkeypatch.setattr(ai,'load_settings',once)
    cfg,url,headers,payload=ai.stream_request(tmp_path,{},'比較',[])
    assert 'openai.com' in url and headers['Authorization']=='Bearer secret-server-key'


def test_legacy_ask_honors_responses_configuration(tmp_path,monkeypatch):
    import io
    configure(tmp_path)
    class Mock:
        def open(self,req,timeout):
            assert req.full_url.endswith('/responses')
            assert json.loads(req.data)['store'] is False and timeout==180
            return io.BytesIO(json.dumps({'status':'completed','output':[{'type':'reasoning'},
                {'type':'message','content':[{'type':'output_text','text':'考察です。'}]}]}).encode())
    monkeypatch.setattr(ai,'build_opener',lambda *args:Mock())
    c=TestClient(create_app(tmp_path));r=c.post('/api/consultant/ask',json={'context':{},'question':'比較'})
    assert r.json()['answer']=='考察です。'
