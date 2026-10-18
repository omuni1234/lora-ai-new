# 論文に基づく選択器（v0.4）

ユーザー提供PDFの本文・数式・Algorithmを読み、該当ページを画像でも照合して実装した。論文PDF自体は配布物に含めない。以下はアルゴリズムの実装根拠であり、論文の性能グラフの再現検証を意味しない。伝搬・干渉・MACは既存シミュレータのものを維持している。

| 実装名 | 読んだ原典と箇所 | PDF SHA-256 |
|---|---|---|
| `gadr` | Farhad et al., *Enhanced LoRaWAN Adaptive Data Rate for Mobile Internet of Things Devices*, Sensors 2020, 20, 6466. §3.1、PDF pp.7–8、式1/2/4/5、Algorithm 1 | `319f7651693fbe967ae57f9458d2c8b424234a6d0b616c2ab4a60134259eac44` |
| `lora_mab` | Ta et al., *LoRa-MAB: A Flexible Simulator for Decentralized Learning Resource Allocation in IoT Networks*. §IV-A、PDF p.5（印刷p.59）、Algorithm 1 | `6fd0f8ab15ef69b9e8bfaad6fc2a9fefbf92cebba046b0ceca8e2d4342d27533` |
| `linucb_sf` | Farhad et al., *LinUCB–SF: A Lightweight Linear Upper Confidence Bandit for Device-Side Spreading Factor Selection in LoRaWAN*, DOI 10.1109/JIOT.2026.3651343. PDF pp.4–7（印刷18407–18410）、式4–15、Algorithm 1、表II | `7a336ec164754da1ee9f6e4295624a95bbb92747f01dd3ec9b4bb394580ef05a` |

## LoRa-MAB

腕は `(SF, channel, TP)` の直積。初期重み1、初期確率1/K。探索率は `min(1,sqrt(K log K/((e−1)T)))`、論文の実験値T=10^7。GUIで候補とT、必要ならγを変更できる。K=1ではγ=0。

ACK受信を1、タイムアウトを0として、選択腕の重みを `exp(γr/(Kp))` 倍し、確率を `(1−γ)w/sum(w)+γ/K` へ更新する。失敗時は重み・確率を変更しない。オーバーフロー回避のため対数重みを保持し、全腕に共通の値を引く。これは確率を変えない数値安定化である。

原論文の実験1（6 SF、1チャネル、14 dBm）が既定値。実験2/3は周波数・TP候補で指定する。ACK学習なのでconfirmed ULが必須。再送を含めて各実送信の選択とフィードバックを対にする。論文の独自PHY/衝突仮定へシミュレータ全体を変更してはいない。

## G-ADR

成功ULの履歴M=20から平均と**標本**標準偏差（M−1）を算出。平均±標準偏差内のSNRだけを再平均する。標準偏差0も許容する。SNRを正規分布の密度で重み付けする処理ではない。

`steps=int((filtered_snr−required_snr−margin)/3)`。`int`は0方向への切り捨てであり、既存ADRの`floor`とは負の値で異なる。正のstepsでSFを先に下げ、その後TPを2 dB刻みで下げる。負ならTPだけを上げる。SF7–12、TP2–14 dBm、既定margin=10 dB。Algorithm 1の最後に従い、値が同じでも履歴充足後にLinkADRReqを生成する。指示はDL受信成功時だけEDへ反映する。

統合上の規約：原論文の文章にはADRACKReqを契機とする記載がある一方、Algorithm 1には開始条件がない。本実装はAlgorithm 1の入力履歴を、既存NSと同様に成功した新規アプリケーションフレームから逐次収集する。再送は同一フレームを二重計上しない。複数GWの集約、スライディング履歴、ED側backoffは既存ADRと共通。本文の添字0..MはM+1個になってしまうため、明示されたM個を用いる。これらの統合条件も再現実験に含めて扱う。

## LinUCB-SF

原論文の腕はSF7–12の6個であり、**TPの学習選択は定義していない**。`linucb_sf`ではTPをinitial.tp_dbmで固定する。原論文のEU868 3チャネルから、選択SFとは独立にホッピングする。

