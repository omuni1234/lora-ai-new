# LoRaSim ドキュメント

現行版: **v0.14.1**。この文書群はGUIの「ドキュメント」タブから、本文検索・目次移動・Markdown原文表示・保存ができます。ネット接続なしでも同梱文書を読めます。外部の論文リンクだけは別タブで開きます。

## はじめに

- [ED数 × 選択アルゴリズムの一括実行](BATCH_RUNS.md): 直積の確認、Experimentへの整理、原子的キュー登録。

- [操作ガイド](GUI.md): 実験設定、Abstruct、Calculator、比較・分析、文書閲覧。
- [README](../README.md): 導入、Docker、CLI、これまでの変更。
- [運用](OPERATIONS.md): バックグラウンド実行、並列Run、通知、接続。
- [設定仕様](CONFIGURATION.md): YAMLと各モデルの設定。
- [理論の適用範囲](FIDELITY.md): ns-3との対応、再現する範囲と差分。

## 分析する

- [ExperimentとPRRの分母](ANALYSIS.md)
- [v0.12の比較・分析](RESEARCH_ANALYSIS.md): 数値一覧、位置SF、SEMA残存、距離別PRR、KL/JS。
- [評価指標と文献](METRICS.md)
- [Parquetトラッキング仕様](TRACKING.md): 新規Runはschema_version=4。
- [分析モジュールを開発する](ANALYSIS_PLUGINS.md)

## 実験前に確認する

- [伝搬距離Calculator](CALCULATOR.md)
- [干渉・GW送信・感度のトレードオフ予測](TRADEOFF_FORECAST.md)
- [移動と送信タイミングによる経験分布](EXPERIENCE_DISTRIBUTION.md)
- [OpenAI APIコンサルタント](AI_CONSULTANT.md)

## モデルを使う・開発する

- [ハイパーパラメータ調整](TUNING.md): 予算配分、局所・網羅探索、独立seed確認、ライブラリへの登録。

- [開発者ガイド](DEVELOPER.md)、[プラグインと設定公開](PLUGINS.md)
- [共通行動空間](ACTION_SPACE.md)、[ADR比較](ADR_COMPARISON.md)
- [選択アルゴリズム](ALGORITHMS.md)、[SEMA/DQN](SEMA.md)
- [Waypoint](WAYPOINTS.md)、[Markov](MARKOV.md)
- [エネルギー](ENERGY.md)、[高速化](PERFORMANCE.md)、[日本語診断](DIAGNOSTICS.md)

## 検証記録

[現行版の検証](VALIDATION-v0141.md)。左の文書一覧にある旧版のVALIDATIONは、当時の環境・テスト結果を残した履歴です。過去の件数や速度を現行版で測り直した結果とは解釈しないでください。論文の対応・補完点は[PAPERS](PAPERS.md)と[PAPERS_V09](PAPERS_V09.md)に記載しています。

## v0.14の追加・更新

- [AIコンサルタント：比較・分析、接続設定、逐次会話](AI_CONSULTANT.md)
- [v0.14の検証記録](VALIDATION-v014.md)
