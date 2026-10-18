# トラッキング仕様 v1

## 出力構成

```
runs/<job-id>/
  profile.yaml                    # デフォルトを展開した実行入力
  manifest.json                   # 上流commit、設定ハッシュ、Pythonソースハッシュ、実行環境・依存版
  progress.json                   # 最新進捗、atomic replace
  summary.json                    # 正常完了・キャンセル時の集計
  policy_state.jsonl              # built-in banditの最終Q値・選択回数（EDごと）
  transmissions/part-000000.parquet
  receptions/part-000000.parquet
  feedback/part-000000.parquet
  events/part-000000.parquet
  notifications.jsonl             # 通知設定があるジョブのみ
  error.log                       # 失敗したジョブのみ（worker実行時）
```

`packet_id`は実際に空中へ送信したUL/DLのID（ジョブ内連番）。`message_id`はアプリケーションメッセージID。再送ULとそのACKはmessage_idを共有し、packet_idは毎回異なります。`attempt`は初回1。ED IDはプロファイルのgroups記載順に0から割り当てます。これらのIDはジョブを跨いで一意ではないため、複数実験を結合する際はjob_idを外側のキーにしてください。

全時間はsimulation timeです。`*_ns`は符号付き64bit整数のナノ秒、`*_s`は秒。場所m、電力dBm、損失dB、干渉エネルギーJ。欠損値はArrow null（0やNaNで代用しません）。JSONにNaN/Infinityを書き込みません。

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
