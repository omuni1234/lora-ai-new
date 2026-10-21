# SEMA / TVT性能評価の実験セット

`python -m lorasim.experiments` は、手法別の独立ネットワークを同じseed群で比較するための設定生成・実行・集計コマンドです。SEMAの優位性を事前に仮定せず、PRR、端末間公平性、状態変化への応答、計算コストを同じ実験から評価します。

## 実行

```bash
python -m pip install -e '.[test]'
# NumPyを起動する前に設定。推論時間の比較時は同一ホストで逐次実行する。
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1

# 設定・実行・集計の動作確認（4 ED、12送信/ED、58 runs）
python -m lorasim.experiments plan --output results/tvt-smoke --suite all --seeds 0 1 --smoke
python -m lorasim.experiments run results/tvt-smoke
python -m lorasim.experiments analyze results/tvt-smoke

# 本実験（重複条件を除いた360 runs。各ED 40,000送信）
python -m lorasim.experiments plan --output results/tvt --suite all --seeds 0 1 2 3 4
python -m lorasim.experiments run results/tvt
python -m lorasim.experiments analyze results/tvt
```

`--suite basic` は基本比較、`--suite additional` は追加比較だけを生成します。`--seeds` で反復数を増やせます。初期5 seedsは実行開始用の既定値であり、統計的な検出力を保証する数ではありません。

短縮版は学習性能の証拠には使用しません。既定のDQNバッチ128に対して12送信しかないため、勾配更新前の経路確認です。学習更新そのものは自動テストで別途検証しています。本実験は長時間・大量のParquet保存を伴います。まず `run ... --limit 1` で1 runの実行時間と保存容量を測り、残りの計算資源を見積もってください。

### 再開・分割

- 同じ `run` コマンドは完了済みrunをスキップします。設定ハッシュと実装ソースハッシュの不一致は拒否します。
- `--only=-sema-` などでrun名を絞り込み、`--limit 1` で今回実行する件数を制限できます。
- 未完了runは自動上書きしません。該当 `runs/<id>` を別の場所へ退避してから再実行してください。
- `--shards 4 --shard-index 0`（index 0～3）で独立プロセスへ分割できます。同一shardや同一runを重複起動しないでください。**計算時間を比較する本実験には、同一ホスト・同一スレッド設定で逐次実行したデータを使います。**
- 集計は既定で全runの完了を要求します。`analyze ... --allow-partial` は途中確認用で、欠落run名とpartialフラグを保存します。
- `profiles/*.yaml` は展開済み設定、`plan.json` は条件一覧と解析規約です。プロファイルを変更するなら別の計画を作成し、対応するhashを持つ新しい実験として扱ってください。既存結果への設定差し替えは禁止です。
- GUI/workerで実行したい場合は生成したYAMLを既存の `lorasim submit` に渡せます。ただし本コマンドの集計は `runs/<id>` と専用完了マーカーを使うため、GUIの結果は既存GUI解析で扱ってください。

## 条件と比較の狙い

| 実験 | 条件 | 比較手法 | 論文で検証する点 |
|---|---|---|---|
| 基本 | 50/100/150/200 ED、reference | SEMA、1入力DQN、ε-greedy、UCB1、既存LoRa-MAB、既存LinUCB、random | 負荷に対する平均PRR・ACK成功率・公平性 |
| 状態情報とモデル分割 | 100/150 ED、全4シナリオ、64×64 | SEMA、1入力DQN、2入力DQN、固定B/C | 移動情報を入力する効果、状態別モデルを持つ効果とメタ選択の差 |
| 小型化 | 100/150 ED、reference、16×16/32×32/64×64 | 上記4手法 | 容量変更下でのPRR・公平性と推論/学習CPU時間の関係 |
| 遷移後の損失 | 上記runの移動開始・停止エピソード | 各手法 | 変化後最初の10/50/100送信における受信損失 |

全体設定：24 km四方、GWは(6,6)/(6,18)/(18,6)/(18,18) km・高さ15 m・各8復調器、共通400地点、速度Uniform(1,5) m/s、初期地点で静止。周期60秒・初期位相Uniform(0,60)秒、application payload 40 bytes、confirmed、再送なし。SF7–12 × 3 UL CH、14 dBm、CR4/5。Duty cycleは無効化し、送信間隔60秒の負荷比較を定義します。PHY/ACK/Class A処理は既存実装のままです。電波伝搬など省略された設定も展開YAMLに保存されます。

- reference：停止時間Uniform(3000,18000)秒。
- stationary_dominant：Uniform(18000,36000)秒。
- moving_dominant：Uniform(0,600)秒。
- mixed：EDの半数ずつに上記2条件を割り当て。同じ移動方式・地点集合・速度を使用。

「静止優勢／移動優勢」はシナリオ名です。実際の送信時の静止／移動割合も集計し、名称だけで割合を決めつけません。手法間でグループ順・ED番号・seed・位置観測タイミングを揃え、位置・移動・トラフィックは方策用と独立したRNG系列を使用します。異なるED密度間の同一軌跡や、異なるシナリオ間の完全な共通乱数を仮定しません。

## 比較モデルの定義

