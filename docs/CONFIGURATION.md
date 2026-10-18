# 設定・方策・移動拡張

`examples/*.yaml`はそのまま実行できます。時刻は秒、座標はメートルの3次元直交座標、周波数は整数Hz、電力はdBmです。`schema_version: 1`を使用します。未知のトップレベル設定キーを黙って無視せずエラーにします。完全なJSON Schemaは管理APIの`GET /api/schema`で確認できます。

## プロファイル

```yaml
name: my-research
seed: 123
duration_s: 3600
area_m: [3000, 3000]
channel:
  exponent: 3.76
  reference_distance_m: 1
  reference_loss_db: 7.7
  collision_matrix: goursaud    # または aloha
mac:
  duty_cycle: true
  rx1_delay_s: 1
  rx2_delay_s: 2
  rx_symbols: 8
  rx1_sf_offset: 0
  rx2_sf: 12
  rx2_frequency_hz: 869525000
  backhaul_delay_s: 0
gateways:
  - id: gw0
    position: [1500, 1500, 15]
    demodulators: 8
    frequencies_hz: [868100000, 868300000, 868500000]
groups:
  - name: learners
    count: 100
    tags: [mobile, experiment]
    placement: uniform          # grid / explicit も選択可
    payload_bytes: 20
    confirmed: true
    max_attempts: 3
    observe_position: false
    policy:
      kind: bandit
      options:
        epsilon: 0.1
        energy_weight: 0.02
        actions:
          - {sf: 7, tp_dbm: 8, cr: 1, frequency_hz: 868100000}
          - {sf: 9, tp_dbm: 14, cr: 2, frequency_hz: 868300000}
          - {sf: 12, tp_dbm: 14, cr: 1, frequency_hz: 868500000}
    timing:
      kind: periodic
      options: {interval_s: 60, random_phase: true, jitter_s: 1}
    mobility:
      kind: random_walk
      options: {speed_m_s: 1.5, step_s: 10}
assignments:
  - ids: [0, 1]
    policy: {kind: fixed, options: {sf: 9, tp_dbm: 14}}
  - groups: [learners]
    tags: [mobile]
    ids: [2]
    mobility:
      kind: waypoints
      options:
        points: [[0, 0, 0, 1.5], [1800, 2500, 2500, 1.5], [3600, 0, 0, 1.5]]
tracking:
  batch_rows: 4096
  max_air_signals: 100000
  max_air_bytes: 536870912
```

ED IDはgroups記載順に0から連番。`assignments`は記載された条件同士がAND、同じフィールド内のgroups/idsはOR、tagsはすべて含む条件です。空条件は全ED。複数一致では後の設定が該当コンポーネントだけを上書きします。方策、移動、タイミングのインスタンスとRNGはEDごとに独立します。

`explicit`は`count`件の`positions: [[x,y,z], ...]`を要求します。uniform/gridのzは1.5m。areaは初期配置とRandom Walkの境界に使います。Waypointsや独自移動はエリア外も指定可能です。

`fixed`はRadioの全項目をoptionsに渡せます。`random`はactionsから一様抽出。`bandit`はepsilon-greedyのサンプル平均更新で、報酬は `ACK - energy_weight * airtime_s * 10^(tp_dbm/10)`。後半は送信RFエネルギーに比例するペナルティであり、回路電力を含む電池消費モデルではありません。ACKのあるconfirmed交通向けです。未確認では報酬更新しません。最終Q値と選択回数を`policy_state.jsonl`へ保存します。

SF7–12、CR1–4、BW125000に対応。SFに応じたEU payload上限とsub-bandの電力上限を送信前にチェックします。上限を超えた方策出力は実験をfailedとし、勝手にクリップしません。固定方策で周波数を省略すると868.1 MHzを使い続けます。上流のランダムなチャネル選択と同じにしたい場合はaction集合や独自方策で選択してください。

`periodic`はfirst_s（既定0）、random_phase、interval_s、jitter_sを指定できます。間隔は生成開始同士の時間で、MACが忙しければ後ろへ延期します。`poisson`はoptions.mean_s。どちらも次の一件だけを予約し、全送信予定をRAMに展開しません。

## Python拡張

`kind: モジュール:クラス`で指定します。APIとワーカーの両方で同じ拡張コードをimport可能にし、`LORASIM_ALLOW_PLUGINS=1`を設定します。拡張は通常のPythonコードとして実行されるため、利用者自身が管理するコードを対象にしています。

