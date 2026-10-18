from .base import AnalysisModule, bars
class Efficiency(AnalysisModule):
    requires=frozenset({'energy'})
    label='受信通信量・エネルギー効率'
    description='1台以上のGWに届いた各UL試行のアプリペイロードを集計。再送の重複受信を含み、アプリのgoodputとは異なります。'
    sources=['https://www.mdpi.com/2224-2708/11/4/65','https://www.mdpi.com/1424-8220/18/6/1716']
    def compute(self,c,o):
        rows=[]
        for r in c.runs:
            d=c.delivery(r);bits=d['received_payload_bits'];e=r['energy']['total_j']
            rows.append(dict(run_id=r['id'],**d,j_per_received_bit=e/bits if bits and r['energy']['measured'] else None,received_bit_per_j=bits/e if e and r['energy']['measured'] else None))
        return dict(rows=rows,views=[bars(title,c.runs,[r[key] for r in rows],unit) for key,title,unit in [('j_per_received_bit','受信ペイロード1 bit当たりの消費','J / bit'),('received_bit_per_j','エネルギー当たりの受信ペイロード','bit / J'),('received_payload_bps','全観測時間当たりの区間内受信ペイロード','bit / s（再送込み）')]])
PLUGIN=Efficiency
