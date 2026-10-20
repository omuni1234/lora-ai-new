# Experiment管理・分析仕様

v0.12: [分析モジュール開発](ANALYSIS_PLUGINS.md) / [指標・文献・累積の定義](METRICS.md)。

## ExperimentとRun

Experimentは比較条件をまとめる単位です。例: `ADR static`、`BADR mobile`。各Experimentに、ED数・seed・時間等の異なるRunを複数所属させられます。Runは1回のシミュレーションで、既存のjob IDと同じです。投入時にExperimentを選ばなければプロファイル名から新規作成します。既存データベースは起動時に列を追加し、過去の各Runを独立Experimentへ移行します。

比較するときは、異なるアルゴリズムを異なるExperimentへまとめてください。割り当て条件でアルゴリズムを混在させたRunの平均値は、その混合ネットワーク全体の値です。アルゴリズムが自動で識別・分離されるとは解釈しないでください。

```bash
python scripts/seed_comparison.py --data data --counts 20 40 60 --seeds 1 2
lorasim worker --data data --workers 2 --once
lorasim serve --data data
```

この例はMAC競合を見やすくするためDuty Cycleを無効化しています。規制を想定する実験では有効に戻してください。GUIの「比較・分析」でExperimentをチェックし、送信回数区間・ED数・MCU有無を指定します。チェックしていないExperimentは解析しません。completed/cancelledのRunだけ対象です。failed/interruptedの部分データはParquetから個別に調査できます。

## 送信番号と分母

`tx_index`は**EDごとの実送信通番（1始まり、再送を含む）**です。`attempt`（同一メッセージ内の試行番号）やsimulation timeとは異なります。範囲`[tx_start, tx_end]`は両端を含みます。

ED iの範囲内判定済みUL数を`N_i`、分類cの件数を`N_i,c`とします。

- `PRR_i = N_i,Received / N_i`。
- グラフの割合は`mean_i(N_i,c / N_i)`。各EDを等しく重み付けします。
- EDが1回でも送っていれば、全て失敗したEDも0として含みます。
- `N_i=0`のEDは未定義のため平均から除き、inactive_ed_countとして表示します。
- `pooled_prr = sum_i(success_i)/sum_i(N_i)`も別に保存します。EDの送信回数が不均一ならED平均と異なります。
- 従来のoutcomes/convergenceでは、同じExperiment/ED数の複数Runを対象EDの総数で重み付けします。PRR対ED数の新しい折れ線はRun等重みです。Run間で同じIDでも異なるED標本です。

## UL分類

1つのULに対して1件の`uplinks`行を生成します。

1. 1台以上のGWでReceivedなら`Received`。
2. 成功がなく終了時点で未判定のGWがあれば`Censored`。割合の分母から除外。
3. 全GWで同じ損失なら、その損失名。
4. 全GWで失敗し、理由が混在したら`MixedLoss`。個々の理由は`gw_outcomes`および`receptions`に残します。

`Censored`を他の損失として割り振りません。LostBecauseTx/NoMoreReceivers等のGWごとの総件数をそのままUL損失率にすると、複数GWで二重に数えてしまうため使用しません。PRRはULの到達率です。ACK受信率とは異なります。

## v0.12の追加

[新しい8モジュールの定義](RESEARCH_ANALYSIS.md)を参照してください。初期表示に数値一覧とPRR対ED数を追加しました。収束は連続した積み上げ面グラフ、表は個別CSV保存に対応します。

## グラフ

- ED数別: Experiment×ED数の積み重ね。成功＋全損失分類の合計が1。
- 時系列: Experiment×ED数ごとに、時点tまでの1〜t回の累積割合をED平均。開始通番は表示範囲だけに適用し、過去の履歴を捨てません。最大表示点数は1–500、ED×点数のメモリ上限に応じて間隔を調整します。
- フェアネス: 対象範囲のED別PRRからRunごとに計算。指標切り替え可能。
- エネルギー: 対象範囲のJ/ED。MCUを含むか選べます。状態別合計、成功UL当たりJ、起動待機を含むかはJSONにも記録します。

## フェアネス指標の根拠

PRRベクトル`x_i >= 0`を入力にします。全値0、または対象EDがないときは指標をnullとし、均等な成功であるかのような1を返しません。

