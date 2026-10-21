# トラッキング仕様（schema 5）

現行Runのmanifest.schema_versionは5です。以下の基本表に加え、末尾に記載するTVT診断用の列・表を使用します。旧版への対応を確認できるよう、導入版を節名に残しています。

## 出力構成

```
runs/<job-id>/
  profile.yaml                    # デフォルトを展開した実行入力
  manifest.json                   # 上流commit、設定ハッシュ、Pythonソースハッシュ、実行環境・依存版
  progress.json                   # 最新進捗、atomic replace
  summary.json                    # 正常完了・キャンセル時の集計
  policy_state.jsonl              # built-in banditの最終Q値・選択回数（EDごと）
  selector_states/part-000000.parquet # SEMAの初期状態・消去時だけ記録
  transmissions/part-000000.parquet
  receptions/part-000000.parquet
  feedback/part-000000.parquet
  events/part-000000.parquet
  notifications.jsonl             # 通知設定があるジョブのみ
  error.log                       # 失敗したジョブのみ（worker実行時）
```

`packet_id`は実際に空中へ送信したUL/DLのID（ジョブ内連番）。`message_id`はアプリケーションメッセージID。再送ULとそのACKはmessage_idを共有し、packet_idは毎回異なります。`attempt`は初回1。ED IDはプロファイルのgroups記載順に0から割り当てます。これらのIDはジョブを跨いで一意ではないため、複数実験を結合する際はjob_idを外側のキーにしてください。

時刻はsimulation timeです。例外として`policy_timing.cpu_ns/meta_cpu_ns`はホストの処理CPU時間です。`*_ns`は符号付き64bit整数のナノ秒、`*_s`は秒。場所m、電力dBm、損失dB、干渉エネルギーJ。欠損値はArrow null（0やNaNで代用しません）。JSONにNaN/Infinityを書き込みません。

## transmissions：空中送信1回につき1行

| カラム | 型 | 意味 |
|---|---|---|
| packet_id, message_id | int64 | 上記ID |
| direction | string | UL / DL |
| device_id | int64 | UL送信ED、またはDL宛先ED |
| gateway_id | string/null | DL送信GW。ULではnull |
| attempt | int32 | 元のUL試行番号 |
| start_ns, end_ns | int64 | 送信側の開始・予定終了。伝搬遅延を含まない |
| sf, cr | int32 | SF7–12、CR1–4（4/(4+CR)） |
| bandwidth_hz, frequency_hz | int64 | 帯域幅と中心周波数 |
| tx_power_dbm | float64 | 設定送信電力 |
| preamble_symbols | int32 | プリアンブル長 |
| implicit_header, crc | bool | LoRa PHYフレーム設定 |
| phy_bytes | int32 | PHYサイズ。ULはアプリペイロード+9、ACKは9 |
| x_m, y_m, z_m | float64 | 送信開始時の送信元座標 |

送信予定がDuty Cycle待ちのまま終わった場合、空中送信ではないためこの表に行を作りません。eventsに延期／打ち切りを残します。

## receptions：受信局ごとの結果

ULは各GWにつき1行。DLは宛先EDへ到来した場合に1行、加えて他EDが実際に受信ロックした場合にも行があります。ULがEDへ到達しただけのWrongPolarity、DLが関係のない睡眠EDや他GWへ到達しただけのイベントは保存しません。これらの信号も干渉計算には寄与します。

| カラム | 型 | 意味 |
|---|---|---|
| packet_id, message_id | int64 | transmissionsとの結合キー |
| direction, device_id | string, int64 | 元の送信の方向・対象ED |
| receiver_kind | string | GW / ED |
| receiver_id | string | GWの設定ID、またはED IDの10進文字列 |
| start_ns, end_ns | int64 | 受信局へ波が到達する開始・予定終了 |
| decision_ns | int64 | この結果を決めた時刻。拒否はstart、受信中断は中断時刻、正常完了はend |
| outcome | string | 下記列挙値 |
| rx_power_dbm | float64 | そのリンクの受信電力 |
| tx_power_dbm | float64 | 送信電力 |
| path_loss_db | float64 | 伝搬損失。rx_power_dbm = tx_power_dbm - path_loss_db |
| distance_m | float64 | 送信開始時の送受信間3D距離 |
| delay_ns | int64 | 伝搬遅延 |
| sf, cr, frequency_hz | int32, int32, int64 | 送信設定 |
| destroyed_by_sf | int32 | 破壊判定になった最初の干渉SF（7→12）。非干渉原因では0 |
| interference_j | list[float64]/null（非null時6要素） | 最後まで受信した候補のSF7→12別積分干渉J。途中中断・開始時拒否ではnull |

