# v0.8 検証記録

2026-10-18、比較対象の変更前コミット`396b488`（v0.7）。PHY/MAC計算は変更せず、共通行動空間のADR制御とRunの起動管理を追加した。

## 自動テスト

- macOS arm64 / Python 3.11.4: **226 passed**, 9.64秒。[ログ](../validation/tests-v08-native.txt)
- Ubuntu 22.04 / linux/amd64 / Python 3.10（Docker Desktop、Apple Silicon上のQEMU）: **226 passed**, 61.99秒。[ログ](../validation/tests-v08-ubuntu22.txt)
- いずれもStarlette TestClientの依存関係に関する非推奨警告が1件。テスト失敗はない。
- 全例のインポート、旧ADR/BADR例の検証、examples 09〜15の実行をUbuntuコンテナで完了。検証イメージID: `sha256:886fd2035ad704fce716af0b88f1f8dd5bcb1312718d75207b9cce79bf60d842`。
- JavaScript構文、`docker compose config -q`、`git diff --check`通過。

追加ケースは、3選択器の部分集合制約、TP11 dBm/CR4/6固定、ADRのSF step予算、BADRの巡回列写像、backoff、再送のCH、成功DL後のみの更新、ED別NS履歴、ライブラリ保存、即時診断の日本語Error、設定APIの認証・無効値拒否。

ワーカーは実際のspawn子プロセスをバリアで制御し、1→2→1の動的変更を検証した。2プロセスのPIDが異なり、上限引き下げで既存Runを終了せず、第3Runを空き枠まで待機させる。再接続・再起動での設定永続化、起動値への復帰、前のsupervisorが残したrunningジョブの枠計上も確認した。通常の実シミュレーションを複数実行する既存テストも継続して通過。

## v0.7の旧モードとの等価性

ADR・BADR・GADRそれぞれで共通空間を未指定にし、10 ED × 50 UL、同一seed・同一入力をv0.7とv0.8で実行した。`transmissions`、`receptions`、`feedback`、`events`、`devices`、`uplinks`、`energy`の**全7テーブルの全値とイベント数が完全一致**。バージョン・ソースハッシュ・実時間などの実行メタデータは比較対象外。

[機械可読の比較結果](../validation/legacy-equivalence-v08.json)。これは当該シナリオでの回帰確認であり、ns-3全体との完全一致を意味しない。

## GUI・実プロセス連携

専用のローカルデータ領域でAPIとworkerを別プロセスとして起動し、ブラウザから以下を確認した。

1. `15-adr-common-space`を読み込み、TPを11 dBm、CRを4/6、SFを{8,10,11}、CHを{868.3,868.5 MHz}に変更。行動数6、比較拡張Warning、定数の説明を表示。
2. 起動時`--workers 1`のworkerへGUIから2並列を適用。通常の実行ボタンで別のRunを投入。
3. **12 ED・360,000 ULの2 Runを別PIDで同時実行**。稼働時間の重複は38.696秒。両方が正常完了し、GUIに進捗と完了結果を表示した。
4. 同一seedの2 Runについて全7追跡テーブルが完全一致。各Runの待機・送信時刻、乱数結果、エネルギーに並列実行順による違いはなかった。MCUは実測時間モードを使っていない。

[PID・時間・比較結果](../validation/parallel-v08-ui.json)、[GUI画面](adr-parallel-v08.png)。この測定は同時実行の成立と結果の再現性の検証であり、CPU台数に比例する性能向上や単一Runの高速化を主張しない。

## 適用範囲

UbuntuコンテナのテストはBuildKit RUNによる実行。対象のUbuntuカーネル5.15実機、NVIDIA GPU、通常のComposeサービスとしての起動は今回確認していない。GUI設定とキューはローカル専用領域で検証し、利用者の稼働中サーバーへはデプロイしていない。APIとworkerを両方v0.8へ更新・再起動した環境で利用する。
