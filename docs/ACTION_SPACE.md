# パラメータ空間とEDごとの学習状態（v0.7）

## EDの独立性

組み込みの自律分散型選択器は、**EDごとに別インスタンス**です。`Simulation._devices()`が各EDに`make()`を呼び出し、`SeedSequence([実験seed, ED ID, コンポーネント番号])`から別の乱数生成器を生成します。同じ`ref`で再利用するのは設定であり、選択器のインスタンスではありません。条件による割り当ても同じ生成経路を通ります。

- `bandit` / ε-greedy: `q`と`n`は各EDのコンストラクタで確保します。EDが2台ならQ表は2つです。
- LoRa-MAB: 各EDにExp3の重みと確率があります。
- LinUCB: 各ED・各行動に逆行列、b、θがあります。
- DQN: ネットワーク、Target、Adam状態、Replay Bufferは各EDが所有します。
- SEMA: **同じED内の**状態別プールで経験を共有する仕様は維持します。別EDのプールとは共有しません。
- ADR/GADRのネットワーク側コントローラもEDごとに生成します。これらは自律分散方式とは異なるネットワーク制御方式です。

観測は対象Deviceから作成し、ACKフィードバックはそのDeviceの`policy.observe()`へ渡します。ED間で伝搬・干渉の影響を受けることと、学習状態を共有することは別です。`policy_state.jsonl`はED ID単位のチェックポイントです。

`tests/test_action_contract.py`で共有ライブラリ参照・割り当て条件の両経路、NumPy配列の非共有、片方だけ学習させた場合の他方の不変性、実行後のED別更新回数とフィードバック記録の一致を検証しています。独自Python実装でクラス変数・モジュール変数に学習状態を置いた場合まで自動的に隔離する仕組みではありません。可変状態は必ず`self`に生成してください。

## 全体集合 → EDの部分集合 → 完全なRadio

```yaml
action_space:
  spreading_factors: [7, 8, 9, 10, 11, 12]
  frequencies_hz: [868100000, 868300000, 868500000]
  coding_rates: [1, 2, 3, 4]
  powers_dbm: [8, 11, 14]
groups:
  - count: 500
    policy:
      kind: bandit
      action_space:
        coding_rates: [1]
        powers_dbm: [14]
      options: {epsilon: 0.1, energy_weight: 0.02}
```

全体は6×3×4×3＝216候補、ED側は6×3×1×1＝18候補です。同じ`bandit`で、ED側を`action_space: {}`にすれば216候補を受け取ります。CRは整数1〜4が4/5〜4/8に対応します。CHはサブバンドのIDではなく、そのサブバンドに登録した**中心周波数の集合**です。

各次元は未指定/nullなら全体集合を継承、配列ならその部分集合です。1値なら固定します。空集合、重複、全体にない値、UL登録外のCH、サブバンド上限を超えるTPはErrorになります。直積のすべての組み合わせが許容される必要があるため、全体TPの最大値はすべての候補CHの上限以下にします。

行動IDの並びはSF→CH→CR→TP（TPが最内側）。`ActionSpace.radios()`がこの順序で完全な`Radio`を返します。候補リストは初期化時に作り、各送信で直積を再計算しません。シミュレータはモデル出力をED側の部分集合でも検証するため、全体では合法でもそのEDに禁止した値を返すことはできません。

GUIでは左の「周波数・行動空間」で全体集合を設定し、「ED・モデルの設定」の選択器で「全体のパラメータ空間を使用」を有効にします。各次元の「全体を継承」を外すと候補をチェックできます。行動数が表示されます。固定モデルは有効集合の先頭を返すので、各次元を1値にすれば意図が明確になります。

## 対応範囲・互換性

| 選択器 | 共通空間 | 学習式・制御の扱い |
|---|---|---|
| fixed / random / bandit | 対応 | 完全なRadio候補を受け取る |
| lora_mab | 対応 | Exp3のACK報酬・重み更新は維持 |
| linucb_sf | 対応 | Context・更新・報酬を維持。複数TP時だけ既存のTP差分SNR補正を適用 |
| dqn / sema | 常に使用 | 入出力サイズを候補数へ合わせる。既存の状態・報酬を維持 |
| ADR / BADR / GADR | 今回は未対応 | 原方式のSF/TP制御、チャネル処理を維持。共通空間を指定するとError |
| linucb_sf_tp | 旧研究拡張のまま | 新規設定ではlinucb_sfの共通空間でTP候補を指定可能 |

論文で固定だった次元を追加すると「論文の完全再現」ではなく比較用拡張になります。CR依存の新しいContext・報酬項を自動追加するものではありません。LinUCB、DQN、SEMAの該当設定はWarningで通知します。LoRa-MABのCR拡張も研究条件に明記してください。

旧プロファイルで`policy.action_space`がない場合、原方式の候補設定は変わりません。従来から共通空間を使うDQN/SEMA/`*_sf_ch`は従来同様に継承します。旧`action_space.tp_dbm`/`cr`はそれぞれ1要素の`powers_dbm`/`coding_rates`へ読み替えます。旧`*_sf_ch`の名前は互換読み込み用に残し、新規GUIの実装候補から外しています。新しいモデル名を作る必要はありません。

共通空間を有効にすると、モデルの`options.actions`や固有のSF/CH/TP候補は使いません。GUIでは該当欄を隠し、YAMLに明示された場合はWarningを出します。`options`には探索率などの学習定数を記述してください。

## 再利用・開発者インターフェース

ライブラリに`kind`、`options`、任意の`action_space`部分集合を保存できます。ライブラリ保存時は部分集合自身の型と初期化を検証し、実験で使う時に全体集合との包含関係を検証します。グループで部分集合を指定するとライブラリの部分集合**全体を置き換えます**。null/未指定なら参照元を継承します。

```python
from lorasim.interfaces import Policy, Options

class MySelector(Policy):
    Options = Options
    accepts_action_space = True
    uses_action_space = True  # この例はコンストラクタで必須とする
    def __init__(self, rng, action_space, **options):
        self.rng = rng
        self.actions = action_space.radios()
        self.q = [0.0] * len(self.actions)  # 必ずインスタンスに置く

    def select(self, observation):
        index = max(range(len(self.q)), key=self.q.__getitem__)
        return self.actions[index].model_copy()

    def observe(self, feedback):
        pass  # 実際にはこのEDのフィードバックだけで更新する
```

`accepts_action_space=True`は任意の注入に対応する宣言です。`uses_action_space=True`は未指定でも共通空間を必要とする宣言です。工場`make()`はEDごとに部分集合を解決し、コピーを渡します。`bound_action_space`と`action_keys`を返されたインスタンスへ付与します。独自モデルの配置・読み込みは[PLUGINS.md](PLUGINS.md)を参照してください。

すぐに実行する例: `lorasim run examples/14-unified-action-space.yaml --output runs/action-space`。この例は動作確認のため異なる選択器を同じRunに混在させています。アルゴリズム比較では別のExperimentへ分けてください。
