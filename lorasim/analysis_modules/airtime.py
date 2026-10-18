from .base import AnalysisModule, bars, stacked
class Airtime(AnalysisModule):
    label='通信時間・SF/CH配分・再送'
    description='UL送信時間と再送比率、SF/CH別の時間配分。重なった送信も加算するためチャネル占有率ではありません。'
    sources=['https://www.mdpi.com/1424-8220/18/6/1716']
    def compute(self,c,o):
        rows=[c.radio(r) for r in c.runs]
        views=[bars('平均UL通信時間',c.runs,[r['mean_airtime_s'] for r in rows],'s / 送信'),bars('再送試行の割合',c.runs,[r['retransmission_fraction'] for r in rows],'割合')]
        for key,title in [('sf_airtime_s','SF別のUL送信時間配分'),('channel_airtime_s','CH別のUL送信時間配分')]:
            views.append(stacked(title,[{k:v/r['total_airtime_s'] for k,v in r[key].items()} if r['total_airtime_s'] else {} for r in rows],[r['experiment_name']+' / '+r['id'][:6] for r in c.runs],'Run'))
        return dict(rows=[dict(run_id=r['id'],**x) for r,x in zip(c.runs,rows)],views=views)
PLUGIN=Airtime
