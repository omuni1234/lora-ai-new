# D-LoRaの実装根拠・再現境界（v0.9）

## 対象原稿

R. Wang et al., “Efficient Resource Allocation Framework for LoRaWAN Network via Online Learning,” IEEE Internet of Things Journal, vol. 13, no. 12, pp. 25732 ff., 2026, DOI [10.1109/JIOT.2026.3676777](https://doi.org/10.1109/JIOT.2026.3676777)。ユーザー提供PDFの本文・数式・疑似コードを読み、PDF 6、7、10ページ（誌面25737、25738、25741）の画像で式19〜24、Algorithm 2、表IIIを照合しています。

原稿ファイル：`Efficient_Resource_Allocation_Framework_for_LoRaWAN_Network_via_Online_Learning.pdf`。
SHA-256：`ccde23f1bd5fb24e0af25b2190e80a410fc555d759f007e0549d47c2292e1bbe`。
PDF自体は配布物に含めません。誤添付された低ランク連合学習のD-LoRA論文は、この実装の根拠として使用していません。

## 実装と行動空間

`lorasim/plugins/selector/d_lora.py`、`kind: d_lora`。共通空間のCH（論文のCF）、SF、TPから各1個を選びます。CRは1値に固定してください。全体の候補集合とEDの部分集合の検証は他の選択器と共通です。TPを1値に固定すれば、モデルを増やさずSF×CHの比較ができます。

各EDはCF、SF、TPそれぞれに独立した選択回数 `N` と平均報酬 `Q` を保持します。別ED、同じライブラリ定義、別Runの間で可変状態・RNGを共有しません。学習状態は `policy_state.jsonl` にED別に保存します。

式19〜21の基底腕報酬は次のとおりです。失敗時も決定的ボーナスを加え、クリップはしません。

```text
r_cf = success
r_sf = success + xi  * (sf / 2**sf) / sum(s / 2**s for s in allowed_sfs)
r_tp = success + eta * (1 - tp / sum(allowed_powers))
```

式22：今回の標本を含む選択回数で `Q += (r - Q)/N` を更新します。Algorithm 2では回数の増分が更新より後に印刷されていますが、式22の分母 `T_i^t` に従い、今回を含めない0除算を避けています。

式23：`UCB = Q + c * sqrt(log(t)/(2*N))`。`t` はフィードバックを処理した送信試行数です。

式24：3基底腕のUCBの和を最大にする組み合わせを選びます。行動空間が直積で和が分離可能なので、各次元を個別に最大化することで全直積列挙と数学的に同じ最大値を得ます。選択・学習の時間／状態量はO(CH数＋SF数＋TP数)です。エンジンの共通行動検証用集合は従来どおり直積です。

表IIIの既定値は `exploration_c: 2`、`sf_weight_xi: 1`、`power_weight_eta: 1.8`。すべてGUI/YAMLから変更できます。初期化はAlgorithm 2の指示に従い、各次元で未試行の腕を優先し、すべての基底腕を少なくとも1回試します。原稿が指定しない探索順序・UCB同点処理はEDの専用乱数で一様選択します。直積の全組み合わせの探索ではありません。

## Class Aへ接続する際の差分

**選択・更新式の実装であり、論文のネットワーク実験全体の再現ではありません。**

| 項目 | 原稿 | 本実装 |
|---|---|---|
| 成功指標 | GWで衝突も伝搬損失もなく受信した指標 | EDが実際に受信したACK。UL成功・DL失敗も0。成功を無償で端末へ知らせる経路は設けない |
| フィードバック | 成否を観測することを仮定し、配送方法の詳細なし | Class AのRX1/RX2、GW half-duplex、Duty cycle、DL干渉を通過したACKのみ。confirmed=true必須 |
| 再送 | Algorithm 2のパケット送信単位 | 実送信試行ごとに選択・更新。原稿との比較にはmax_attempts=1を推奨 |
| TP報酬の単位 | 候補はdBm、式10のエネルギーは線形電力を要するが、式21の単位は明示なし | 既定dbmは候補の数値をそのまま式21へ使用。0以下のdBmは拒否。mwは10^(dBm/10)へ変換する明示的な比較オプション |
| PHY | LoRaSimPlus、shadowing・AWGN・論文の衝突モデル | 既存ns-3由来のLogDistance・Goursaud/ALOHA・感度判定を維持。ノイズやモデルを置換しない |
| 消費エネルギー | 式10のRF送信エネルギー | 既存ns-3由来の無線状態電流モデル＋任意MCUモデルを維持 |
| 候補 | 原稿の例は8CF×6SF×7TP、CR4/5 | GUIの共通候補とED部分集合。既定プロファイルは3CH・TP固定なので、原稿条件と混同しない |

候補集合を変更するとSF/TP報酬の正規化分母も変わります。TPが1候補なら低TPボーナスは0です。これらの差分、原稿が指定しない同点処理、採用したTP数値単位は実験の報告に記載してください。GUIの設定検証では `DLORA_FEEDBACK` Warningを表示します。

パケット未送信・Duty cycle待機だけでは更新しません。未確認ULの `ack=None` では更新せず、シミュレーション終了で未確定の送信も勝手に失敗学習しません。EDが観測できない他EDの学習結果・GWの真の成否・位置は参照しません。

論文が参照する[LoRaSimPlus](https://github.com/Rich-King395/LoRaSimPlus)の公開READMEも照会しましたが、D-LoRaの式の実装根拠は添付PDFです。理論的な収束・性能優位性を本実装で検証したとは主張しません。

## 今回追加しない手法

ユーザーの確認結果に従い、LMLoRaとHybridQ-ADRは登録・実装しません。

- LMLoRa：パケット内の連続RSSI、独自の内容反復、probe/CAD、全二重GW等が既存PHY/MACの範囲外。簡略選択器も不要との指示を受けています。
- HybridQ-ADR：Class Aへの接続自体は可能ですが、原稿のクラスタリング、AssignSF、混雑フラグ生成が一意に定義されず、本文とAlgorithm 1にTP更新幅などの不一致があります。補完を加えた比較実装は今回は見送る指示を受けています。

検討した原稿の識別子：LMLoRa PDF SHA-256 `66f7bf263e323ab128a73ad253c04e48e19b10559f000cd1fb80b6f5d8f0da5b`、HybridQ-ADR DOI `10.1109/JIOT.2026.3659938`、PDF SHA-256 `47fcf13ef689d18feccc1749dbd310a5f09548217a14fe5d64ded7d29a348e01`。
