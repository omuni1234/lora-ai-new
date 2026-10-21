from collections import defaultdict
from pydantic import Field, model_validator
from lorasim.interfaces import Options
from .base import AnalysisModule
from ._common import line, table, title
from ._tvt import scratch_database


class Settings(Options):
    horizons: list[int]=Field(default_factory=lambda:[10,50,100],min_length=1,max_length=8,title='遷移後の送信窓 H',description='同じ物理状態が続く間の最初のH送信。1〜1000、重複なし。')
    burnin: int=Field(2000,ge=0,title='初期除外送信数',description='最初の送信通番がこの値以下のエピソードを除外。初期状態も常に除外します。')
    @model_validator(mode='after')
    def valid(self):
        if len(set(self.horizons))!=len(self.horizons) or any(not 1<=h<=1000 for h in self.horizons):
            raise ValueError('horizons must be distinct integers in 1..1000')
        return self


class TVTTransitions(AnalysisModule):
    Options=Settings
    label='TVT：移動開始・停止後のPRRと損失'
    description='真の状態開始時刻でイベントを整列。H送信に満たない状態区間・打切りを完全窓と分離して表示します。'

    def compute(self,c,o):
        views=[];rows=[];notes=[];max_h=max(o.horizons)
        for r in c.runs:
            counts=defaultdict(lambda:[0,0,0.]);curve=defaultdict(lambda:[0,0]);missing=0
            with scratch_database() as db:
                db.execute('CREATE TABLE ul(ed INTEGER,tx INTEGER,epoch INTEGER,state TEXT,ok INTEGER,PRIMARY KEY(ed,tx))')
                for batch in c.batches(r['id'],'uplinks',['device_id','tx_index','motion_epoch_start_ns','motion_state','outcome']):
                    source=batch.to_pylist();valid=[v for v in source if v.get('motion_epoch_start_ns') is not None and v.get('motion_state') in ('stationary','moving')]
                    missing+=len(source)-len(valid)
                    db.executemany('INSERT INTO ul VALUES(?,?,?,?,?)',((v['device_id'],v['tx_index'],v['motion_epoch_start_ns'],v['motion_state'],None if v['outcome']=='Censored' else int(v['outcome']=='Received')) for v in valid))
                key=None;prefix=[];first=0
                def finish():
                    if key is None or key[1]<=0 or first<=o.burnin or not c.selected(first):return
                    state=key[2]
                    for h in o.horizons:
                        a=counts[(state,h)]
                        complete=len(prefix)>=h and all(v is not None for v in prefix[:h])
                        a[0]+=int(complete);a[1]+=int(not complete)
                        if complete:a[2]+=1-sum(prefix[:h])/h
                    for j,v in enumerate(prefix):
                        if v is not None:curve[(state,j+1)][0]+=v;curve[(state,j+1)][1]+=1
                for ed,tx,epoch,state,ok in db.execute('SELECT ed,tx,epoch,state,ok FROM ul ORDER BY ed,tx'):
                    current=(ed,epoch,state)
                    if current!=key:
                        finish();key=current;prefix=[];first=tx
                    if len(prefix)<max_h and (c.end is None or tx<=c.end):
                        # Missing transmission ordinals are unknown, not compressed away.
                        while len(prefix)<min(tx-first,max_h):prefix.append(None)
                        if len(prefix)<max_h:prefix.append(ok)
                finish()
            if missing:notes.append(f"{r['id']}: 状態開始時刻がないUL {missing} 件を対象外にしました。asset_waypointの新規Runで記録できます。")
            for state in ('stationary','moving'):
                for h in sorted(o.horizons):
                    complete,incomplete,loss=counts[(state,h)]
                    rows.append([r['id'],state,h,complete,incomplete,loss/complete if complete else None])
                pts=[dict(x=j,y=curve[(state,j)][0]/curve[(state,j)][1] if curve[(state,j)][1] else None,n=curve[(state,j)][1]) for j in range(1,max_h+1)]
                views.append(line(title(c,r)+' · '+state,[dict(label='遷移後PRR',points=pts)],'状態変化後の送信番号','PRR（その番号が観測されたエピソード）',y_max=1))
        return dict(views=[table('完全窓の平均損失と不完全窓数',['Run','遷移先状態','H','完全窓','不完全窓','損失率'],rows),*views],
            notes=notes+['初期状態と初期除外区間を除外。共通送信範囲の途中で始まる窓だけを採用し、終端で切れた窓は不完全。送信のない状態区間は観測対象外。',
                '各Hの損失は完全窓を等重み。PRR曲線は各番号に観測があるエピソードの平均で、標本数が変化します。収束時間ではありません。一時SQLiteを使用します。'])


PLUGIN=TVTTransitions