干渉行列はSF別に独立判定し、複数SFが破壊条件を満たしても`destroyed_by_sf`は上流同様に最初のSFです。先着ロック方式なので「強い方が途中で新しく捕捉し直す」preamble captureモデルではありません。

### 損失・結果分類

| outcome | 意味 / 上流の対応 |
|---|---|
| Received | 正常復号 / ReceivedPacket |
| Interference | SF間干渉積分の判定で失敗 / LostPacketBecauseInterference |
| UnderSensitivity | 感度未満 / LostPacketBecauseUnderSensitivity |
| NoMoreReceivers | GW復調器がすべて使用中 / LostPacketBecauseNoMoreReceivers |
| LostBecauseTx | GWがDL送信中、またはDL開始により受信中断 / NoReceptionBecauseTransmitting、packet trackerのLOST_BECAUSE_TX |
| WrongFrequency | 受信周波数と不一致。GWで上流はtrace未実装の条件も明示記録 |
| WrongSpreadingFactor | EDの受信SFと不一致 |
| NotListening | DL宛先EDが睡眠・TXなどで受信可能状態でない |
| AlreadyReceiving | EDが別の信号へロック済み |
| NotAddressed | 他ED宛てDLをロック・復号した。目的のACKではない |
| SimulationEnd | 受信途中でduration_sへ到達。物理層損失とは区別 |
| Cancelled | キャンセル時に受信中。物理層損失とは区別 |

GWは送信中→周波数→極性→感度→復調器数の順に拒否判定するため、複数要因が同時成立しても先行理由だけを記録します。早期拒否した信号の最終干渉成否を仮想的に判定して二重計上することはしません。

## feedback：EDが得る結果

完了したUL attemptごとに1行。packet_idはそのULのID。message_id, device_id, time_ns, attempt, ack(bool/null), dl_rssi_dbm(float/null), airtime_s, tx_power_dbmを保存します。

- confirmedでACKを受信: ack=true、正しく受信した自分宛てDLのRSSIを記録。
- confirmedで受信なし／受信失敗: ack=false、dl_rssi_dbm=null。
- unconfirmed: ack=null（ACK未観測という意味）。UL成功の可否はEDへ知らせません。
- 終了時刻で未完了: feedback行を生成しません。falseを推測して学習させません。

`receptions.rx_power_dbm`にはシミュレーターが知る失敗リンクの電力もありますが、これは研究者向け真値でありED観測に含まれません。

## events・集計

`events`は time_ns, device_id, packet_id(nullable), event, detail を持ちます。RX1Open/RX2Open、RX1Timeout/RX2Timeout、RX1NoGateway/RX2NoGateway、DutyCycleDeferred、AttemptTruncatedを記録します。ACKが送られなかった場合、存在しないDLパケットの受信損失行を作りません。

summaryの`uplinks_received_any_gw`は「少なくとも一つのGWで成功したUL attempt数」。再送による重複配送を含みます。`rx_Received`はUL/DL・複数受信局を含むため、これをUL送信数で割ってPDRにしてはいけません。アプリケーションレベルの配送率は成功ULのmessage_idを重複排除して計算してください。終了境界の未完了attemptも区別してください。

## RAMと耐障害性

各テーブルのPythonバッファは`batch_rows`まで。既定4096行ごとにzstd圧縮の独立Parquetファイルへ書き込み、tmp→renameで公開します。単一の巨大Parquetを最後まで閉じない方式ではありません。空テーブルにもスキーマ付き0行ファイルを作ります。

