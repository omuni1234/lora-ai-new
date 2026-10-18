# 実行速度と試験的なマルチコア処理（v0.4）

## 使い方

GUIの「実行方式・高速化」で設定する。旧YAMLでも等価高速化は既定で有効。

```yaml
execution:
  optimized: true
  geometry_cache_bytes: 67108864
  writer_processes: 2  # 試験機能。0なら逐次保存（既定）
```

`examples/09-fast-multicore.yaml`ですぐ実行できる。1つの実験でDES本体と、Arrow変換・Parquet圧縮を担当する複数のCPUプロセスが並行動作する。**イベントの計算本体は因果順序を保つ単一プロセス**。全コア100%や常時の高速化を保証する方式ではなく、記録量・CPU・ストレージ・プロセス起動/転送費用で効果が変わる。小規模実験では遅くなる場合があるため試験機能とし、既定値は0。

`LORASIM_WORKERS`は独立実験の同時実行数。`writer_processes`は1実験内の保存プロセス数。たとえばworkers=4、writer_processes=2なら、最大4個のDES本体と8個の保存プロセスが動く。GPU/CUDAはこの小粒度の因果的イベント処理の高速化には使用していない。

## 変更した処理

1. **DL到着イベントの集約**：元の全ED到着の時刻とFIFO番号を保持する。次の外部イベントまで、受信状態が変わり得ないEDの到着をまとめて処理する。受信待機中のEDと宛先EDは個別に評価する。途中の受信ウィンドウ開始、他の送信、受信終了を追い越さない。同時刻も元の番号順。到着遅延の丸めや電波伝搬を省略していない。
2. **静止リンクのキャッシュ**：全EDが組込みstationaryのときのみ、距離・損失・伝搬遅延を再利用する。LRU上限は既定64 MiB。TPごとの受信電力は毎回計算。動くEDがいる場合はこのキャッシュを使わない。
3. **ランダムウォークの配列計算**：組込みクラスだけを対象に位置・反射処理をまとめる。方向変更時の各ED専用RNGの呼出順と浮動小数演算を維持。独自クラスやサブクラスへ推測で適用しない。
4. **別プロセス保存**：親がpart番号を割り当て、各子がArrow変換、Zstd圧縮、tmp→parquetの置換を行う。処理待ちバッチ数を最大2×保存プロセス数に制限し、満杯時は待機する。エラーは親へ伝播し実験を失敗として扱う。終了時に全バッチを待ってから集計する。

感度未満の信号も干渉源として残す。追跡テーブルの間引き、MACウィンドウの省略、時間粒度の粗化、無線モデルの変更は行っていない。独自MLモデルの推論時間そのものの高速化はモデル/実行ライブラリに依存する。LinUCBは論文のrank-one更新、Exp3は安定化した重み更新を使う。

## 再現可能な速度比較

```bash
python scripts/benchmark_execution.py --output runs/speed-static --n 500 --rounds 40
python scripts/benchmark_execution.py --output runs/speed-mobile --n 500 --rounds 40 --mobility random_walk
```

reference（等価高速化off）、optimized（on）、multicore（on＋保存2プロセス）を同じ乱数・全追跡で実行し、全Parquet列の完全一致も確認する。`benchmark.json`は総UL、親/子のCPU時間、セットアップ込み実時間、シミュレーション実時間、環境を記録する。信頼できる比較には他の負荷を止め、`--repeat 3`など複数回測定する。

**「2万回」の区別**：`--rounds 40 --n 500`は1台40回、全体2万UL。1台あたり2万回なら`--rounds 20000`で全体1千万ULになる。後者は全追跡のストレージ量も大きくなる。短い測定から長時間の確定所要時間を主張せず、利用端末でこのコマンドにより確認する。

同梱の実測値と検証条件は[VALIDATION-v04.md](VALIDATION-v04.md)を参照。`execution.optimized=false`は比較のために元の実行方式を残したもので、物理モデルは同じ。

## リソース上限

通常バッファは7テーブル×batch_rows行。並列時にはこれに最大2×writer_processesの送信待ちバッチ、子での変換用配列、プロセス実行環境が加わる。RAM使用は全実験時間に比例して増えない。batch_rowsを大きくするとファイル数は減るが、ピークRAMは増える。geometry_cache_bytesとmax_air_bytesも別に確保されるので合計を考慮する。

進捗更新やキャンセル確認の単位は実際のイベント処理。重いユーザー定義select/observeの実行中には停止判定へ戻れない。並列保存では既に投入した有限個のバッチを回収してから終了する。突然の強制終了でも確定済みpartは独立に読めるが、tmpや未投入のバッファは完了データではない。

## v0.5: 到着順と静止区間の再利用

DL到着時刻の500件などのPythonタプル生成・sortを、安定ソートされたNumPyのインデックスと遅延参照に置き換えました。全端末静止時にはGWからの到着順も既存LRUのbyte上限内でキャッシュします。移動時は遅延に従って再ソートします。同時刻は元のED ID/FIFO順を保ち、途中の状態変更イベントで必ず区切ります。論理イベント数、受信の可否、記録順は変更しません。

組込みWaypoints/Markovの静止区間では、次の区間境界まで前回位置を再利用します。移動中は元の `position()` と `np.interp` を呼ぶため、浮動小数点の補間式を近似しません。独自モデル・サブクラスには適用せず、元の呼び出しを維持します。`execution.optimized: false` でこれらを無効にし、逐次イベントと完全な位置更新による参照処理に戻せます。

学習回数を減らす、干渉信号を省く、ACKを理想化する、時間を丸める変更は行っていません。DQNの `train_every` は別の実験条件であり、物理計算の等価な高速化に含めません。イベント所有スレッドは1本です。複数コアの試験機能は従来通り独立した保存プロセスで、DQNのGPU実行や並列離散イベント実行を追加したものではありません。

旧版の展開フォルダとの比較を再現するコマンド:

```bash
python scripts/benchmark_revisions.py --baseline-source /path/to/v04 \
  --output runs/revision-static --n 500 --rounds 2000
python scripts/benchmark_revisions.py --baseline-source /path/to/v04 \
  --output runs/revision-waypoints --n 500 --rounds 40 --mobility waypoints
```

両版を順番に別プロセスで実行し、全7テーブルをバッチ単位で比較します。起動後のモデル作成・全追跡・最終集計を測定に含めます。結果・環境・制約は [VALIDATION-v05.md](VALIDATION-v05.md) に記載します。
