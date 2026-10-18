# LoRaSim Research

Class A LoRaWAN研究用のPythonシミュレーターです。ns-3のLoRaWANモジュールを読み、伝搬・送信時間・SF間干渉・PHY受信制約を移植し、プログラマブルなED、管理GUI、独立ワーカー、ストリーミング出力を組み合わせています。

**研究用の初版です。ns-3全体との完全互換を保証するものではありません。** 対象はEU868 / BW 125 kHz / LogDistance / GoursaudまたはALOHA干渉モデルです。適用範囲と上流との差分を [FIDELITY.md](docs/FIDELITY.md) に記載しています。実験で使う前に、この条件が研究目的に合うことを確認してください。

## 起動（Ubuntu 22.04 / Docker Compose）

Docker EngineとComposeプラグインが利用可能な環境で、このディレクトリに移動して実行します。

```bash
docker compose up -d --build
docker compose exec api lorasim import-examples --data /data
```

管理画面: <http://localhost:8000>。`api`と`worker`は別コンテナで、ブラウザ／SSH切断後も動作します。サーバーが別端末なら `ssh -L 8000:localhost:8000 user@server` で接続してください。初期設定はlocalhostのみ公開します。

1. 保存済みプロファイルから例を選ぶ。
2. 基本設定、または詳細YAMLで編集する。
3. 「検証」「保存」「実験を実行」を押す。
4. 実行状況からジョブを選び、集計とParquetファイルを確認する。

`.env.example`を`.env`へコピーするとワーカー数等を変更できます。`LORASIM_WORKERS=4`は**独立した実験を4プロセスで実行**します。一つの無線ネットワークのイベントを非同期に並列化して因果関係を変えることはしません。

```bash
docker compose exec api lorasim submit examples/03-mobile-learning.yaml --data /data
docker compose exec api lorasim sweep examples/01-small.yaml --seeds 1 2 3 4 --data /data
docker compose exec api lorasim jobs --data /data
# 全結果をホストへコピー（停止不要）
docker compose cp api:/data/runs ./runs
# 停止。-v を付けなければ結果ボリュームを保持します。
docker compose down
```

## Pythonのみで実行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test,analysis]'
lorasim run examples/01-small.yaml --output runs/small
lorasim run examples/02-collisions.yaml --output runs/collisions
lorasim run examples/03-mobile-learning.yaml --output runs/mobile
pytest -q
```

`run`は前景実行です。SSH切断に耐える運用では、別ワーカーを使用します。

```bash
lorasim import-examples --data data
lorasim start-worker --data data --workers 2
lorasim serve --data data --host 127.0.0.1 --port 8000
# 別ターミナル
lorasim submit examples/01-small.yaml --data data
```

`start-worker`はstdinを切り離した新セッションで起動し、ログを`data/worker.log`へ出します。OSのログアウト時プロセス削除ポリシーまで避ける必要がある環境は、Composeまたは [systemd例](deploy/lorasim-worker.service) を使います。ホスト停止後にシミュレーション途中から再開するチェックポイントはありません。部分Parquetを保存し、失われた実行を`interrupted`として扱います。

## 機能

| 要件 | 実装 |
|---|---|
| Class A | TX→RX1→RX2、ACK、再送、RX中のウィンドウ延長、GW半二重 |
| 送信ごとのSF/TP/CR等 | 方策`select(Observation)`、受信結果`observe(Feedback)` |
| 方策割り当て | グループ、タグ、ED ID条件、後勝ち上書き |
| ED観測 | ACKの成否、成功したDLのRSSI、自己送信設定、任意で自己位置 |
| 送信タイミング | periodic、poisson、Python拡張 |
| 移動 | 静止、waypoints、反射境界random_walk、Python拡張 |
| GUI / YAML | 基本フォーム、全文エディター、保存・読込・検証・投入 |
| バックグラウンド | SQLite永続キュー、独立プロセス、進捗・キャンセル |
| 通知 | 非同期curl POST、割合閾値、終端通知、結果ログ |
| 計測 | UL/DLごとの送信、GW/ED受信、損失要因、受信電力、伝搬損失 |
| 分析 | 明示スキーマ付き分割Parquet、Arrow/pandas/matplotlib例 |
| デプロイ | Ubuntu 22.04 Dockerfile、Compose、任意GPUワーカー |

実行済みの小規模結果を [sample-results/small](sample-results/small) に同梱しています。

## 読む順序

- [モデルの根拠・適用範囲・相違点](docs/FIDELITY.md)
- [プロファイル・プラグイン・GPU利用](docs/CONFIGURATION.md)
- [パケットトラッキング仕様と分析](docs/TRACKING.md)
- [実施した検証と性能測定](docs/VALIDATION.md)
- [運用とデータ保全](docs/OPERATIONS.md)

上流ソースに基づく改変コードを含むため、ライセンスは **GPL-2.0-only** です。[NOTICE](NOTICE) と [LICENSE](LICENSE) を参照してください。
