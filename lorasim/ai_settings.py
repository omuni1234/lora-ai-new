"""Server-only, atomic OpenAI settings. Never return a stored key to the browser."""
import json
import os
from pathlib import Path
import tempfile
import threading
from urllib.parse import urlsplit
from pydantic import BaseModel, Field, SecretStr, ConfigDict

OPENAI_BASE_URL = 'https://api.openai.com/v1'
_LOCK = threading.Lock()


class SettingsError(ValueError): pass


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    base_url: str = Field(default=OPENAI_BASE_URL, max_length=500)
    api_type: str = Field(default='responses', pattern='^(responses|chat_completions)$')
    model: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = None  # null/empty retains existing credential
    clear_key: bool = False
    max_tokens: int = Field(default=8000, ge=1, le=128000, strict=True)
    timeout_s: int = Field(default=180, ge=10, le=600, strict=True)


def normalize_url(value):
    base = value.strip().rstrip('/')
    # Accept a full endpoint pasted from the API reference as well as a base URL.
    for suffix in ('/chat/completions', '/responses'):
        if base.endswith(suffix): base = base[:-len(suffix)]
    try:
        p = urlsplit(base)
        valid = not any(c.isspace() or ord(c)<32 for c in base) and p.scheme in {'http', 'https'} and p.hostname and not (p.username or p.password or p.query or p.fragment)
        _ = p.port
        if p.hostname == 'api.openai.com':
            valid = valid and p.scheme == 'https' and p.path == '/v1' and p.port in (None, 443)
    except ValueError: valid = False
    if not valid:
        raise SettingsError('接続先URLが不正です。LORASIM_AI_BASE_URLまたはGUIの接続先を確認してください。OpenAIでは '+OPENAI_BASE_URL+' を指定します。')
    return base


def settings_path(root): return Path(root)/'private'/'ai-settings.json'


def load_settings(root=None):
    cfg = dict(base_url=os.getenv('LORASIM_AI_BASE_URL', '').strip() or OPENAI_BASE_URL,
               model=os.getenv('LORASIM_AI_MODEL', '').strip(), api_key=os.getenv('LORASIM_AI_API_KEY', '').strip(),
               api_type=os.getenv('LORASIM_AI_API_TYPE', 'chat_completions').strip(),
               max_tokens=os.getenv('LORASIM_AI_MAX_TOKENS', '2000'), timeout_s=os.getenv('LORASIM_AI_TIMEOUT_S', '180'),
               source='environment')
    if root is not None and settings_path(root).exists():
        try:
            saved = json.loads(settings_path(root).read_text())
            if not isinstance(saved,dict) or not isinstance(saved.get('api_key',''),str): raise ValueError
            cfg.update(SettingsUpdate.model_validate({k:v for k,v in saved.items() if k!='api_key'}).model_dump(exclude={'api_key','clear_key'}))
            cfg['api_key'] = str(saved.get('api_key', ''))
            cfg['source'] = 'gui'
        except (OSError, ValueError, TypeError):
            raise SettingsError('保存されたAI設定を読み取れません。接続設定で環境変数設定に戻してから保存し直してください。') from None
    return cfg


def public_settings(root=None, *, settings=None):
    try: cfg = dict(settings) if settings is not None else load_settings(root)
    except SettingsError as exc: return dict(configured=False, hint=str(exc), source='invalid', base_url='', model='', key_configured=False)
    problems=[]
    try: base=normalize_url(cfg['base_url'])
    except SettingsError as exc: base=''; problems.append(str(exc))
    is_openai=bool(base and urlsplit(base).hostname=='api.openai.com')
    if not cfg['model']: problems.append('モデル名が未設定です。GUIのモデル名またはLORASIM_AI_MODELを設定してください。')
    if is_openai and not cfg['api_key']: problems.append('OpenAI APIキーが未設定です。GUIまたはLORASIM_AI_API_KEYを設定してください。')
    if cfg['api_type'] not in {'responses','chat_completions'}: problems.append('API形式はresponsesまたはchat_completionsを指定してください。')
    for key, env, lo, hi in [('max_tokens','LORASIM_AI_MAX_TOKENS',1,128000),('timeout_s','LORASIM_AI_TIMEOUT_S',10,600)]:
        try:
            cfg[key]=int(cfg[key])
            if not lo<=cfg[key]<=hi: raise ValueError
        except (ValueError,TypeError):
            cfg[key]=None
            problems.append(('出力上限が不正' if key=='max_tokens' else '待機時間が不正')+f'です。{env}またはGUIで{lo}〜{hi}の整数を設定してください。')
    return dict(configured=not problems, hint=' '.join(problems), base_url=base, model=cfg['model'],
                provider='OpenAI' if is_openai else 'OpenAI API互換', api_type=cfg['api_type'],
                max_tokens=cfg['max_tokens'], max_completion_tokens=cfg['max_tokens'], timeout_s=cfg['timeout_s'],
                source=cfg['source'], key_configured=bool(cfg['api_key']))


def save_settings(root, body):
    cfg=body.model_dump(exclude={'api_key','clear_key'});cfg['base_url']=normalize_url(body.base_url);cfg['model']=body.model.strip()
    if not cfg['model']: raise SettingsError('モデル名を入力してください。')
    key=body.api_key.get_secret_value().strip() if body.api_key else ''
    if len(key)>4096 or any(ord(c)<33 or ord(c)>126 for c in key): raise SettingsError('APIキーの形式が不正です。空白や改行を除いて入力してください。')
    with _LOCK:
        previous=load_settings(root)
        if key and body.clear_key: raise SettingsError('APIキーの入力と削除は同時に指定できません。')
        if not key and not body.clear_key and previous['api_key']:
            try: same=normalize_url(previous['base_url'])==cfg['base_url']
            except SettingsError: same=False
            if not same: raise SettingsError('接続先の変更時は、その接続先のAPIキーを入力するか「保存済みキーを削除」を選択してください。以前のキーを別の接続先へ自動転送しません。')
        cfg['api_key']=key or ('' if body.clear_key else previous['api_key'])
        path=settings_path(root);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.ai-')
        try:
            with os.fdopen(fd,'w') as f:
                json.dump(cfg,f,ensure_ascii=False);f.flush();os.fsync(f.fileno())
            os.chmod(tmp,0o600);os.replace(tmp,path)
        finally:
            if os.path.exists(tmp): os.unlink(tmp)
    return public_settings(root)


def reset_settings(root):
    with _LOCK: settings_path(root).unlink(missing_ok=True)
    return public_settings(root)
