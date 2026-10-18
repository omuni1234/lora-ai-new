# モデルの根拠と適用範囲

## 固定した上流

- [signetlabdei/lorawan](https://github.com/signetlabdei/lorawan/tree/e45b4af428c92f30b2e6c201055809110758125f): `e45b4af428c92f30b2e6c201055809110758125f`
- [nsnam/ns-3-dev-git / ns-3.48](https://github.com/nsnam/ns-3-dev-git/tree/d2add90b452d600cfb4859baed8e9ea633519447): `d2add90b452d600cfb4859baed8e9ea633519447`

LoRaWAN側の`NS3-VERSION`が指定するns-3.48を採用しました。開発ブランチの将来の変更には自動追従しません。`lorasim/upstream.json`を各実行のmanifestへ保存します。

本実装は上流の**選択したモデルのPython再実装**です。LoRaWANスタック全体の完全移植でも、実機のビット誤り率を直接再現するモデルでもありません。以下の式の実体比較、PHYケース比較、機能テストを行っています。任意条件でns-3の全イベント列が一致するという主張はしていません。

## ソースと対応

| Python | 上流ソース・関数 | 扱い |
|---|---|---|
| `phy._airtime` | [lora-phy.cc / GetTimeOnAir](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/model/lora-phy.cc) | 式、LDRO、明示/暗黙ヘッダー、CRC、CR、プリアンブル |
| `phy.destroyed_by` | [lora-interference-helper.cc](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/model/lora-interference-helper.cc) | SF別重複エネルギー・Goursaud/ALOHA行列 |
| `phy.links` | [propagation-loss-model.cc / LogDistancePropagationLossModel](https://github.com/nsnam/ns-3-dev-git/blob/d2add90b452d600cfb4859baed8e9ea633519447/src/propagation/model/propagation-loss-model.cc) | 基準距離以下を基準損失へクランプ |
| `phy.links` | [propagation-delay-model.cc / ConstantSpeedPropagationDelayModel](https://github.com/nsnam/ns-3-dev-git/blob/d2add90b452d600cfb4859baed8e9ea633519447/src/propagation/model/propagation-delay-model.cc) | 距離/速度、ns時間分解能 |
| `engine._arrive/_receive_end/_reply` | [simple-gateway-lora-phy.cc](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/model/simple-gateway-lora-phy.cc) | TX、周波数、極性、感度、復調器数の順で判定。DL開始時にULを中断 |
| 同上・ED側 | [simple-end-device-lora-phy.cc](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/model/simple-end-device-lora-phy.cc) | 受信可能状態、周波数、SF、感度、先着ロック |
| `engine._tx_done/_open_window` | [class-a-end-device-lorawan-mac.cc](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/model/class-a-end-device-lorawan-mac.cc) | RX1/RX2、RX中の延長、ACK成功後のRX2抑止 |
| `engine._reply` | [network-scheduler.cc](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/model/network-scheduler.cc) / `network-status.cc` | 最初の成功ULから応答予約、受信RSSI順のGW選択、RX1で送信できない場合のみRX2 |
| `engine` duty記録 | [logical-lora-channel-helper.cc / AddEvent](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/model/logical-lora-channel-helper.cc) | 次回可能時刻 = 送信開始 + ToA / duty_cycle |
| `config`のEU設定 | [lorawan-mac-helper.cc](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/helper/lorawan-mac-helper.cc) | EU sub-band、電力上限、初期3チャネル、RX2 |
| heapイベント順序 | [scheduler.h](https://github.com/nsnam/ns-3-dev-git/blob/d2add90b452d600cfb4859baed8e9ea633519447/src/core/model/scheduler.h) | 時刻→挿入ID。並列ワーカー間に共有無線状態なし |

上流のテーブルや式を改変移植しているためGPL-2.0-onlyを適用しています。

## PHYの定義

シンボル時間は `T_sym = 2^SF / BW`。送信時間は

```
N_payload = 8 + max(ceil((8*PL - 4*SF + 28 + 16*CRC - 20*IH)
                         / (4*(SF - 2*DE))) * (CR + 4), 0)
T_air = (N_preamble + 4.25 + N_payload) * T_sym
```

`CR=1,2,3,4`は4/5,4/6,4/7,4/8です。BW=125000 Hz、SF11/12でDE=1。他は0。上流はCR別の感度改善やBERを計算していないため、本実装も符号化利得を追加しません。CRの変更はToA、干渉の重複時間、Duty Cycleへ影響します。

ULのPHYサイズは `application payload + 9 bytes`。上流の`LoraFrameHeader`はFPort込み8 bytes、MHDRが1 byteで、MICトレーラーはこのモデルにありません。ACKのPHYサイズも9 bytes。実LoRaWANの全オーバーヘッドを加えて13 bytesとすると上流比較が変わるため、9 bytesに合わせています。

伝搬は `PL(d) = reference_loss_db + 10*n*log10(max(d/d0,1))`、`RSSI = TP - PL`、`delay = d/c`。初期設定は上流の例と同じ`n=3.76, d0=1m, loss0=7.7dB`です。実測環境に校正された万能値ではありません。送受信位置を**送信開始時**に取得し、そのパケット中は電力と伝搬遅延を固定します。これは`LoraChannel::Send`と同じ扱いです。

GW感度はSF7→12の順に `[-130,-132.5,-135,-137.5,-140,-142.5] dBm`、EDは `[-124,-127,-130,-133,-135,-137] dBm`。等号は受信候補になります。BWによる感度補正を勝手に行わないため、現在の公開設定はBW125kHzに限定しています。

各干渉SFについて `E_i = Σ overlap_seconds * power_watts` を求め、`10 log10(E_signal / E_i)`が行列のしきい値未満なら破壊されます。行が希望SF、列が干渉SFです。周波数が同一の信号だけを加算し、UL/DLの極性差は干渉除去に使いません。感度以下、復調器不足で拒否された信号も干渉に含めます。熱雑音を別に加算するSINRモデル、隣接チャネル漏洩、確率的BERモデルは追加していません。

## Class Aと研究拡張

- UL終了からRX1=1秒、RX2=2秒。初期の待受タイムアウトは8 symbols。待受中に受信開始した場合、パケット末尾まで受信し、RX2時点でRX1がRX_ACTIVEならRX2を開きません。
- 自分宛てのACKを正しく受信すると次の受信窓と再送を抑止します。他ED宛てDLにもPHYが先着ロックすることがあり、復号後にアドレス不一致と扱います。
- GWは成功したULの受信電力順に選び、同電力はGW設定順で決着します。RX1でGWが使えずRX2で使える場合はRX2応答。RX1で送信したACKが電波上で失われても、サーバーはそれを知れないためRX2再送しません。
- `max_attempts`は初回を含みます。未確認送信もNbTrans相当の繰返しが可能。再送待ちはUniform(1,3)秒、その後Duty Cycleを守ります。
- **研究拡張**として、再送も含め各attemptで方策を呼び、SF/TP/CR/チャネルを変更できます。Duty Cycle延期中は選んだ設定を保持します。選択時刻と実送信時刻が異なる場合があります。
- ED観測へGW受信成功、GW RSSI、損失原因を渡しません。失敗ACKにRSSIを捏造しません。未確認送信のACK欄は`null`です。位置を観測できるのは`observe_position: true`のEDだけです。

## 上流との差分・未対応範囲

1. データ面のClass Aモデルです。OTAA/join、暗号化/MIC、フレームバイト列、MAC command、ADR command/backoff、Class B/C、エネルギーモデル、LR-FHSS/FSKは移植していません。上流のADRを有効にした実験と比較する場合は同等方策を拡張する必要があります。
2. 上流の任意のns-3伝搬モデルの組合せには対応しません。建物侵入損失、相関シャドーイング、Nakagami等は未実装。モデルを無断で近似置換することはしません。
3. GWとサーバー間のP2Pリンクは省略し、`backhaul_delay_s`という一つの応答遅延で表現します。シリアライズ・キュー・リンク損失・GatewayStatusの1ms予約余白は再現しません。
4. アプリケーションはEDごとに一つの未完了メッセージのみを保持します。periodicの次回予定がMAC処理中に過ぎた場合、処理終了後へ延期し、一斉の追いかけ送信をしません。上流のPeriodicSenderの生成タイミング／busy時5秒延期／新パケットによる置換とは異なります。負荷の高い実験ではこの違いが重要です。
5. NumPy PCG64を使用します。ns-3のRNG系列とは異なります。同じseed番号だけで移動やランダム選択が一致するわけではありません。厳密比較では決定的配置・時刻・方策を指定してください。
6. 移動は静止、線形補間Waypoints、一定時間ごとの方向再選択＋反射境界Random Walkです。ns-3の全MobilityModelオプションの逐語移植ではなく、同種モデルのプログラマブルな実装です。
7. 過去の全干渉リストを保持せず、重複積分を受信中の6要素配列へ蓄積します。これは同じ式の逐次計算です。ただし上流の`CleanOldEvents`は100イベント超で終了後2秒の信号を削除するため、長いパケットと大量干渉の組合せでは過去干渉が消える可能性があります。本実装はその保持期限を再現せず、物理的な全重複エネルギーを保持します。
8. ns解像度と同時刻FIFO順は共通ですが、イベント生成順そのものがns-3のノード登録順と一致する保証はありません。感度・capture境界付近の浮動小数点差にも注意してください。
9. `duration_s`は硬い終了時刻です。終了時刻ちょうど以降のイベントは実行しません。未完了パケットを損失と決めつけず、`SimulationEnd`/`AttemptTruncated`を保存します。

研究論文で用いる際には、対象シナリオ、上流コミット、これらの差分、入力プロファイル、出力manifestを併記してください。完全なMAC軌跡の差分テスト、Linux/CUDA実機試験は今後の検証項目です。
