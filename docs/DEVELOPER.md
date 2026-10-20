# 移動モデル・選択アルゴリズム開発者マニュアル

v0.3以降の新規開発は **[プラグイン開発ガイド](PLUGINS.md)** の抽象クラス・OptionsによるGUI公開を推奨します。以下の従来APIも読み込み可能です。

## 責務とディレクトリ

- `examples/*.yaml`: 実験条件（ED/GW数、時間、配置、割り当て）。
- `examples/models/*.yaml`: 再利用する名前付き定義。実装を`kind`、引数を`options`で指定。
- `lorasim/plugins/selector/*.py`: 選択アルゴリズム。`adr.py`にはEDとネットワークの制御実装。
- `lorasim/plugins/mobility/*.py`: 移動モデル。`plugins/application/*.py`: 生成タイミング。
- `policies.py`, `algorithms.py`, `mobility.py`, `timing.py`は旧import互換用で、実装を保持しません。
- `lorasim/interfaces.py`: EDに公開される入力。`components.py`: ファクトリと旧importとの互換窓口。
- `config.py`: 宣言的設定の検証・参照解決。`engine.py`: 時刻・無線状態・配送を管理。

モデルはプロファイルを直接読みません。コンストラクタへ設定と固有の乱数生成器を渡します。各EDに別インスタンスが作られます。学習済み重み等の不変データだけプロセス内で共有して構いません。

## 定義と参照

外部ファイル`models/research.yaml`:

```yaml
policy:
  baseline: {kind: fixed, options: {sf: 9, tp_dbm: 12}}
mobility:
  walker: {kind: random_walk, options: {speed_m_s: 1.5, step_s: 10}}
```

プロファイル:

```yaml
model_files: [models/research.yaml]
groups:
  - name: walkers
    count: 50
    policy: {ref: baseline, options: {tp_dbm: 14}}
    mobility: {ref: walker}
assignments:
  - ids: [0, 1]
    policy: {kind: badr, options: {sequence: [12, 7, 10, 7, 10, 7]}}
```

`options`は**最上位キー単位**で上書きします。`actions`等の配列は全体を置換します。カテゴリを跨ぐrefは不可。循環参照・未定義参照は実行前にエラーになります。割り当てはgroups/ids/tagsの条件間がAND、同じリスト内のgroup/IDはOR、tagsは包含条件です。複数一致では後の割り当てが勝ちます。

CLIでは`model_files`をプロファイルファイルのディレクトリに対して解決します。Webにはサーバーのファイルパスを送らず、「モデルライブラリ」へ定義を登録してrefで参照します。Webの保存済み定義→外部ファイル→プロファイル内`models`の順に優先度が上がります。投入時には定義を全て埋め込み、`model_files: []`にしたスナップショットを保存します。ライブラリを編集しても投入済みRunは変わりません。保存プロファイルに同名の埋め込み定義がある場合は、その定義がライブラリに優先します。

Python実装を変更した場合、既存Runのモデルコードまで自動凍結するものではありません。独自モデルのGit commit、学習重みのSHA256と配布環境を研究記録に追加してください。内蔵実装のSHA256はmanifestに記録されます。

## 選択アルゴリズム

```python
from lorasim.config import Radio
from lorasim.interfaces import Observation, Feedback

class MyPolicy:
    def __init__(self, rng, initial_sf=9):
        self.rng = rng
        self.sf = initial_sf
        self.inference_time_s = 0.0004
        self.training_time_s = 0.002

    def select(self, observation: Observation) -> Radio:
        return Radio(sf=self.sf, tp_dbm=14, cr=1)

    def observe(self, feedback: Feedback) -> None:
        if feedback.ack is not None:
            self.sf = max(7, self.sf-1) if feedback.ack else min(12, self.sf+1)
```

配置例: `plugins/my_policy.py`。`PYTHONPATH`に`plugins`を加え、API・worker双方で`LORASIM_ALLOW_PLUGINS=1`にします。

```yaml
policy:
  kind: my_policy:MyPolicy
  options: {initial_sf: 10}
```

コンストラクタは入力検証でも呼ばれます。ネットワークアクセス、学習開始、ファイル書き換えをコンストラクタで行わないでください。重いモデルは最初の`select`でロードし、重み共有は`examples/torch_policy.py`を参考にしてください。

`Observation`:

| 属性 | 意味 |
|---|---|
| time_s | シミュレーション時刻。ホスト時刻ではない |
| device_id / tx_count | ED ID / このEDの実送信回数（再送を含む） |
| last_ack | 確認送信のACK成功/失敗。初回・未確認送信はNone |
| last_dl_rssi_dbm | 受信できた自分宛てDLのRSSI。未受信はNone |
| last_sf / last_tp_dbm | 直前の実送信設定。初回None |
| position | observe_position=true時だけ自己位置tuple、それ以外None |