コンテキストは式4の6要素：定数1、tanh(前回受信済みUL SNR/10)、tanh(前回受信済みUL RSSI/20)、平滑化速度/最大速度、残り再送予算の割合、前回SF/12。速度はEDに許可された位置観測と決定時刻の差分から求める。位置観測がないとき速度特徴は0。現在のGW側状態や真の瞬間速度を直接参照しない。再送予算はmax_attempts−1をMaxRetx、attempt−1を使用済み再送数とし、再送なしでは割合1とする。

式7のSNR gate → 式12のUCB → 式13のペナルティ → Algorithm 1のヒステリシス → ε探索の順。式15のSherman–Morrison更新を用い、毎送信の行列逆演算を避ける。GW情報未受信時は初期SF、適用可能な腕が空のときはSF12へ戻す。現在腕がgateを通らない場合にヒステリシスで維持しない。

### DL通知の実装と情報境界

原論文p.4はGW/NSが測定したSNR/RSSIをMAC応答で通知すると説明するが、具体的な符号化やバイト数を定義していない。そこで、**研究用の通知形式**を採用した：種別1 byte + RSSI float32 + SNR float32 = 9 byte。標準LoRaWANにこの形式が定義されているとは主張しない。選択した返信GWのUL測定値を使い、ACKに9 byteを追加するためairtime、干渉、Duty Cycle、ED受信電力消費にも反映される。SNRは既存ADRと同じ125 kHz・noise figure 6 dBの式で求める。

ULがGWへ届いても、DLが不達なら端末のSNR/RSSI履歴は更新しない。DLのRSSIと、DLで通知されたULのRSSIは別のフィールドで記録する。過去の成功通知がある場合はその値を保持する。confirmed ULを必須とし、学習の結果は実際のACK成否による。

### 原典の未指定部分・不整合と実装の選択

| 項目 | 実装 |
|---|---|
| 表IIの既定値 | α=0.8、λ=1、η=0.08、link margin=2 dB、ε=0.05、hysteresis=0.05 |
| 逆行列の初期化 | 式9のA=λIからI/λ。Algorithm 1のA⁻¹=λIとはλ≠1で整合しないため式9に従う |
| 報酬の値域 | 式8をそのまま計算。本文は[0,1]とするが式は負や1超になり得る。`clip_reward=true`で追加クリップを明示的に選択可能 |
| 式8の未指定定数 | β=0.1、γmax=1、c=0.001/ms、δ=0.05、SFlow=8を**実装既定値**として公開 |
| スコアのT(a) | 比例定数未指定のため`airtime_scale × 2^(SF−7)`。既定scale=1。報酬Tは実際のairtimeをmsで使用 |
| 速度平滑化 | 未指定の係数は0.5、正規化上限は2 m/s。GUI変更可能 |
| 距離項 | 未指定の重みは0。明示された既知GW位置とED位置観測だけから算出。式13のg(d)は腕に共通であり、順位には影響しない |

これらを隠して「完全な論文再現」とは扱わない。設定値・実装版・通知方式を研究報告に記載する。

## LinUCB-SF×TP（原論文からの拡張）

`linucb_sf_tp`は別の名前・クラスで提供する。6 SF × 候補TP（既定8/11/14 dBm）の腕を持ち、SNR gateでは最後に受信したUL測定SNRへ `(候補TP − そのULの既知TP)` を加える。これは既存LogDistanceモデルと整合する予測だが、**原論文の選択器にはない変更**である。報酬は式8のままで、TP消費への追加ペナルティは入れない。比較では原論文版と別アルゴリズムとして表示する。

## 動かす

`examples/11-paper-selectors.yaml`は4モデルを同一実験内で動かす結合確認用。性能比較用にはアルゴリズムごとに独立したExperimentを作り、配置・乱数・負荷・追跡設定を揃える。`policy_state.jsonl`にはExp3の確率・重み、LinUCBの逆行列・b・θと最終受信済みUL情報を保存する。すべてのOptionsはWebモデルライブラリとEDグループ設定へ自動公開される。
