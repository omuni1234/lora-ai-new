import io
import json
from urllib.error import HTTPError, URLError
import pytest
from fastapi.testclient import TestClient
from lorasim.api import create_app
from lorasim.config import Profile
from lorasim import consultant as ai


@pytest.fixture(autouse=True)
def isolated_ai_environment(monkeypatch):
    for name in ('BASE_URL','MODEL','API_KEY','MAX_TOKENS'):
        monkeypatch.delenv('LORASIM_AI_'+name,raising=False)


def test_preview_removes_notifications_secrets_and_no_jobs(tmp_path,monkeypatch):
    monkeypatch.delenv('LORASIM_AI_BASE_URL',raising=False)
    c=TestClient(create_app(tmp_path));p=Profile().model_dump(mode='json')
    p['notifications']=[{'url':'https://example.test/secret-webhook'}]
    r=c.post('/api/consultant/preview',json={'yaml':json.dumps(p)})
    assert r.status_code==200
    assert 'notifications' not in r.json()['context']['profile']
    assert not r.json()['connection']['configured']
    assert c.get('/api/jobs').json()==[]
    assert ai.scrub({'options':{'api_key':'private','threshold':3}})=={'options':{'threshold':3}}
    r=c.post('/api/consultant/ask',json={'context':r.json()['context'],'question':'相談'})
    assert r.status_code==422 and '未設定' in r.text


def test_provider_payload_and_plain_text_response(monkeypatch):
    monkeypatch.setenv('LORASIM_AI_BASE_URL','https://provider.example/v1')
    monkeypatch.setenv('LORASIM_AI_MODEL','test-model')
    monkeypatch.setenv('LORASIM_AI_API_KEY','secret-provider-key')
    captured={}
    class Mock:
        def open(self,request,timeout):
            captured['payload']=json.loads(request.data);captured['headers']=dict(request.header_items())
            captured['url']=request.full_url
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'設定変更案です。<script>test</script>'},'finish_reason':'stop'}]}).encode())
    monkeypatch.setattr(ai,'build_opener',lambda *a:Mock())
    r=ai.consult({'profile':{'token':'never-send','name':'test'}},'何を変えるべきか')
    assert r['answer'].startswith('設定変更案')
    assert captured['url']=='https://provider.example/v1/chat/completions'
    assert captured['headers']['Authorization']=='Bearer secret-provider-key'
    assert 'never-send' not in json.dumps(captured['payload'])
    assert captured['payload']['model']=='test-model'
    assert 'secret-provider-key' not in json.dumps(ai.configuration())


@pytest.mark.parametrize('failure',[
    *(HTTPError('x',code,'sensitive provider response',{},None) for code in (400,401,403,404,429,500)),
    URLError('sensitive endpoint'),
])
def test_provider_errors_are_japanese_and_do_not_echo_secrets(monkeypatch,failure):
    monkeypatch.setenv('LORASIM_AI_BASE_URL','http://localhost:11434/v1');monkeypatch.setenv('LORASIM_AI_MODEL','test')
    class Mock:
        def open(self,*a,**kw):raise failure
    monkeypatch.setattr(ai,'build_opener',lambda *a:Mock())
    with pytest.raises(ai.ConsultantError) as e:ai.consult({},'test')
    assert 'sensitive' not in str(e.value)
    assert '確認' in str(e.value)


def test_context_is_bounded_and_endpoint_credentials_not_exposed(monkeypatch):
    with pytest.raises(ai.ConsultantError):ai.bounded_context({'text':'x'*200000})
    monkeypatch.setenv('LORASIM_AI_BASE_URL','https://user:secret@provider.example/v1');monkeypatch.setenv('LORASIM_AI_MODEL','test')
    assert not ai.configuration()['configured']
    assert ai.configuration()['base_url']==''


