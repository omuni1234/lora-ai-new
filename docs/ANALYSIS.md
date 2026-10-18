# Experiment管理・分析仕様

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
- 同じExperiment/ED数の複数Runでは、対象EDの総数で重み付けします。Run間で同じIDでも異なるED標本です。

## UL分類

1つのULに対して1件の`uplinks`行を生成します。

1. 1台以上のGWでReceivedなら`Received`。
2. 成功がなく終了時点で未判定のGWがあれば`Censored`。割合の分母から除外。
3. 全GWで同じ損失なら、その損失名。
4. 全GWで失敗し、理由が混在したら`MixedLoss`。個々の理由は`gw_outcomes`および`receptions`に残します。

`Censored`を他の損失として割り振りません。LostBecauseTx/NoMoreReceivers等のGWごとの総件数をそのままUL損失率にすると、複数GWで二重に数えてしまうため使用しません。PRRはULの到達率です。ACK受信率とは異なります。

## グラフ

- ED数別: Experiment×ED数の積み重ね。成功＋全損失分類の合計が1。
- 時系列: Experiment×ED数を選択。横軸はEDごとの送信通番。同じ区間・ビンを全Runで共有してED平均。最大ビン数は1–500、ED×ビンのメモリ上限に応じて幅を自動調整し、実際の区間を返します。細かい挙動は範囲を狭めて確認してください。
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

## 位置・送信の可視化負荷

workerに可視化のタイマーや位置サンプリング追加イベントはありません。GUIがONで表示中のときだけAPIへ要求し、内蔵移動の位置を独立RNGで再構成します。独自プラグインはGUI経由では実行しません。RandomWalkの極端に長い再生は1ED当たり10,000ステップで打ち切り、位置取得不能を返します。

GUIは最大500 EDを一定strideで抽出し、送信は最大1,000件を表示します。API上限は2,000 ED/送信、直近8つのコミット済みParquet partのみ。全送信履歴の逐次再生ではありません。ブラウザ非表示/可視化OFF/切断で新たな取得・描画を停止します。既に処理中の1要求が完了する場合はあります。可視化対象の選択は無線計算の結果に影響しません。パケットをクリックするとGW RSSI・TP・伝搬損失を表示します。
