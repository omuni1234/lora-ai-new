"""Read existing per-GW outcomes; never reclassify a network-level UL."""
from collections import Counter, defaultdict
import json
from typing import Literal

from pydantic import Field
from lorasim.interfaces import Options
from .base import AnalysisModule, stacked
from ._common import profile, table, title
from ._tvt import records


class Settings(Options):
    population: Literal['all', 'network_failures', 'mixed_only'] = Field(
        'all', title='集計対象UL',
        description='all：全判定済みUL。network_failures：全GWで失敗したUL。mixed_only：そのうちGWごとに失敗理由が異なるUL（MixedLoss）。判定未了ULは常に除外します。')
    gateway_ids: list[str] = Field(default_factory=list, max_length=64,
        title='対象GWのID', description='空欄は保存プロファイル内の全GW。指定したGWごとの受信結果を個別に集計します。')


class GatewayOutcomes(AnalysisModule):
    Options = Settings
    label = 'GW別の受信結果・複合損失の内訳'
    description = '既存gw_outcomesからGW別の損失要因を表示。UL全体のPRRや損失分類は変更しません。'

    def compute(self, c, o):
        views, summary, details, notes = [], [], [], []
        population_label = dict(all='全判定済みUL', network_failures='全GW失敗UL', mixed_only='複合損失UL')[o.population]
        for r in c.runs:
            known = [g.id for g in profile(c, r).gateways]
            selected = [g for g in known if not o.gateway_ids or g in o.gateway_ids]
            unknown = sorted(set(o.gateway_ids) - set(known))
            if unknown:
                notes.append(f"{r['id']}: プロファイルにないGWを除外しました: {', '.join(unknown)}")
            data = {g: defaultdict(Counter) for g in selected}
            missing = Counter()
            eligible = censored = 0
            for v in records(c, r, 'uplinks', ['device_id', 'tx_index', 'outcome', 'gw_outcomes']):
                if not c.selected(v['tx_index']):
                    continue
                if v['outcome'] == 'Censored':
                    censored += 1
                    continue
                if o.population == 'network_failures' and v['outcome'] == 'Received':
                    continue
                if o.population == 'mixed_only' and v['outcome'] != 'MixedLoss':
                    continue
                eligible += 1
                try:
                    outcomes = json.loads(v['gw_outcomes'])
                    if not isinstance(outcomes, dict):
                        outcomes = {}
                except (TypeError, ValueError):
                    outcomes = {}
                for g in selected:
                    result = outcomes.get(g)
                    # Keep actual cause names, but never turn missing/pending data
                    # or a network aggregate into a per-GW physical failure.
                    if not isinstance(result, str) or not result or result in ('Censored', 'SimulationEnd', 'Cancelled', 'MixedLoss'):
                        missing[g] += 1
                        continue
                    data[g][v['device_id']][result] += 1
            parts, labels = [], []
            for g in selected:
                eds = data[g]
                counts = sum(eds.values(), Counter())
                fractions = {cause: sum(hist[cause] / hist.total() for hist in eds.values()) / len(eds)
                             for cause in sorted(counts)} if eds else {}
                # Empty data is N/A in the table, never a zero-success bar.
                if eds:
                    parts.append(fractions)
                    labels.append(g)
                summary.append([r['id'], g, population_label, eligible, len(eds), counts.total(), missing[g], censored])
                for cause in sorted(counts):
                    details.append([r['id'], g, cause, counts[cause], fractions[cause]])
            if parts:
                views.append(stacked(title(c, r) + ' · ' + population_label, parts, labels, 'GW（各GW内でED等重み）'))
            if missing:
                notes.append(f"{r['id']}: GW別の欠損・判定未了は除外し、UL全体の分類から補完していません。")
        return dict(views=[*views,
            table('GW別の対象件数', ['Run', 'GW', '対象UL', '対象UL数', '対象ED数', 'GW判定済み件数', 'GW記録欠損・未了', '除外した判定未了UL'], summary),
            table('GW別の受信結果', ['Run', 'GW', '受信結果', '件数', 'ED平均割合'], details)],
            notes=notes + ['割合は各GWについて、対象ULの判定済みGW受信件数をED別の分母としてED等重みで平均します。対象ED数0はN/Aです。',
                '同一ULが複数GWに現れます。GW別割合の和や平均はネットワーク全体のUL損失率ではありません。',
                '複合損失だけを選ぶと、そのUL集合内の条件付き分布になります。全体PRRとの直接比較には「全判定済みUL」を使ってください。'])


PLUGIN = GatewayOutcomes
