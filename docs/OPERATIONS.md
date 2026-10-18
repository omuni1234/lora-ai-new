# 運用

## プロセスと永続化

GUIはFastAPIへ入力を送り、SQLiteの永続キューへ登録します。ワーカースーパーバイザーは別プロセス／別コンテナでキューをclaimし、実験をspawn方式の子プロセスで実行します。APIを再起動しても実験はAPIの子プロセスではないため継続します。同一データディレクトリのworker supervisorはflockで一つに制限し、内部の`--workers`で並列数を制御します。

状態は `queued → running → completed / failed / cancelled`。workerからのheartbeatが30秒以上消えた既存runningジョブは`interrupted`にします。5秒間隔のheartbeatはシミュレーション時間の進み方と独立しています。worker再起動時に同じジョブを無断で再実行せず、新しいjob_idへ同じ設定を再投入します。

ブラウザの接続数やポーリングを停止しても実験は止まりません。SSH切断後の継続はCompose `up -d`またはsystemdを推奨します。`start-worker`は端末のSIGHUPを避けますが、管理者のログアウト時プロセス削除やホスト再起動には対抗しません。

SQLiteは同一ホストのローカルディスク／Docker named volumeで使います。NFS共有上で複数ホストから並列ワーカーを起動する分散キューではありません。停止後の再開チェックポイント、自動再試行、複数ホストへまたがる実行管理は未実装です。

## 進捗と通知

進捗はsimulation time / duration。イベントを処理しながら、実時間0.25秒または整数%進行のタイミングで更新し、最終状態を必ず保存します。GUIは表示中だけ4秒間隔で取得します。ETAは表示していません。高負荷区間でsimulation timeと実時間の比率が変わるためです。

```yaml
notifications:
  - url: https://YOUR-SYSTEM.example/progress
    every_percent: 10
    timeout_s: 5
```

実際に起動するコマンドの構造は次です。JSONはstdin経由で渡します。

```text
curl --silent --show-error --fail --max-time 5 --request POST \
  --header 'Content-Type: application/json' --data-binary @- --url <configured-url>
```

本文はjob_id、status、progress(0..1)、sim_time_s、duration_s、wall_s、counts。シェルは使わず、curlの任意引数や任意shell commandをYAMLから実行しません。http(s) URLのみ受け付けます。通知は専用スレッドで送信し、キューは最新の1更新へ集約するため、速い実験では10%刻みの全通知が届く保証はありません。最新進捗と完了／失敗の通知を優先します。受信システムはjob_idとstatusを使い冪等に処理してください。

curlの終了コードとエラーをジョブ内`notifications.jsonl`へ追記します。通知失敗はシミュレーション失敗にしません。自動再試行や永続配信保証はありません。ローカルHTTP受信サーバーによる送信テストを実施しています。

## API・アクセス

初期設定はlocalhostにbindします。リモートアクセスはSSHトンネルを使用できます。外部公開するなら`LORASIM_API_TOKEN`を設定し、TLS終端とアクセス制御を別途設置してください。GUIの「API認証」はトークンをそのタブのsessionStorageへ保存します。ユーザー分離・ロール権限・公開SaaS向けマルチテナント機能はありません。

Python拡張を許可すると、指定モジュールのconstructorが設定検証時にも実行されます。自身で管理するプラグインを使用し、constructorでは軽い初期化だけを行い、重いモデルロードはselect時に遅延してください。通知URLも利用者が管理する宛先のみ設定してください。

`GET /api/jobs`は最新100件、`GET /api/jobs/{id}`は個別詳細。`/files`で結果一覧、`/files/{path}`でファイルを取得します。profile.yamlに保存する値には秘密情報を含めないでください。ノード座標や観測結果を外部へ自動送信する実装はなく、通知本文は集計のみです。

## Docker / GPU

CPU版DockerfileはUbuntu 22.04とPython3.10を使用します。ComposeはAPIとworkerで同じnamed volumeを共有し、非rootユーザーで動作します。依存はpyprojectで互換範囲を指定し、実際に解決したバージョンはmanifestへ保存します。CPU環境や依存解決の完全再現が必要なら、検証済み環境のイメージdigestとrequirementsの厳密lockを保存してください。今回のmacOS検証版の依存一覧はvalidation/python-environment.txtに収録します。