異常終了時は既に閉じたpartが読めます。SIGKILLや電源断で最後の未flushバッファが失われる可能性があります。ファイルシステム全体のfsyncや停電時の完全なトランザクション耐久性は保証しません。workerが制御可能なキャンセルではfinallyでflushします。

エンジンは全パケット履歴を保持せず、伝搬中の信号配列と受信中の状態のみを残します。概ね`O(ED数 + GW数 + 同時信号数*(ED数+GW数) + 受信中候補 + batch_rows)`。移動モデル・MLモデルの独自メモリを含むプロセスRSSのハード上限ではありません。`max_air_bytes`（既定512MiB）は信号のNumPy配列量の上限で、超過時に近似的間引きをせずfailedにします。必要ならComposeのmem_limit等も設定してください。

## 分析例

小さな実験ならpandasで直接読み込みます。

```python
from pathlib import Path
import pandas as pd
r = Path('runs/YOUR_JOB_ID')
tx = pd.read_parquet(r / 'transmissions')
rx = pd.read_parquet(r / 'receptions')
ul = tx.query("direction == 'UL'")
gw = rx.query("direction == 'UL' and receiver_kind == 'GW'")
received_ids = gw.loc[gw.outcome.eq('Received'), 'packet_id'].unique()
finished_ids = pd.read_parquet(r / 'feedback').packet_id.unique()
# 完了attemptを母集団とするACKサイクル終了ベースのPHY配送率
eligible = ul[ul.packet_id.isin(finished_ids)]
print(eligible.packet_id.isin(received_ids).mean())
print(gw.groupby(['receiver_id', 'outcome']).size())
```

大きい実験では全件read_parquetを避け、Arrowの列選択・predicate pushdown・batch走査を使います。[scripts/analyze.py](../scripts/analyze.py)はRAMに全行を置かず、GWごとの受信試行の損失内訳を集計します。複数GWのmessage_id重複排除など、大きな結合が必要な処理にはディスクへspillできる分析エンジンも適しています。


## v0.2追加スキーマ（manifest.schema_version=2）

従来4テーブルは保持し、以下を追加します。全て同じbatch_rows上限・原子的part確定・zstd圧縮を使います。

### transmissionsの追加列

`tx_index:int64`はEDごとの実送信通番。UL/DLは対応するULと同じ番号です。`adr:bool`は方策ADR、`adr_ack_req:bool`はそのULで送った要求ビット（DLはfalse）。`link_adr_sf:int32?` / `link_adr_tp_dbm:float64?`はDLに載せた要求です。失敗DLでも要求値は記録され、適用の有無は`events.LinkADRReqApplied`で区別します。

### devices

`device_id:int64, group:string, policy:string, mobility:string, x_m/y_m/z_m:float64`。初期座標、割当後のモデル名（refがあればその名前、なければkind）。Runごと1 ED 1行。

### uplinks

`packet_id/message_id/device_id/tx_index/time_ns:int64, attempt:int32, policy/outcome/gw_outcomes:string, received:bool, x_m/y_m/z_m:float64`。

1実ULにつき1行。全GWの判定が出た時に確定し、終了時は残りをCensoredとして記録。`time_ns`はUL送信開始時刻、座標はその時刻の送信位置。`gw_outcomes`はGW ID→結果文字列のJSON。Receivedが1 GWでもあれば成功、全GWで同じ損失はその理由、異なる損失はMixedLoss。成功なしで未判定が残るとCensored。分析での分母・平均は[ANALYSIS.md](ANALYSIS.md)。

### energy

| 列 | 型 | 意味 |
|---|---|---|
| device_id / tx_index | int64 | ED、帰属送信番号（0は起動・未送信計算） |
| component | string | radio / mcu |
| state | string | SLEEP / STANDBY / TX / RX_ENABLED / RX_ACTIVE、idle / inference / training / inference_untransmitted |
| start_ns / end_ns | int64 | 状態区間。MCU activeは呼び出し時刻で両端同値 |
| duration_s | float64 | 状態時間、または想定/実測計算時間 |
| power_w / energy_j | float64 | 電力と消費量 |
| mode | string | state / baseline / fixed / declared / measured |

