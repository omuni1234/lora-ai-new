# ADR / BADRの定義と範囲

## ADR

LoRaWANはADRの制御メッセージ・端末側動作を定めますが、ネットワーク側の最適化戦略は一意ではありません。本実装は[上流AdrComponent](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/model/adr-component.cc)を基準にしています。

```yaml
policy:
  kind: adr
  options:
    initial: {sf: 12, tp_dbm: 14}
    history_range: 20
    gateway_combine: max
    history_combine: max
    change_tx_power: true
    margin_db: 0
    ack_limit: 64
    ack_delay: 32
    backoff: true
```

サーバーだけがGW受信電力を使用します。複数GW・複数パケットのそれぞれにmax/min/avgを選べます。avgは上流通りdB値の算術平均です。

`SNR = RSSI + 174 - 10log10(125000) - 6`、必要SNRはSF7→12の順に`[-7.5,-10,-12.5,-15,-17.5,-20] dB`。`steps=floor((SNR-required-margin_db)/3)`とし、正のstepはSFを7まで減らし、次にTPを2 dBm刻みで2 dBmまで減らします。負のstepではTPを14 dBmまで上げます。上流の有効なmarginは0 dBであり、本実装の既定も0です。任意marginは研究拡張です。

履歴は成功してサーバーに届いた独立したメッセージです。再送で履歴を水増ししません。20件未満ならサーバーは設定変更しません。下りのLinkADRReqを模擬電波で配送し、受信したEDだけに適用します。要求は5 bytes、次のULの応答は2 bytesとしてToAへ反映します。失敗した下りはEDに見えません。未確認ULでもADR制御下りを利用でき、`ack=None`と`downlink_received=True`を区別します。

端末側のbackoffは[LoRaWAN 1.0.4](https://lora-alliance.org/wp-content/uploads/2021/11/LoRaWAN-Link-Layer-Specification-v1.0.4.pdf)および[Semtechの実装解説](https://learn.semtech.com/mod/book/view.php?chapterid=162&id=174)を参照しています。応答なしの新規ULを数え、64件後にADRACKReq、96件後に最大TP、以降32件ごとに低いDRへ移します。最低DRの次の段階で既定3チャネルと繰返し1回へ戻します。受信DLでカウンタをリセットします。

**適用範囲:** EU868のBW125、SF7–12、TP2–14 dBm。LinkADRReqのSF/TP変更、成功応答のバイト数、ADRACKReq、端末backoffをモデル化します。MACバイト列の完全シリアライズ、複数の連結LinkADRReq、任意ChMask/NbTrans変更、ADRParamSetupReq、全拒否応答のプロトコル認証試験までは実装していません。上流はADRのサーバー計算を持ちますが、今回追加した端末backoffとの全軌跡一致を主張しません。

## BADR（Blind ADR）

[Semtech FAQ](https://www.semtech.com/design-support/faq/faq-lorawan)および[Blind ADR解説](https://www.semtech.com/company/video/technology-in-depth/blind-adr)は、移動端末に異なるSFを混ぜて使用する方式を紹介しています。これはLoRaWAN仕様が一意に定める標準アルゴリズムではありません。実験条件として列・比率を明示してください。

```yaml
policy:
  kind: badr
  options:
    sequence: [12, 7, 10, 7, 10, 7]
    random_phase: true
    tp_dbm: 14
    cr: 1
```

列は送信attemptごとに巡回します。上記はSF7を3回、SF10を2回、SF12を1回使う**本実装の比較用プリセット**です。Semtech仕様で順番まで固定されていると解釈しないでください。random_phaseはEDごとの初期位相を独立乱数でずらします。ACKから学習はしません。時間ごとの使用頻度は`timing`と組み合わせて定義します。例えば間隔600秒で、MAC/Duty Cycleによる延期がなければ6回で1時間です。

## v0.4の論文ベース選択器

`gadr`、`lora_mab`、`linucb_sf`を追加。`linucb_sf_tp`は明示的に分けた研究拡張。原典PDF、式番号、既定値、情報の観測境界、論文の未指定/不整合部分は[PAPERS.md](PAPERS.md)を参照。

## v0.8の共通空間

ADR・BADR・GADRへ`policy.action_space`を指定する比較用拡張を追加。上記の旧モードとの制御則・CH・backoffの差分は[ADR_COMPARISON.md](ADR_COMPARISON.md)を参照してください。
