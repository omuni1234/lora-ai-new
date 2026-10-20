import numpy as np
from pydantic import Field
from lorasim.interfaces import Options
from .base import AnalysisModule
from ._common import profile, records, title


class Settings(Options):
    grid_size: int=Field(20,ge=2,le=60,title='縦横の分割数',description='エリアをこの数ずつ分割します。座標は実際のUL送信開始位置。')
    sf: int | None=Field(None,ge=7,le=12,title='表示SF（未指定＝最頻SF）',description='指定SFを選んだ割合をセルごとに表示。未指定は最多のSF。同数は小さいSFを表示し、内訳をホバーで確認できます。')
    device_id: int | None=Field(None,ge=0,title='ED ID（未指定＝全ED）',description='特定EDの選択位置を確認できます。ED番号は0からです。')
    minimum_samples: int=Field(1,ge=1,title='表示に必要な最小送信数',description='標本が少ないセルは灰色。送信数の多いEDほどこの分布への寄与が大きくなります。')


class SpatialSF(AnalysisModule):
    Options=Settings
    label='位置に対する選択SFの統計'
    description='UL送信開始時のSFを位置ごとに集計します。GWの受信成否で選別せず、再送も含む実送信の分布です。'
    def compute(self,c,o):
        views=[]; notes=[]
        for r in c.runs:
            p=profile(c,r); grid=np.zeros((o.grid_size**2,6),dtype=np.int64); outside=0
            for direction,ed,index,x,y,sf in records(c,r,'transmissions',['direction','device_id','tx_index','x_m','y_m','sf']):
                if direction!='UL' or not c.selected(index) or (o.device_id is not None and ed!=o.device_id): continue
                if x is None or y is None or sf not in range(7,13): continue
                if not 0<=x<=p.area_m[0] or not 0<=y<=p.area_m[1]: outside+=1; continue
                ix=min(o.grid_size-1,int(x/p.area_m[0]*o.grid_size))
                iy=min(o.grid_size-1,int(y/p.area_m[1]*o.grid_size))
                grid[iy*o.grid_size+ix,sf-7]+=1
            cells=[]
            for counts in grid:
                n=int(counts.sum())
                value=(int(counts.argmax())+7 if o.sf is None else float(counts[o.sf-7]/n)) if n>=o.minimum_samples else None
                cells.append(dict(value=value,n=n,counts=counts.tolist()))
            views.append(dict(type='heatmap',title=title(c,r),grid_size=o.grid_size,area_m=list(p.area_m),
                              cells=cells,mode='sf' if o.sf is None else 'fraction',sf=o.sf,
                              gateways=[dict(id=g.id,position=g.position) for g in p.gateways]))
            if outside: notes.append(f"{r['id']}: エリア外の {outside} 送信を地図から除外しました。")
        return dict(views=views,notes=notes)
PLUGIN=SpatialSF