- `dqn`：従来どおりACK受信電力1入力。同じDQN設定・SF報酬を使用。
- `dqn_mobility`：ACK受信電力＋移動ビットの単一DQN。移動ビットはSEMAと同じ「前回選択位置との差が1 m超」で検出。シミュレータの真の移動状態は入力しません。ACK処理時の次状態は更新したRSSIと最後に検出した移動ビットです。次回選択時の未来の位置を経験再生へ混入させません。
- `dqn_state_split`：静止時はB、移動時はCを必ず選択。ACK時に選択モデルだけを更新。Aの共有モデル、ε-greedy、メタ選択はありません。SEMAの複数の設計要素との差をまとめて測る対照であり、この比較だけから個々の要素の因果効果を分離したとは主張しません。
- `sf_reward_bandit` / epsilon：成功報酬(13−SF)/6、失敗−1。累積報酬を活用し、ε=max(0.1,20/(20+t))。SEMA内のEと違い、単独対照では移動状態によるリセットを行いません。
- `sf_reward_bandit` / ucb1：同じ報酬を(r+1)/2で[0,1]に正規化し、標本平均＋sqrt(2 log(t)/n)で選択。各行動を1回ずつ試行後にUCBへ移行します。
- 既存 `lora_mab_sf_ch` はACK 0/1報酬の実装のままです。修論で記載された報酬や重み更新と同一だとは扱いません。`linucb_sf_ch` も既存実装の観測・報酬を維持します。

このセットは現在のPythonシミュレータにおける再評価です。修論の旧Python環境やns-3全体をそのまま再現する実験ではありません。モデル対応は [FIDELITY.md](FIDELITY.md)、SEMAの式と未指定パラメータの実装選択は [SEMA.md](SEMA.md) に従います。

## 出力と解析規約

| report内のファイル | 内容 |
|---|---|
| runs.csv | seedごとのPRR、ACK成功率、Jain、下位10%、しきい値達成率、状態別PRR・割合、計算時間、遷移後損失 |
| per_ed.csv | EDごとの最終窓PRR。経験CDF用の全EDデータ |
| transitions.csv | run/ED/実際の状態開始時刻/Hごとの損失と完全窓フラグ |
| aggregate.csv | 条件別に独立seedを反復とした平均・95% bootstrap区間 |
| paired_sema_minus_baseline.csv | 同じ条件・同じseedのSEMA−比較手法差と区間。正が有利なのはPRR等、時間と損失は負が有利 |
| analysis.json | 窓定義、欠落run、短縮版フラグ、統計手順、実装hash |

- PRRは「少なくとも1 GWでULを受信した割合」。学習に使用するACK成功率とは別に保存します。
- 最後の2,000実送信/EDを性能窓とします。全EDの40,000送信完了を検証し、窓内の打切りや欠落ACKを含むrunは集計拒否します。生成回数を明示的に制限し、終了まで30秒の余裕を与えます。
- JainはED別PRRで計算。全EDのPRRが0なら未定義として空欄。下位10%はPRR順でceil(0.1 N)台の平均。しきい値達成率はPRR≥0.7のED比率です。
- 95%区間はseed単位のpercentile bootstrap（10,000反復、解析seed固定）。EDやパケットを独立な実験反復として水増ししません。1 seedでは区間は空欄。対応差は共通seedのみで計算し、組数を表示します。多重比較補正は行っていないため、個々の区間を全比較一括の有意性と解釈しないでください。
- 遷移損失は実際の物理的移動開始／停止から、その状態が継続している間の最初のH送信です。初期状態は除外し、最初の送信番号が2,000以下のエピソードも除外。H送信より短い状態区間や終了時の不足窓は不完全として別計数し、完全窓の平均には混ぜません。送信が一度もないエピソードは観測対象外です。窓の完全率を併記し、これを収束時間と呼びません。
- `motion_epoch_start_ns` は診断ログだけに追加し、ED方策の観測へ渡しません。現在はrandom_waypointで定義し、他の移動方式ではnullです。
- CPU計測は `process_time_ns` によるホスト上の処理時間で、無線シミュレーションの時間を進めません。`inference` は行動選択全体（SEMAのメタ選択含む）、`training` はACKフィードバック処理全体、`meta_update` はSEMAのメタ報酬更新・逐次消去部分です。`decision_plus_meta` は選択とメタ更新の平均の和です。学習時間を推論時間に隠さず併記します。平均と95パーセンタイルは同じ最終窓から算出します。
- CPU計測をMCU実測遅延・実機消費電力と同一視しません。CPU競合、BLASスレッド数、実行環境を揃えます。各runは依存パッケージ・OS・ソースhashとスレッド環境変数を記録します。
- 大きな結果の並べ替えは一時SQLiteで行います。一時ディスク（`TMPDIR`で指定可能）にUL結果の作業領域が必要です。エピソードCSVはrunごとに追記し、全runのイベントをメモリへ保持しません。

既存のrun解析には時間／送信番号ごとの曲線、距離別結果、SEMAのarm消去履歴もあります。論文の章は「基本性能と公平性 → 状態情報・固定モデル対照 → シナリオ依存性と遷移損失 → 小型化と計算コスト」の順で構成でき、各結論を対応する実測結果へ結び付けられます。