| 指標 | 式 | 解釈 |
|---|---|---|
| Jain | `(sum x)^2 / (n sum x^2)` | 1に近いほど均等。絶対性能はPRRと併読 |
| Gini | `sum_i sum_j abs(x_i-x_j)/(2 n sum x)` | 0に近いほど均等 |
| Min/Max | `min(x)/max(x)` | 最も不利なEDに敏感 |
| CV | `population_std(x)/mean(x)` | 相対的なばらつき。小さいほど均等 |

Jainは[Jain, Chiu, Haweの原論文](https://arxiv.org/abs/cs/9809099)を参照。Giniの絶対差の定義は[NIST Gini Mean Difference](https://www.itl.nist.gov/div898/software/dataplot/refman2/auxillar/gmd.htm)、CVは[NIST Coefficient of Variation](https://itl.nist.gov/div898/software/dataplot/refman2/auxillar/coefvari.htm)を参照しています。Giniは全組合せの配列を作らず、ソート後の等価式でO(n log n)計算します。CVは標本の不偏推定ではなく、観測された全ED集団の記述統計です。Min/Maxは本実装の補助指標です。

公平だが全ED低PRRのケースがあるので、PRR、Jain/Gini、下位10% PRR、中央値を併せて確認してください。複数seedの分散・信頼区間を自動推定するものではありません。Run別CSVを使って後段解析してください。

## RAM・API・エクスポート

Arrowの8192行バッチで読み、巨大なpacket joinやDataFrame全件ロードを行いません。EDごとのカウンタと、最大100万ED×ビンセルのNumPy配列を使います。時間複雑度は保存行数に比例し、パケット数に比例してRAMを増やしません。1要求最大50 Experiment / 200 Run。解析はAPIプロセスで実行し、workerの無線イベントループには載せません。

`POST /api/analysis`:

```json
{"experiment_ids":["experiment-id"],"tx_start":10,"tx_end":100,"include_mcu":true,"bins":100,"ed_count":100}
```

結果の`runs`はRun別PRR/公平性/エネルギー、`by_ed_count`はED数別割合、`by_time`はExperiment別時系列です。GUIからJSONとRun別CSVを保存できます。精密な図はJSONまたは[TRACKING.md](TRACKING.md)のParquetをpandas/matplotlibで処理してください。

v0.1の結果は`uplinks`/`energy`/`tx_index`を持たないため、新しい解析の対象にできません。過去の生Parquetは変更せず、v0.2で再実行してください。解析時に古いRunが含まれていれば422エラーで明示します。

## 実験前の設定確認（v0.3）

中央パネルは実行前の設定確認専用です。初回表示と「設定を確認する」ボタンで、独立した乱数生成器によりサンプルEDを構成します。実行中の送信追跡Parquetをポーリングせず、workerには可視化用のイベント・記録・周期処理を追加しません。Run選択も結果表示だけです。時刻スライダーはブラウザに保持した位置サンプルを使い、通信しません。設定変更後は「再確認」を表示します。

既定80 ED、上限200 ED、1EDあたり既定12回・上限30回の生成予定を表示します。移動の方向変更回数には合計10,000回相当の予算を設け、超過するRandomWalkの軌跡は省略します。詳細は[PLUGINS.md](PLUGINS.md)。独自モデルは明示的な `preview_safe=True` の場合だけ呼び出します。モデル開発者は有限時間で戻り副作用がないことを保証してください。任意のPythonコードをサンドボックス化する機能ではありません。

プレビューはMACのDuty cycle待ち・再送・ACK・学習を模擬しません。予定時刻はアプリケーション生成時刻であり、実送信時刻ではありません。適用モデル名・定数、初回生成時刻、未学習状態の選択例を確認する用途です。省略した移動モデルは初期座標を表示し、その理由をED詳細に明記します。

後方互換の `/api/jobs/{jid}/visualization` と `/packets/{packet_id}` は手動の結果調査用に残っています。新GUIからの定期呼び出しはありません。

Experiment一覧のinfoはフォーカス・ホバー時に小さな概要だけ取得し、10秒間キャッシュします。作成・開始・終了時刻、実時間の合計、最新Runの条件、最後に実行したRunのmanifestに記録されたOS/Python/依存関係を表示します。異なるRunが同じ環境だったと推測せず、環境を取得したRun IDを明示します。環境記録がない場合は「記録なし」です。
