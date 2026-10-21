from .base import AnalysisModule
from ._common import table, line, title
from ._tvt import cpu_stats


class TVTCPU(AnalysisModule):
    label='TVT：推論・学習CPU時間とPRRの比較'
    description='実測ホストCPU時間の平均・95%点・最大値、選択処理とメタ更新の合計、学習時間、PRRとの関係を表示します。'

    def compute(self,c,o):
        views=[];rows=[];points=[];notes=[]
        for r in c.runs:
            data=cpu_stats(c,r)
            if not data['metrics']:
                notes.append(f"{r['id']}: CPU計測なし。新規実験で tracking.policy_diagnostics を有効にしてください。")
                continue
            for name,v in data['metrics'].items():
                rows.append([r['id'],name,v['n'],v['mean_us'],v['p95_us'],v['max_us']])
            v=data['metrics'].get('decision_plus_meta')
            if v and r['mean_ed_prr'] is not None:
                points.append(dict(label=title(c,r),points=[dict(x=v['mean_us'],y=r['mean_ed_prr'],n=v['n'])]))
            views.append(line(title(c,r)+' · CPU時間の推移',[dict(label=k,points=v) for k,v in data['series'].items()],
                              'EDごとの送信通番（最大100区間）','平均CPU時間 [µs]'))
        return dict(views=[table('CPU時間（実測）',['Run','処理','件数','平均 [µs]','95%点 [µs]','最大 [µs]'],rows),
            line('通信品質と計算時間',points,'行動選択＋メタ更新の平均CPU時間 [µs]','ED平均PRR',y_max=1),*views],
            notes=notes+['inferenceはメタ選択を含む行動選択全体、trainingはメタ更新を含むフィードバック処理全体。decision_plus_metaは同じED・送信通番で両方が存在する組だけを計数。',
                'process_time_nsによるホスト時間。MCU実測やエネルギー推定ではなく、実行環境の固定は強制しません。未送信の選択（通番0）は除外。'])


PLUGIN=TVTCPU
