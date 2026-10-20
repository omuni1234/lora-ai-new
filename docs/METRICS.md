# 研究用評価指標と文献対応（v0.12）

一次文献を調べ、平均到達率だけでは見えない端末間の偏り、資源使用量、成功通信量とエネルギーの関係を比較できるようにしました。論文ごとにPHY/MAC、再送、成功の分母が違うため、本シミュレータの集計定義を下記に固定します。以下は指標の採用理由であり、各論文の実験全体の再現を主張するものではありません。

## 根拠と採用した指標

1. Abdelfadeel, Cionca, Pesch, **A Fair Adaptive Data Rate Algorithm for LoRaWAN** (2018), §1, §3, Fig.1–2。DERの端末間公平性をJain indexで評価し、DER・全体エネルギー・距離による偏りも比較しています。これに沿ってPRRの公平性・消費量を比較し、偏りの確認用に分位点と要求未達率を補助指標として追加しました。要求PRRの閾値と分位点の選択は本システムの追加仕様です。[原論文](https://arxiv.org/html/1801.00522)
2. Kim, Yoo, **Contention-Aware Adaptive Data Rate for Throughput Optimization in LoRaWAN**, Sensors 18(6), 1716 (2018)。SFへの集中と競合が総スループットに影響する問題を扱っています。SF/CH別の通信時間配分・平均airtime・再送割合・受信通信量の併読に採用しました。CH配分と再送割合の追加は本実装の診断用拡張です。[原論文](https://pmc.ncbi.nlm.nih.gov/articles/PMC6022063/)
3. Kufakunesu, Hancke, Abu-Mahfouz, **A Fuzzy-Logic Based Adaptive Data Rate Scheme for Energy-Efficient LoRaWAN Communication**, JSAN 11(4),65 (2022)。評価に総エネルギー、Confirmed Packet Success Rate、UL-PDR、干渉、GW busy、エネルギー効率を使っています。本実装は既存UL損失分類に加え、受信ペイロードと消費量の比を提供します。CPSRをUL-PRRと取り違えないことが必要です。[原論文](https://www.mdpi.com/2224-2708/11/4/65)

## 基本7モジュール（追加8モジュールは後述）

| kind | 内容 | 集計定義 |
|---|---|---|
| outcomes | ED数別の成功・損失割合 | ED別に`件数 / 判定済UL件数`を求め、対象EDを等加重平均 |
| convergence | 累積の収束 | 時点tまでの全履歴`1..t`で上記を計算。区間平均の積み上げではない |
| fairness | Jain / Gini / MinMax / CV | 対象区間のED別PRRベクトル。全員成功0は未定義 |
| energy | 平均J/ED | 区間に帰属した無線、任意でMCU。分母は全配置ED数 |
| reliability | PRR下位10%点・中央値・要求未達率・成功0のED割合 | 判定済ULのあるEDを分母。閾値未満は`< threshold`。分位点はNumPy既定の線形補間 |
| airtime | 平均airtime、再送割合、SF/CH別airtime配分 | 区間内UL試行。再送はattempt>1。SF/CHごとの送信時間和を全UL送信時間和で割る |
| efficiency | J/受信bit、受信bit/J、観測時間当たり受信bit | `B = Σ成功UL試行 8 × そのEDのpayload_bytes`、`E = 区間内消費J`。E/B、B/E、B/T |

### 境界と解釈

- `tx_index`はEDごとの実送信通番（1始まり、再送を含む）。t=0は送信前です。通常の範囲は両端を含む`[開始,終了]`。
- **累積だけは開始値を表示開始に使用**し、集計自体は常に1からです。開始200、終了500なら各表示点に200より前の履歴も含めます。
- `Received`は少なくとも1台のGWが受信したUL試行。GW数だけ二重加算しません。ACKの成功率ではありません。Censoredを成功/失敗へ割り当てません。
- 受信量Bは再送が複数回成功した場合、それぞれを数えます。重複除去後のアプリケーションgoodputでも、ACKを確認できたメッセージのCPSRでもありません。ペイロードにはMAC/PHYヘッダを含めません。
- `T`はsummary.jsonの実際に観測したシミュレーション秒数（中断Runでは中断まで）。実行に掛かったwall-clock秒数ではありません。送信回数で絞った場合も分母は全観測時間なので、「区間内で成功した通信の全観測期間への寄与」を示します。開始/終了送信番号の差を秒数に変換しません。summaryがなければrateはnullです。
- airtimeは開始したULを対象にし、終了horizonで切り詰めます。summaryがない直接解析では記録上の予定airtimeを使い、`airtime_basis=scheduled`で明示します。成功が確定しなかったULも実際の送信時間には含めます。重なった送信を足すため、無線チャネルの時間占有率ではありません。
- Eは成功時のTXだけでなく、同じ通番に帰属する待機・受信等を含みます。起動時通番0は除外。MCU含有はリクエスト全体の指定です。電力計算無効時はエネルギー効率をnullとします。
- B=0ではJ/bitをnull、E=0ではbit/Jをnullにします。有限値であるかのように0やInfinityへ置き換えません。E>0かつB=0のbit/Jは0です。
- fairness、reliability、airtime、efficiency、energyはRunごとに表示します。outcomesとconvergenceだけがExperiment×ED数の複数RunをED標本数で重み付けします。同一Run内の混在アルゴリズムを自動分離しません。

## 累積集計の計算量

表示する終点`t_j`を先に決め、各行を最初に含まれる終点のビンへ加算します。ED×ビンの件数配列に`cumsum(axis=0)`を行い、その後にED別の分母で割って平均します。すべてのtに対してParquetを読み直したり、以前の割合を単純に平均したりしません。

UL行数をP、ED数をN、表示点数をB、損失分類数をCとすると、追加の累積スキャンは概ね`O(P log B + NBC)`、メモリは`O(NBC + 8192行)`です。Bは最大500、N×Bが100万セル程度となるよう間引きます（N自体が100万を超える場合は少なくともNセルが必要）。間引くのは表示点であり、点に含める過去の送信データではありません。基本摘要を含めてもスキャン回数は表示点数に依存しません。

モジュール追加手順は[ANALYSIS_PLUGINS.md](ANALYSIS_PLUGINS.md)、トラッキングの列定義は[TRACKING.md](TRACKING.md)を参照してください。

## v0.12の追加指標

全15モジュール。追加は`abstract`, `prr_by_count`, `spatial_sf`, `sema_pool`, `stationary_distance`, `channel_distribution`, `channel_js`, `mobility_outcomes`です。数式・分母・旧データへの対応は[RESEARCH_ANALYSIS.md](RESEARCH_ANALYSIS.md)を参照してください。KL/JSは分布差の記述統計であり、小さい方策が常に高PRRであることを意味しません。