def test_saved_run_uses_its_snapshot_and_endpoint_invokes_provider(tmp_path,monkeypatch):
    from lorasim.jobs import Store
    store=Store(tmp_path)
    p=Profile(name='saved-condition')
    jid=store.submit(p.model_dump_json(),None)
    store.finish(jid,'completed',summary={'abstract':{'mean_ed_prr':.75}})
    c=TestClient(create_app(tmp_path))
    r=c.post('/api/consultant/preview',json={'yaml':Profile(name='edited-different-condition').model_dump_json(),'job_id':jid})
    assert r.status_code==200
    context=r.json()['context']
    assert context['profile']['name']=='saved-condition'
    assert context['run']['summary']['abstract']['mean_ed_prr']==.75
    captured={}
    def fake_consult(ctx,question):
        captured['context']=ctx
        return {'answer':'検証用の応答','model':'test'}
    monkeypatch.setattr('lorasim.api.consult',fake_consult)
    reply=c.post('/api/consultant/ask',json={'context':context,'question':'改善案は？'})
    assert reply.status_code==200 and reply.json()['answer']=='検証用の応答'
    assert captured['context']==context


def test_openai_default_requires_server_key_before_any_network_call(tmp_path,monkeypatch):
    monkeypatch.setenv('LORASIM_AI_BASE_URL','')
    monkeypatch.setenv('LORASIM_AI_MODEL','test-model')
    def no_network(*args):pytest.fail('未設定のAPIキーで外部接続してはいけない')
    monkeypatch.setattr(ai,'build_opener',no_network)
    c=TestClient(create_app(tmp_path))
    config=c.get('/api/consultant/config').json()
    assert config['base_url']=='https://api.openai.com/v1'
    assert config['provider']=='OpenAI' and not config['configured']
    assert 'APIキーが未設定' in config['hint']
    r=c.post('/api/consultant/ask',json={'context':{},'question':'設定の相談'})
    assert r.status_code==422 and 'APIキーが未設定' in r.text


def test_openai_request_uses_default_endpoint_and_server_credentials(monkeypatch):
    monkeypatch.setenv('LORASIM_AI_MODEL','test-openai-model')
    monkeypatch.setenv('LORASIM_AI_API_KEY','secret-server-key')
    monkeypatch.setenv('LORASIM_AI_MAX_TOKENS','3000')
    class Mock:
        def open(self,request,timeout):
            assert request.full_url=='https://api.openai.com/v1/chat/completions'
            assert request.get_header('Authorization')=='Bearer secret-server-key'
            payload=json.loads(request.data)
            assert payload['model']=='test-openai-model'
            assert payload['max_completion_tokens']==3000
            return io.BytesIO(json.dumps({'choices':[{'message':{'content':'実験条件を確認しました。'}}]}).encode())
    monkeypatch.setattr(ai,'build_opener',lambda *args:Mock())
    config=ai.configuration()
    assert config['configured'] and not config['hint']
    assert 'secret-server-key' not in json.dumps(config)
    assert ai.consult({},'設定の相談')['answer']=='実験条件を確認しました。'


@pytest.mark.parametrize('limit',['0','-1','bad',''])
def test_bad_token_limit_is_actionable_japanese_error(tmp_path,monkeypatch,limit):
    monkeypatch.setenv('LORASIM_AI_MODEL','test')
    monkeypatch.setenv('LORASIM_AI_API_KEY','secret-server-key')
    monkeypatch.setenv('LORASIM_AI_MAX_TOKENS',limit)
    c=TestClient(create_app(tmp_path))
    config=c.get('/api/consultant/config').json()
    assert not config['configured'] and '出力上限が不正' in config['hint']
    r=c.post('/api/consultant/ask',json={'context':{},'question':'設定の相談'})
    assert r.status_code==422 and 'LORASIM_AI_MAX_TOKENS' in r.text


@pytest.mark.parametrize('url',['http://api.openai.com/v1','https://[invalid'])
def test_invalid_endpoint_rejected_without_exposing_url(monkeypatch,url):
    monkeypatch.setenv('LORASIM_AI_BASE_URL',url)
    config=ai.configuration()
    assert not config['configured'] and config['base_url']==''
    assert '接続先URLが不正' in config['hint']


def test_local_compatible_server_can_still_run_without_key(monkeypatch):
    monkeypatch.setenv('LORASIM_AI_BASE_URL','http://localhost:11434/v1')
    monkeypatch.setenv('LORASIM_AI_MODEL','local-model')
    assert ai.configuration()['configured']
