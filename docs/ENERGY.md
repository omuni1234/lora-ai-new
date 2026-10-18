# 無線・MCU消費電力

## 上流ソースと式

無線は固定コミット`e45b4af428c92f30b2e6c201055809110758125f`の以下を移植しています。

- [LoraRadioEnergyModel](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/model/lora-radio-energy-model.cc): `GetStateA`, `ChangeState`, `GetTotalEnergyConsumption`。
- [LinearLoraTxCurrentModel](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/model/lora-tx-current-model.cc): `CalcTxCurrent`。
- [LoraRadioEnergyModelHelper](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/helper/lora-radio-energy-model-helper.cc): 未設定では定電流TX、指定時に線形モデルを接続。
- [ns-3 BasicEnergySource](https://github.com/nsnam/ns-3-dev-git/blob/d2add90b452d600cfb4859baed8e9ea633519447/src/energy/model/basic-energy-source.cc): 既定電源電圧3.0 V。

状態区間ごとに `E = duration_s × I_A × supply_voltage_V` を加算します。定期ポーリングによる近似ではなく、イベントのns時刻差を使用します。RX_ENABLED（受信窓待受）とRX_ACTIVE（実受信）の電流は同じです。

| パラメータ | 既定値 | 上流対応 |
|---|---:|---|
| sleep_current_a | 0.0000001 A | SleepCurrentA |
| standby_current_a | 0.0014 A | StandbyCurrentA |
| tx_current_a | 0.028 A | TxCurrentA |
| rx_current_a | 0.0108 A | RxCurrentA |
| voltage_v | 3.0 V | BasicEnergySupplyVoltageV |
| initial_state | STANDBY | PHYとRadioEnergyModelの初期状態 |
| eta | 0.452750 | LinearLoraTxCurrentModel::Eta |
| tx_voltage_v | 3.3 V | 同::Voltage |
| base_current_a | 0.014646 A | 同::BaseCurrent |

線形TXモデルは `I_tx = 10^((TP_dBm-30)/10) / (tx_voltage_v × eta) + base_current_a` です。電源電圧と線形モデルの電圧は上流でも独立の設定です。実機校正では両方を明示してください。定電流モデルではTPを変えてもTX回路電流は変わりません。

```yaml
energy:
  radio:
    enabled: true
    tx_current_model: linear
    voltage_v: 3.3
    tx_voltage_v: 3.3
  mcu:
    enabled: true
    mode: fixed
    active_power_w: 0.015
    idle_power_w: 0.000003
    inference_s: 0.002
    training_s: 0.010
```

シミュレーション終了・キャンセル時まで状態積分します。電池枯渇による停止、電池内部抵抗、電圧変動、再充電は今回の消費電力量モデルに含みません。有限バッテリやBasicEnergySourceの枯渇コールバック全体を移植したものではありません。

## MCUは独立のモデル

推論は`select`、学習は`observe`の呼び出しに対応します。idleを時間全体に積算し、呼び出しごとに `(active_power_w - idle_power_w) × compute_duration_s` を追加します。activeはidle以上に制限します。radioとMCUは別部品なので電力を別々に加算し、分析時にMCUを含めるか選べます。

`fixed`は設定値、`declared`は方策の`inference_time_s` / `training_time_s`、`measured`はホスト実測を使います。GPU処理時間を対象MCUの処理時間とみなさないでください。measuredには初回モデルロード時間も含まれます。対象機器での値にはdeclaredを推奨します。

これは計算コストの積算モデルです。MCUの実行キュー、無線との競合、計算中の遅延・タスク重複はモデル化しません。仮定した計算時間が送信間隔を大きく超える実験では、利用者側でMCUスケジューリングの拡張が必要です。

## 記録と期間指定

`energy/part-*.parquet`へ状態区間・MCU呼び出しをストリーミングします。全時間を保持するRAM配列はありません。

- radio / MCU idleの帰属は、実送信k開始から次の実送信開始までを`tx_index=k`とします。最終送信は終了時刻まで。
- 最初の送信前は`tx_index=0`。起動時待機として期間指定の分析から除き、`startup_excluded_j`に別掲します。
- 推論は、その設定を実際に使った送信番号に帰属します。Duty Cycle延期中にシミュレーションが終わり未送信になった推論は、`tx_index=0, state=inference_untransmitted`として別計上します。
- 学習はフィードバック対象の実送信番号に帰属します。
- MCU activeのstart_ns=end_nsは「課金した呼び出し時刻」です。仮想処理時間は`duration_s`に記録します。radio/idleはend-startとdurationが一致します。
- 右側アブストラクトの合計は起動待機も含む全消費量。期間指定の分析は0を除くため、この2値は一致しない場合があります。

`summary.energy.radio_j / mcu_j`は全件合計です。ジュールを秒で割れば平均電力[W]、電圧で割れば電荷[C]になります。

## 数値照合

`validation/ns3-energy-oracle.cc`は実際の上流クラスを呼び出します。8つのTPにおけるTX電流、定電流/線形モデルの全状態積分2ケース、合計10ケースを照合しました。最大絶対誤差`1.6653345369377348e-16`。再実行:

```bash
python validation/compare_energy.py validation/energy-oracle.csv --output /tmp/energy-result.json
```

参照値の生成を含む手順は`validation/reproduce.py`にあります。