`Feedback`は`time_s, ack, dl_rssi_dbm, airtime_s, tx_power_dbm, attempt, downlink_received`です。`downlink_received=True`にはACKのないADR下りも含みます。GWのRSSI、UL受信成否、実際の損失要因、他EDの位置は学習入力へ渡しません。中央サーバーのADR判断は別オブジェクトが行い、無線下りが成功した時のみED設定を更新します。

`select`は送信attemptにつき一回。Duty Cycle延期中は選択を保持します。`observe`はattemptの受信窓処理終了時に一回。終了時刻で切られた未完了attemptには呼ばれません。観測時刻と実送信時刻の差に注意してください。

## MCU計算時間

`energy.mcu`を全体設定、`groups[].mcu`をグループ単位の上書きに使います。

- `fixed`: `inference_s` / `training_s`を毎回使用。再現実験向け。
- `declared`: 呼び出し直後に方策の`inference_time_s` / `training_time_s`を読む。対象MCUで計測した値や演算数モデルを設定できる。属性欠落はエラー。
- `measured`: 実際のPython呼び出しを`perf_counter`で測定。ホストの値であり、対象MCUの時間とは同じではありません。GPU処理はプラグイン内で同期してから戻る必要があります。

計算時間は電力積算用です。MACイベントを遅らせるMCUタスクスケジューラではありません。無線状態とは独立に、idleベース電力＋active増分を計上します。詳細は[ENERGY.md](ENERGY.md)。

## 移動モデル

```python
import numpy as np

class Linear:
    def __init__(self, initial, area, rng, velocity=(1., 0., 0.)):
        self.initial = np.asarray(initial, dtype=float)
        self.velocity = np.asarray(velocity, dtype=float)

    def position(self, time_s):
        return self.initial + self.velocity * time_s
```

`position(t)`は3つの有限座標[m]を返します。同時刻に繰り返し呼んでも同じ位置にしてください。呼び出しはシミュレーション内で非減少時刻です。`initial`は配置モデルが決めた初期位置、`area`は幅・高さ、`rng`はそのEDの移動専用乱数です。移動が通信頻度で変わらないよう、乱数方向の更新は呼び出し回数ではなくシミュレーション時刻で決めます。

内蔵RandomWalkは定速・一定間隔の方向変更・反射境界です。Waypointsは`[time_s,x,y,z]`列を線形補間し、最終点以降は停止します。独自Pythonの移動再現をGUIリクエストから実行しません。その場合、GUIは送信位置の軌跡と初期配置を表示します。内蔵移動は独立RNGで要求時刻の位置を再構成します。

## タイミングモデル

`__init__(rng, **options)`、`next_time(observation, previous_generation_s)`を実装します。返り値は次の絶対時刻[s]、または送信停止を表すNoneです。初回は現在以降、2回目以降は現在より未来を返します。EDごとに未完了メッセージは一つです。

## テストと再現性

```bash
pip install -e '.[test]'
pytest -q
lorasim validate examples/06-badr-mobility.yaml
lorasim run examples/06-badr-mobility.yaml --output runs/badr
```

最低限、同一seedの再現、複数ED間の状態独立、合法なRadio返却、欠損ACK/RSSI、長いDuty Cycle延期、時刻の重複問い合わせを検証してください。独自モデルのGPU推論はCUDA/cuDNN/重み版も記録してください。モデルはOS、ディスク、ネットワークにアクセスできる信頼済みPythonコードです。プラグインを有効にした管理APIはアクセス可能な利用者を制限して運用してください。

## v0.12の更新

分析はlorasim/analysis_modulesの独立クラス、共通描画はstatic/analysis-charts.jsです。新指標の追加はANALYSIS_PLUGINS.mdを参照してください。GUI文書はdocuments.pyがREADME.mdとdocs/*.mdを読み、MarkdownIt（HTML無効）で表示します。wheelにはshare/lorasim/docs、Dockerには/app/docsとして同梱します。編集元はdocs/*.mdだけで、生成コピーを直接編集しません。

[操作ガイド](GUI.md) / [文書一覧](INDEX.md)

## ハイパーパラメータ探索（v0.13）

`lorasim/tuning.py` が通常のジョブ基盤の上で候補評価を管理します。モデル実装に専用の学習ループを追加する必要はありません。Optionsから公開される定数を指定し、最終結果は通常のライブラリComponentに変換します。[設計・API・指標](TUNING.md)を参照してください。