```python
from lorasim.config import Radio

class MyPolicy:
    def __init__(self, rng, **options):
        self.rng = rng
        self.sf = 7

    def select(self, observation):
        # observationは凍結dataclass。GWの真の状態は含まない。
        return Radio(sf=self.sf, tp_dbm=14, cr=1)

    def observe(self, feedback):
        if feedback.ack is False:
            self.sf = min(12, self.sf + 1)
```

`Observation`: time_s, device_id, tx_count, last_ack, last_dl_rssi_dbm, last_sf, last_tp_dbm, position。
`Feedback`: time_s, ack, dl_rssi_dbm, airtime_s, tx_power_dbm, attempt。
API上は観測情報を分離していますが、任意Pythonコードをサンドボックス化しているわけではありません。公平な分散学習実験では、プラグインが他のログや大域情報を参照しないよう研究者側で管理してください。

タイミング拡張は`__init__(rng, **options)`と`next_time(observation, previous_generation_s)`を実装し、次の絶対時刻（秒）または`None`を返します。初回previous_generation_s=None。それ以降は現在より将来の時刻が必須です。移動拡張は`__init__(initial, area, rng, **options)`と`position(time_s)`を実装し、有限な[x,y,z]を返します。問い合わせは時刻の非減少順です。モデル内部に全履歴を溜めない実装にしてください。

`examples/custom_models.py`に方策・時刻列・直線移動の例があります。

```bash
LORASIM_ALLOW_PLUGINS=1 PYTHONPATH=examples lorasim run your-profile.yaml --output runs/custom
```

## GPU

因果的なイベントカーネルはCPU上で実行します。NumPyによる伝搬ベクトル計算と独立実験プロセスが基本の高速化です。CUDAは大きい学習・推論モデルに適用できますが、EDごとの小さな推論では転送や同期のコストが勝る可能性があります。

`examples/torch_policy.py`はローカル観測4要素をTorchScriptモデルへ渡す例です。GPUモデルはワーカー内で遅延ロードされ、同じモデルパスはそのプロセス内で共有します。出力は(1, action数)のlogits。GPU推論結果やGPU学習はCPUとビット単位一致するとは限りません。バッチ学習を追加する場合も、未来時刻の観測を使わないことが必要です。

NVIDIA Container Toolkitをホストへ導入した上で、モデルを`models/policy.pt`へ配置し、次のように起動します。

```bash
mkdir -p models
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build
```

ワーカーだけCUDA/cuDNNイメージを使用します。プロファイル例:

```yaml
policy:
  kind: torch_policy:TorchPolicy
  options:
    model_path: /models/policy.pt
    device: cuda
    actions: [{sf: 7}, {sf: 9}, {sf: 12}]
```

CPU版APIにも同じ`examples`ディレクトリを`/app/plugins`へread-onlyマウントし、`PYTHONPATH=/app/plugins`と`LORASIM_ALLOW_PLUGINS=1`を設定する必要があります（`compose.gpu.yaml`はこの設定を含みます）。GPUモデル自体のロードはAPIでは行いません。CUDA環境での実行検証は未実施です。


## v0.2の再利用・比較・電力

外部定義`model_files`、埋め込み`models`、`policy/mobility/timing.ref`を使用できます。[DEVELOPER.md](DEVELOPER.md)に定義の優先順位と不変スナップショットを記載しています。外部ファイル参照はCLIで解決し、Webではライブラリを使います。

`energy.radio`は全EDの無線モデル、`energy.mcu`はMCU既定値、`groups[].mcu`はグループ上書きです。設定一覧・単位はGUIの全項目フォームと`GET /api/schema`、計算式は[ENERGY.md](ENERGY.md)。`adr`/`badr`のoptionsは[ALGORITHMS.md](ALGORITHMS.md)。

Webでは配列の追加/削除、条件割当、座標、電流、Webhook、全Radio引数までフォームから変更できます。長い配列は50件ずつ表示します。任意プラグインのoptionsはキー名・型・値を追加できます。未登録のキーや不正な型はサーバーで検証されます。

## v0.3のGUI定数・サブバンド

`Options`による型・既定値・日本語説明の公開、グループごとの初回生成分布、UL/DLサブバンド、ID自動採番の書式は [PLUGINS.md](PLUGINS.md) を参照してください。従来のperiodic.first_s/random_phaseはstart未指定時に同じ意味で動作します。GUIの周波数候補はsub_bands[].channels_hzから生成します。
