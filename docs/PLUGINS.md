# モデルの開発とGUIへの設定公開（v0.3）

## 共通構成

```text
lorasim/
  interfaces.py               # Policy / Mobility / Application / NetworkPolicy / Options
  registry.py                 # 実装の発見、メタデータ、Options検証
  plugins/
    selector/                 # 選択モデル: fixed.py, random.py, bandit.py, adr.py, badr.py
    mobility/                 # 移動モデル: stationary.py, random_walk.py, waypoints.py
    application/              # 生成タイミング: periodic.py, poisson.py
examples/
  models/research.yaml         # 再利用する定数の組み合わせ
  exposed_models.py            # 独自モデル3種類の実行可能な例
  07-preflight-sub-bands.yaml  # 自動採番・開始時刻・サブバンドの例
  custom/08-user-exposed.yaml  # exposed_models.pyを使う実験
```

カテゴリ名は既存YAMLとの互換性のため `policy / mobility / timing` を維持します。ディレクトリは `selector / mobility / application` です。`selector`は選択モデルを意味します。

内蔵モデルも独自モデルも、クラス、Options、コンストラクタ、メソッドの同じ契約を使います。内蔵モジュールには `PLUGIN = クラス` を公開し、registryがモジュール名を自動登録します。`kind: adr` と `kind: lorasim.plugins.selector.adr:ADR` は同じ実装に解決します。工場関数にモデルごとの分岐を追加する必要はありません。

独自モデルは好きなPythonパッケージに置けます。APIとworkerの両方に同一コードをインストールし `LORASIM_ALLOW_PLUGINS=1` を指定してください。内蔵フォルダへユーザーコードをコピーする必要はありません。Pythonのimport自体にコード実行が伴うので、配置したコードは管理者が信頼している前提です。GUIはPythonコードをアップロード・インストールする機能を持ちません。

## 最小の選択モデル

```python
from pydantic import Field
from lorasim.interfaces import Policy, Options
from lorasim.config import Radio

class Settings(Options):
    sf: int = Field(9, ge=7, le=12,
                    title="拡散率", description="毎回の送信に使うSFです。")
    tp_dbm: float = Field(14, ge=2, le=14,
                          title="送信電力 [dBm]", description="RF出力電力です。")

class MySelector(Policy):
    Options = Settings
    label = "研究用固定モデル"
    description = "定数の公開方法を示す最小の例。"
    preview_safe = True

    def __init__(self, rng, sf=9, tp_dbm=14):
        self.radio = Radio(sf=sf, tp_dbm=tp_dbm)

    def select(self, observation):
        return self.radio.model_copy()
    # observe(feedback) は基底クラスの何もしない実装を使うことも可能。
```

GUIで「独自Pythonクラス」を選び `my_package:MySelector` を入力し、「設定項目を読み込む」を押すとフォームを生成します。ライブラリに保存すれば複数のEDグループで参照できます。

`Options`はPydanticモデルです。数値・文字列・bool・Literalの選択肢・配列・固定長tuple・入れ子のOptions・nullableをGUIで編集できます。各項目に `Field(title=日本語名, description=意味と単位)` を必ず記載し、数値範囲を宣言してください。条件間の制約は `@model_validator` で実装します。未宣言の定数や非有限数値は拒否します。高度な再帰型や複雑なUnionはGUI対象外とし、YAMLと独自検証を使用してください。

既定値はOptionsが唯一の管理場所です。registryはOptionsで検証・補完した値を `model_dump()` してコンストラクタへ渡すため、入れ子モデルはdict、Radioの候補列はdictのlistになります。コンストラクタを直接呼ぶ既存コードとの互換性のため、同じ引数名と既定値も維持してください。新しい定数を公開するのにフロントエンドの変更は不要です。

旧形式の独自クラスも必要なメソッドがあれば読み込めますが、Optionsがない場合は汎用辞書の編集になります。新規開発は抽象クラスを継承してください。

## インターフェース

| 種類 | 基底クラス | コンストラクタのコンテキスト | 必須メソッド |
|---|---|---|---|
| 選択 | `Policy` | `rng` | `select(Observation) -> Radio` |
| 移動 | `Mobility` | `rng, initial, area` | `position(time_s) -> [x,y,z]` |
| 生成 | `Application` | `rng` | `next_time(Observation, previous_generation_s) -> float or None` |

`observe(Feedback)`は学習用です。EDで観測可能なACK成否・DL RSSI等だけが入力され、GW側RSSIや衝突原因は入りません。詳細は[DEVELOPER.md](DEVELOPER.md)。MCU申告時間は `inference_time_s` と `training_time_s` です。

乱数は渡されたNumPy Generatorだけを使ってください。グローバル乱数・壁時計を判断に使うと再現性を失います。各ED、各カテゴリは独立したインスタンスと乱数列を持ちます。

