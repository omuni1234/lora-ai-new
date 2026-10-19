# v0.9 検証記録

2026-10-19。変更前は `0251bd6`（v0.8）。共通Waypoint移動、D-LoRa選択器、ADR系の一様ランダムCH選択を追加した。伝搬・干渉・感度・Class Aの受信窓・無線電流の理論は変更していない。LMLoRaとHybridQ-ADRは指示に従い追加していない。

## 自動テスト

- macOS arm64 / Python 3.11.4: **241 passed**、11.01秒。[ログ](../validation/tests-v09-native.txt)
- Ubuntu 22.04 / linux/amd64 / Python 3.10（Apple Silicon上のDocker/QEMU）: **241 passed**、68.97秒。[ログ](../validation/tests-v09-ubuntu22.txt)
- JavaScript構文チェック、`docker compose config -q`、`git diff --check` 通過。
- Starlette TestClientのhttpxに関する非推奨警告が1件。シミュレーションの失敗ではない。
- Ubuntuイメージでは全16例のインポート、旧ADR/BADR例の設定検証、examples 09〜17の実行も完了した。検証イメージIDは `sha256:4c7c3ea8369d4c8558e5f20874d6c58e833cef39bcd46ed33fff02c09f86fef0`。[ベースイメージのビルドログ](../validation/build-v09-ubuntu22.txt)

追加テストでは、以下を検証した。

- 共通地点配列は読み取り専用で、グループ・ED間で共有する一方、ED別のRNG・移動状態・学習状態は独立している。
- 初期位置が共通Waypoint上にあり、直線移動中の速度が一定。速度は出発ごと、静止時間は到着ごとに抽選し、同じ地点への再訪でも抽選する。
- 位置照会を細かく行っても途中を飛ばしても、同じ時刻の位置と乱数状態が一致する。1地点、旧時刻指定、設定違反も検証する。
- プレビューと本実験の初期位置・軌跡が一致する。短いモデル名と完全修飾名、ライブラリ参照、ED別の割当を含む。プレビューの遷移処理量には上限がある。
- D-LoRaの式19〜24の報酬・平均更新・基底腕探索を検証する。各次元のUCB最大化と全直積を列挙した最大化が一致する。
- TP・CR固定、共通候補の検証、ACK必須、ED別の独立状態、ADR/BADR/GADRのCH選択とSF・TP制御を検証する。
- 多数のWaypoint遷移・D-LoRa学習・再送を含む同一シナリオを最適化あり／なしで実行し、`devices`、`uplinks`、`transmissions`、`receptions`、`feedback`、`events`、`energy` の全7テーブルと `policy_state.jsonl` が完全一致する。

この等価性は当該シナリオでの回帰検証であり、ns-3全体や論文の実験結果との完全一致を意味しない。

## GUI・実プロセス連携

専用のローカルデータ領域でAPIとworkerを起動し、ブラウザから次を確認した。

1. `16-d-lora-waypoints` を読み込み、共通行動数126、D-LoRaの係数、Waypoint数100と速度・静止時間の上下限を表示する。
2. 移動方式を切り替えると、その方式に対応した入力項目を表示する。速度の最小値を最大値より大きくすると、日本語Errorと修正ヒントを表示する。
3. 設定を修正し、プレビューから配置・軌跡・共通地点を確認して、GUIの実行ボタンでRunを投入する。終了後に完了状態と結果を表示する。
4. `17-adr-random-channels` でも一様CH選択と候補周波数を表示し、GUIから実行して正常終了する。

| 実行例 | ED数 | UL送信数 | いずれかのGWで受信 | ACK受信 | ED平均PRR | 実時間 |
|---|---:|---:|---:|---:|---:|---:|
| D-LoRa＋共通Waypoint | 12 | 240 | 215 | 134 | 0.895833 | 0.1694秒 |
| ADR＋一様CH選択 | 12 | 177 | 149 | 97 | 0.821423 | 0.1034秒 |

D-LoRaでは12台すべてが同じ100地点集合のいずれかから開始し、ED別に12個の学習状態を保存した。各EDの20回の更新が実際に受け取ったフィードバック数に一致した。両例で3CHすべての使用を確認した。ADRの例はシミュレーション終了時に未送信の生成メッセージが1件、未確定の送信試行が1件ある。

両例は設定の異なる動作確認であり、アルゴリズム間の性能比較や大規模性能測定ではない。[機械可読の結果](../validation/ui-v09.json)、[GUI画面](waypoint-dlora-v09.png)。

## 適用範囲

Ubuntuの検証はBuildKit RUNによるテストとCLI実行。通常のComposeサービス起動、対象のUbuntuカーネル5.15実機とNVIDIA GPUでは今回測定していない。GUIはローカル専用領域で検証し、利用者の稼働中サーバーへはデプロイしていない。APIとworkerを両方v0.9へ更新・再起動して利用する。D-LoRaのACK観測・報酬の単位などの再現境界は [PAPERS_V09.md](PAPERS_V09.md)、旧Waypoint設定の扱いは [WAYPOINTS.md](WAYPOINTS.md) に記載した。