radioは全時間区間を隙間なく被覆し、`sum(energy_j)`がsummaryのradio_jと一致します。MCUはbaselineとactive増分を足します。最初の送信前・未送信計算の扱いは[ENERGY.md](ENERGY.md)。

### summary / API

`summary.energy`は全時間合計。`summary.abstract`はED平均PRR、全ED数、対象ED数、判定未了、割合、フェアネス、エネルギー。無線・MCUを合算したabstract内の期間分析値はtx_index=0を除きます。`/api/jobs/{id}/abstract`は保存済みの値を返します。

```python
import pyarrow.dataset as ds
# バッチごとのpandas変換ならRAM量を制限できます。
scanner = ds.dataset("runs/example/uplinks", format="parquet").scanner(
    filter=(ds.field("tx_index") >= 10) & (ds.field("tx_index") <= 100),
    batch_size=8192)
for batch in scanner.to_batches():
    frame = batch.to_pandas()
    # 必要な集約だけ更新。frameを全てリストへ貯めない。
```

## v0.3: 周波数制限とコード配置

`events.RX1SubBandDisabled` はRX1の周波数が `dl_sub_bands` で禁止され、DLをRX2へ回したことを示します。送信していないDLの損失行は生成しません。EDのRXウィンドウと消費電力計算は維持します。ULで禁止帯域を選んだ場合は設定エラーでRunを失敗させます。故意にパケット損失へ置き換えないため、成功率へ紛れ込みません。

manifestの`source_sha256`は `plugins/selector/adr.py` 等を含むパッケージ内の相対パスをキーにします。v0.2以前のファイル名だけのキーも分析時にはそのまま読めます。Parquetのスキーマ番号は2を維持します。

## v0.4追加スキーマ（manifest.schema_version=3）

既存列は維持。`transmissions.link_report_bytes`（int32）は研究用SNR/RSSI通知の追加byte数。LinUCBのDLでは9、その他とULは0。このbyte数は`phy_bytes`とairtimeに含まれる。

`feedback.ul_rssi_dbm`、`feedback.ul_snr_db`、`feedback.ul_tx_power_dbm`（float64、nullable）は、**その試行のDLを正常受信して取得した**UL情報。RSSI/SNRはfloat32として通知した値をfloat64列へ格納する。ULのTPは端末が既知の値。不達時は全てnullであり、GW受信テーブルから補完していない。`dl_rssi_dbm`と混同しない。過去の受信済み情報は選択器が内部保持するが、失敗フィードバックの列へコピーしない。

`policy_state.jsonl`には新選択器の最終状態を1 EDあたり1行保存する。Exp3の確率/対数重み、LinUCBの逆行列/b/θ/最終通知値を含む。時間ごとの行列をRAMへ蓄積しない。

並列保存ではpart番号と行順序は逐次版と一致するが、ファイルの作成完了順序は異なり得る。実行中の末尾partを完了順で連結せず、ファイル名順で読み込むこと。実験終了は全保存プロセスの完了後。manifest/profile.yamlにexecution設定と全ソースSHAを記録する。MCU measuredモードはホスト実時間依存なので、高速化・並列化を変えた場合のMCUエネルギーの一致は保証しない。fixed/declaredモードは同じ設定で一致する。

## v0.5の学習モデル

Parquetの物理列形式（schema_version=3）は変更していません。共通SF×CHは実行時の `profile.yaml` の `action_space` に固定保存されます。行動IDの復元はSF外側×周波数内側のリスト順です。`transmissions.sf/frequency_hz/tx_power_dbm/cr` が実際に使われた値です。

SEMA/DQNは `policy_state.jsonl` にED別学習集計を記録します。SEMAの `arms` は `(静止候補,移動候補)` の順で、`arm_counts` / `arm_rewards` は腕ID順です。`eliminated` の `experience` は当該EDのメタ試行累計。`selected_algorithms` は選択時の回数なので、終了時点で未送信・未確定の選択を含み得ます。`learners.*.experiences` はACKの成否が確定した経験のみです。`last_ack_rssi_dbm` は失敗時には保持値であり、その失敗試行で観測できたRSSIではありません。終端の未確定試行は学習しません。