生成モデルは最初の `previous_generation_s=None` で初回時刻を返します。その後は有限の未来時刻、停止するならNoneを返します。前回生成時刻とMAC処理が終わった時刻は異なります。`observation.time_s` より過去に生成を戻せません。移動モデルの問い合わせはシミュレーション時刻順で、3つの有限座標を返してください。

ADRは `NetworkPolicy` を継承します。`create_server()` がEDごとの制御器を返し、その `request(message_id, radio, powers)` がRadio形式のSF/TP変更またはNoneを返します。ED側には実際にDLが届いた後だけ `downlink(command)` を通知します。`select_attempt`, `sent`, `answer_pending`, `ack_requested`, `reset_repetitions` はLinkADRReq互換の通信状態用です。汎用的な任意MACコマンドを追加するAPIではありません。`plugins/selector/adr.py` が完全な参考実装です。

## 送信開始時刻

```yaml
groups:
  - count: 100
    timing:
      kind: periodic
      options:
        interval_s: 120
        start: {mode: uniform, min_s: 0, max_s: 90}
        jitter_s: 0
```

初回は各EDで一様分布 `[0,90)`、以後は120秒間隔です。`start: {mode: fixed, at_s: 10}` なら10秒に開始します。上下限が等しい一様分布はその定数です。`start`を指定すると旧形式の`first_s`と`random_phase`より優先します。`start: null`または省略時は旧動作を維持します。モデル定義を `ref` で共有し、各グループの `options` で間隔・開始時刻を上書きできます。上書き単位は最上位キーです。

## 実験前プレビュー

`preview_safe = True` は、副作用・学習・ネットワークアクセスを伴わず、有限回の呼び出しで確認できるという開発者の宣言です。未宣言モデルの呼び出しを省略し、該当EDに理由を表示します。独自のコンストラクタとメソッドの計算量は開発者が制御してください。

必要なら `preview_cost = staticmethod(lambda options, horizon_s: ...)` を移動モデルに設け、内部反復数の上限を返せます。RandomWalkはこれで計算上限を判断します。上限を超えた軌跡は初期位置に代替し、警告を表示します。モデル本体の設定や乱数状態は変更しません。

プレビューは無線・MACのシミュレーションではありません。最大200 ED × 30生成予定、移動座標も有限個のサンプルです。ACKや学習結果を作らず `select` の未学習・初期状態の動作を表示します。ADRサーバ制御も動かしません。選択結果から将来の学習後性能を推測しないでください。

## 自動採番とサブバンド

GWの`id`、EDグループの`name`、サブバンドの`id`は省略・空欄で `gw0`, `group0`, `band0` 等に自動採番します。明示した識別子を先に予約し、欠番の最小値から割り当てます。生成後の名前は検証応答・GUI追加・実行スナップショットに反映します。既存の識別子は変更せず、同じ明示名の重複はエラーです。ED IDは従来通りグループ順で0から自動採番します。

`sub_bands[].channels_hz`に中心周波数を登録すると、選択モデル・GW・RX2のフォームで候補になります。`ul_sub_bands`と`dl_sub_bands`は許可するサブバンドIDの一覧です。null/省略は全て許可、空配列は入力ミスとして拒否します。RX2はDL許可帯域にある必要があります。ULが許可外の周波数を返した場合は実行をエラー停止し、RX1がDL許可外の場合はそのDLを送信せずRX2へ回します（`RX1SubBandDisabled`イベント）。受信ウィンドウの無線状態・電力計算は維持します。

登録チャネル一覧はGUIの候補であり、PHYの周波数を離散化する制約ではありません。YAML・独自モデルから指定した周波数も許可サブバンドの範囲内なら使えます。旧プロファイルで候補未登録の値はGUIに保持し、候補未登録と表示します。必要な値は「サブバンドとチャネル」へ登録してください。これはEU868を対象にした本シミュレータの実験上のチャネル制限であり、他地域のLoRaWAN規則を自動実装する機能ではありません。

## 実行例

```bash
lorasim run examples/07-preflight-sub-bands.yaml --output runs/preflight-example
# リポジトリのルートで実行
LORASIM_ALLOW_PLUGINS=1 PYTHONPATH=examples lorasim run examples/custom/08-user-exposed.yaml --output runs/user-example
LORASIM_ALLOW_PLUGINS=1 PYTHONPATH=examples lorasim serve --host 127.0.0.1 --data data
# 別端末。GUIと同じプラグインコードと設定を渡す
LORASIM_ALLOW_PLUGINS=1 PYTHONPATH=examples lorasim worker --data data
pytest -q
```

CPU/GPU用Dockerfileは `lorasim/plugins` をパッケージとして含みます。ユーザーコードはイメージに追加インストールするか、API/workerの同じパスに読み取り専用マウントし、両方のPYTHONPATHと有効化設定を揃えてください。内蔵実装のSHA256はmanifestへ相対パス付きで記録します。外部モデルのコードと学習重みは独立にバージョン・ハッシュを管理してください。
