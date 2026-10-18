# v0.7 検証記録

実施日: 2026-10-18。対象はローカルソースのv0.7.0です。既存ユーザーデータの移行・リモートへのデプロイは行っていません。

## 自動テスト

- macOS 26.3.1 arm64 / Python 3.11.4: **197 passed**、6.81秒。
- Ubuntu 22.04 / linux/amd64 / Python 3.10: Docker BuildKit内で**197 passed**、51.62秒。Apple Silicon上のQEMUエミュレーションであり、ネイティブx86性能の測定ではありません。
- 両環境のWarning 1件はFastAPI/Starlette TestClientのhttpx連携に関する非推奨通知です。
- Dockerの例05/06の検証、09/10/11/12/13/14の実行を完了。Compose設定の構文検証、JavaScript構文検証、git diffの空白検査を通過。
- Docker試験イメージ: `lorasim-research:v07-tested`、SHA256 `d5d7c616b9320da932c6a9a93852a3d627a91dfccb67bb10c5d66a57bb591ca8`。

ログ: [native](../validation/tests-v07-native.txt)、[Ubuntu](../validation/tests-v07-ubuntu22.txt)、[Docker build](../validation/docker-build-v07.txt)。長時間のCompose常駐運用試験・GPU性能試験ではありません。

## 重点検証

`tests/test_action_contract.py`:

- 同じライブラリ定義を複数EDへ適用し、割り当て条件を経由した場合も別インスタンスになること。
- bandit、bandit_sf_ch、LoRa-MAB、LinUCB、DQN、SEMAの可変NumPy配列がED間でメモリを共有しないこと。
- ED0に選択・ACK学習を行っても、ED1の学習配列・観測が変わらないこと。
- 実行後の各EDのQ表更新回数と、そのEDのフィードバック件数が一致すること。
- 全体216候補、固定CR/TPで18候補、旧固定値の読み替え、候補外出力の拒否。
- プロファイルの全体集合が未確定でもライブラリを保存でき、適用時に包含関係を検証すること。

`tests/test_analysis_modules.py`:

- 区間平均ではなく累積1〜tで集計すること。表示開始以前の履歴を含むこと。
- ED数・履歴長・損失分類を変え、行順をランダムにしたParquetで素朴な累積計算と一致すること。
- Censoredと未送信EDを除いたED等加重平均、ゼロ分母の未定義値。
- オフのモジュールの追加スキャンを行わないこと。
- エネルギー・bit・秒の単位、MCU切り替え、起動時除外、horizonを超えるairtimeの切り詰め。
- 独自分析モジュールの読込・定数検証・個別の例外表示と他モジュールの継続。

## PHY/MACの回帰確認

変更前commit `79b6ad0a1d1ee48b5dc120b2f013cf21845120a2`と現在のソースで、従来プロファイルの50 ED×20回＝1,000 ULを実行。**全7トラッキング表の値とイベント件数が完全一致**しました。伝搬・干渉・Time-on-Air・MACの計算式は変更していません。

[比較ログ](../validation/legacy-equivalence-v07.txt)。比較スクリプトの歴史的な出力ラベル`v04`は今回のbaseline commit、`v05`は今回のcurrent sourceを指します。実測時間0.183/0.195秒は小規模な回帰試験で、速度改善の根拠にはしません。

## 累積集計の性能

[合成ベンチマーク](../validation/analysis-benchmark-v07.json): 100万行、500 ED、各2000回、100表示点。`time_series()`を1回実行し**0.796秒**、約126万行/秒。プロセス最大RSSは約115.4 MiB（合成データの生成も含む）。Parquet作成時間は測定から除外しました。

これは累積解析の実装を確認するベンチマークで、シミュレーション速度や全7モジュールの処理時間を示すものではありません。再実行:

```bash
python scripts/benchmark_analysis.py --output work/analysis-benchmark --devices 500 --rounds 2000 --bins 100
```

出力ディレクトリ内に既存uplinksがある場合は上書きせず失敗します。

## 実ブラウザ

- 例14をYAML画面から取り込み、GUIで検証してRunを登録。
- 独立workerが実行しcompletedへ遷移。24 ED、250 UL、ED平均PRR約76.3%。
- 全7モジュールを追加して結果を表示。outcomes削除で6、airtimeをオフにして5モジュールだけ集計。
- banditのCR/TPを全体継承へ切り替え、表示行動数が18→72→216へ変わることを確認。別グループのExp3は18のまま。
- [GUI確認記録](../validation/ui-v07.json)、[画面](analysis-v07.png)。

試験Runの条件・結果は動作検証用で、アルゴリズム間の有意な性能差を評価する実験ではありません。