SEMAの消去履歴は最大5件、DQNの再生配列は容量制限付きです。1送信ごとのニューラルネット重みや勾配のログは蓄積しません。重みを保存して途中再開する機能は含みません。

## v0.12追加スキーマ（manifest.schema_version=4）

既存の列を削除せず、`uplinks`と`transmissions`へ`motion_state: string`を追加しました。UL送信開始時の`stationary` / `moving` / `unknown`です。DLのtransmissionsではnull。選択器の観測や学習には渡さず、解析専用です。`uplinks`では判定完了時刻でなく送信開始時の状態を保持します。既存ファイルに列がなければ、組み込みStationaryへの割り当てだけ安全に推定し、他はunknownです。

新表`selector_states`:

| 列 | 型 | 意味 |
|---|---|---|
| device_id | int64 | 独立エージェントのED番号 |
| time_ns | int64 | 初期化時刻またはフィードバック後の状態変更時刻 |
| tx_index | int64 | 当該EDの実送信通番。初期状態は0 |
| experience | int64 | SEMAがACK成功/失敗を学習した累積回数 |
| arms | string | 静止/移動アルゴリズムの組のJSON配列。順序がアームID |
| active_arms | list<int32> | この時点以後に残存するアームID |

SEMAは初期状態＋消去のある時点だけ出力します（内蔵6アームはEDあたり最大6行）。同じ時点で複数アームを消去した場合は結果を1行にまとめます。最大送信回数に比例したスナップショット保存を行いません。空表は非対応選択器のRunでも生成されます。旧`policy_state.jsonl`は引き続き最終状態の保存用です。旧Runに過去の時刻を捏造する移行は行いません。

新表も既存Trackerのバッチ/バックプレッシャーを使用します。分析は[RESEARCH_ANALYSIS.md](RESEARCH_ANALYSIS.md)。


## TVT診断の追記（manifest.schema_version=5）

`tracking.policy_diagnostics`は既定falseです。trueにすると、CPU時間と実際の選択モデル・局所移動判定を記録します。従来の列・表と読み出し契約を維持し、旧Runにない値は分析側でN/Aにします。

| 表 / 列 | 型 | 定義 |
|---|---|---|
| policy_timing.device_id, tx_index | int64 | EDと実送信通番。未送信の選択は通番0 |
| policy_timing.time_ns | int64 | コールバック開始時のsimulation time |
| policy_timing.operation | string | inference / training |
| policy_timing.cpu_ns | int64 | process_time_nsで測ったselect / observe全体。Parquet書き込みは含まない |
| policy_timing.meta_cpu_ns | int64 | SEMAのメタ報酬・回数更新と消去。trainingの内数。他手法・inferenceでは0 |
| uplinks / transmissions.motion_epoch_start_ns | int64/null | 真の物理状態が始まったsimulation time。asset_waypointが読み取り専用で提供。初期静止0、未対応モデルはnull、DLはnull |
| uplinks / transmissions.selected_algorithm | string/null | 選択時の下位モデル。SEMAはA/B/C/epsilon、固定B/C対照はB/C。フックがない選択器は既存policyラベル。診断無効・DLはnull |
| uplinks / transmissions.detected_motion_state | string/null | 選択時の局所位置差判定。真の移動状態とは独立。未対応・無効・DLはnull |
| feedback.tx_index | int64 | 対応する実送信通番。診断スイッチによらず記録 |

状態開始時刻は対応移動モデルなら診断スイッチによらず保存します。初回の状態と状態遷移後を区別し、複数の状態区間を跨いだ送信窓を作らないための記録です。これらの診断情報はObservation/Feedbackに加えません。

CPU計測値はホスト上の処理時間であり、simulated MCUの時間・エネルギー計算には使用しません。従来のmeasured MCUモードは従来どおりコールバックのwall timeを計測します。CPU計測の追加自体の小さな実行負荷はあるため、実時間の完全一致を要求する指標ではありません。

大きな解析ではParquetを8192行ずつ読み、一時SQLiteで順序整列・分位点を計算します。[TVTの集計定義](TVT_GUI_EXPERIMENTS.md)を参照してください。
