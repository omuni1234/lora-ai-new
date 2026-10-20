"""Bounded, immutable analysis evidence. No extra Parquet scan or simulation."""
import hashlib
import json
import re
import time
import uuid
import threading
from .tracking import atomic_json
_SNAPSHOT_LOCK=threading.Lock()
from .consultant import bounded_context, ConsultantError, scrub
from .config import load_profile


def sampled(value, limit, omissions, path=''):
    if isinstance(value,dict):
        return {k:sampled(v,limit,omissions,path+'/'+str(k)) for k,v in value.items()}
    if isinstance(value,list):
        indices=list(range(len(value)))
        if len(value)>limit and not (path.endswith('/columns') or re.search(r'/rows/\d+$',path)):
            indices=sorted({round(i*(len(value)-1)/(limit-1)) for i in range(limit)})
            omissions.append({'path':path,'original_count':len(value),'included_count':len(indices),'indices':indices,'method':'evenly spaced indices, endpoints included'})
        return [sampled(value[i],limit,omissions,path+'/'+str(i)) for i in indices]
    if isinstance(value,str) and len(value)>6000:
        omissions.append({'path':path,'original_characters':len(value),'included_characters':6000})
        return value[:6000]+' [省略]'
    return value


def profile_context(p):
    """Only models used by this profile; never attach unrelated library entries."""
    result=p.model_dump(mode='json')
    for section in ('groups','assignments'):
        for obj,raw in zip(getattr(p,section),result[section]):
            for category in ('policy','mobility','timing'):
                spec=getattr(obj,category,None)
                if spec is not None: raw[category]=p.component(spec,category).model_dump(mode='json')
    result.pop('models',None);result.pop('model_files',None)
    return scrub(result)


def snapshot(store, result, selection):
    # Main per-Run scalar results are never subsampled. Only nested views and profiles.
    runs=[];evidence=[]
    for r in result['runs']:
        item=store.get(r['id'])
        profile=profile_context(load_profile(item['profile'],text=True))
        condition={k:v for k,v in profile.items() if k not in {'name','seed','tracking','notifications'}}
        profile=scrub(profile)
        meta={k:item.get(k) for k in ('id','name','status','created','started','finished')}
        path=store.root/'runs'/r['id']/'manifest.json'
        if path.exists():
            try:
                manifest=json.loads(path.read_text())
                meta['environment']={k:manifest.get(k) for k in ('simulator_version','platform','python','dependencies','profile_sha256')}
            except (OSError,ValueError,AttributeError):
                meta['environment']={'unavailable':'実行環境の記録を読み取れませんでした。'}
        meta['condition_sha256']=hashlib.sha256(json.dumps(scrub(condition),sort_keys=True).encode()).hexdigest()
        meta['profile']=profile
        evidence.append(meta)
        run={k:v for k,v in r.items() if k not in {'series','device_prr'}}
        # 'measured: false' must not be interpreted as zero consumption.
        if not run.get('energy',{}).get('measured'): run['energy']={'measured':False}
        runs.append(run)
    base=dict(source='analysis_result',created=time.time(),selection=selection,
              definitions=result['definitions'],runs=runs,skipped_runs=result.get('skipped_runs',[]))
    for limit in (100,40,16,6,2):
        omissions=[]
        context={**base,'run_conditions':[sampled(x,limit,omissions,'run_conditions/'+str(i)) for i,x in enumerate(evidence)],
                 'modules':[sampled(x,limit,omissions,'modules/'+str(i)) for i,x in enumerate(result.get('modules',[]))],
                 'sampling':{'array_limit':limit,'omissions':omissions,
                             'note':'Run別の主要集計値は全件。長い配列のみ等間隔で抜粋。省略データ・未選択モジュールは未観測であり、局所変動・収束を断定できない。'}}
        try: context=bounded_context(context);break
        except ConsultantError:
            if limit==2: raise ConsultantError('AI相談用の集計データが大きすぎます。Experimentまたは分析モジュールを絞って再分析してください。') from None
    sid=uuid.uuid4().hex;path=store.root/'consultant'/'analyses';path.mkdir(parents=True,exist_ok=True)
    encoded=json.dumps(context,ensure_ascii=False,allow_nan=False)
    with _SNAPSHOT_LOCK:
        atomic_json(path/(sid+'.json'),context)
        # Concurrent API processes may have pruned a file since glob() returned it.
        old_files=[]
        for p in path.glob('*.json'):
            try: old_files.append((p.stat().st_mtime,p))
            except FileNotFoundError: pass
        for _,old in sorted(old_files,reverse=True)[20:]: old.unlink(missing_ok=True)
    return dict(id=sid,bytes=len(encoded.encode()),sampled_arrays=len(omissions),run_count=len(runs),created=base['created'])


def read_snapshot(root,sid):
    if not re.fullmatch('[a-f0-9]{32}',sid): raise ConsultantError('分析結果の参照が不正です。再分析してください。')
    try: return bounded_context(json.loads((root/'consultant'/'analyses'/(sid+'.json')).read_text()))
    except FileNotFoundError: raise ConsultantError('相談用の分析結果が保存期限外または削除済みです。再分析してください。') from None
    except (OSError,ValueError): raise ConsultantError('相談用データを読み取れません。再分析してデータを準備し直してください。') from None