GPU版は`Dockerfile.gpu`＋`compose.gpu.yaml`です。CUDA12.4.1/cuDNNランタイムとPyTorch2.5.1 cu124を使用します。ホストのNVIDIAドライバーとContainer Toolkitが必要です。ホストのCUDA/cuDNNがインストール済みでもDockerへGPUを渡す設定は別に必要です。[NVIDIA Container Toolkit公式手順](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)に従って設定してください。

Docker DesktopでUbuntu 22.04 / linux/amd64イメージをビルドし、BuildKitのRUN内で61件のテストが通過しました（Apple Silicon上でx86-64エミュレーション）。通常のdocker run/Compose起動は、この端末のDocker daemonでCreatedのまま開始要求が完了しなかったため未確認です。シミュレーターを起動しない/bin/trueでも同じ状態になり、原因は特定できていません。既存環境へ影響するdaemon再起動は行っていません。ローカルmacOSでのAPI・ワーカー独立実行は確認済みです。NVIDIA GPUと指定Linuxカーネル上の実機試験は未実施です。


## v0.2の運用

起動時にSQLiteへExperimentとモデルライブラリの表を追加します。既存Runは削除せず独立Experimentへ移行します。v0.1のParquetに新しい列を捏造する自動変換はありません。新しい期間指定分析には再実行が必要です。

モデルライブラリ更新後も投入済みRunは埋め込み定義を使います。独自Pythonプラグインは別途バージョン固定してください。`lorasim sweep`はseed群を一つのExperimentにまとめます。`submit/sweep --experiment-id ID`で既存Experimentへ追加できます。

v0.2の最新Linuxテストログは[こちら](../validation/docker-v02-test.log)。実機NVIDIA、指定Linuxカーネル、通常のComposeサービス起動は未確認です。BuildKit RUNのUbuntu/amd64実行と、macOSでのAPI/独立ワーカー/ブラウザ連携を検証しています。

## v0.8: GUIから複数Runを並列実行

右側「実行リソース」の「同時実行するRunの上限」を2以上にして「並列数を適用」を押します。別のRunを通常どおり登録すれば、空き枠で開始します。1 Runにつき独立したspawnプロセスなので、複数のCPUコアを利用できます。1つのDESのイベント順序・PHY計算を並列化する変更はありません。同じExperimentに属するRunも同時実行の対象です。

- 実行中数、待機数、上限、workerのCPU枠を表示します。CPU枠は論理CPU/affinityと通常のLinux cgroup CPU quotaから求める目安で、CPUの予約や固定割り当てではありません。
- 上限は1〜256。保存領域のSQLiteに永続化し、同じ領域の全ユーザー・Runに適用します。プロファイルの実験条件には含めません。APIや画面の再起動でも保持します。
- workerは約0.2秒ごとに読み直し、空き枠を確認します。GUI表示は表示中だけ4秒間隔で更新します。上限を下げても実行中のRunは止めず、枠が空くまで新規起動を待ちます。
- 優先順位は**保存済みGUI設定 > workerの`--workers` > min(2, CPU枠)**。「起動設定に戻す」でGUI設定を削除できます。Composeの`LORASIM_WORKERS`とsystemdの値は`--workers`の初期値です。
- worker停止中も設定を保存できますが、APIはworkerを自動起動しません。次回起動で反映します。停止中のCPU表示は最後のworker値、未起動ならAPI環境の推定値です。
- 同じデータ領域のsupervisorは1つに保ちます。flockとSQLiteの原子的claimで二重起動を防ぎ、前のsupervisorが残した生存中Runも枠数に含めます。

CLI例（GUIの保存値がなければ4並列）：

```bash
lorasim start-worker --data data --workers 4
lorasim sweep examples/15-adr-common-space.yaml --data data --seeds 1 2 3 4
```

API: `GET /api/worker`で状態とRun→PIDの`processes`、`PUT /api/worker/settings`に`{"workers":4}`で変更、`DELETE /api/worker/settings`で起動設定へ戻します。既存APIと同じBearer認証を適用します。

各RunはそれぞれRAM・GPUメモリ・出力I/Oを必要とします。保存用`writer_processes`は別枠で、4 Run × writer_processes=2ならDES4＋保存8プロセスです。DQN等のGPU学習はGPUを共有し、GPUごとの振り分けは行いません。CPU枠超過はGUIで注意を表示します。固定/宣言済みMCU時間を使う条件の計算結果は並列順に依存しませんが、`energy.mcu.mode: measured`の実測時間とエネルギーは競合負荷で変化し得ます。任意の独自プラグインが外部の共有状態を使う場合の再現性はプラグイン実装側の責任です。
