# ADR・BADR・G-ADRの共通行動空間（v0.8）

既存の`adr`、`badr`、`gadr`へ`policy.action_space`を指定すると、シミュレータが解決した共通空間を受け取る**比較用拡張**になります。新しい選択器名は不要です。省略/nullの旧プロファイルは従来の制御・乱数消費を維持します。モデル参照時のnullは参照元を継承します。参照元が共通モードの場合、GUIの切替で参照を展開して旧モードに戻せます。

## GUIとYAML

1. 「周波数・行動空間」で全体のCH・SF・CR・TP候補を設定します。
2. 「ED・モデルの設定」でADR / BADR / G-ADRを選びます。新規選択では共通空間モードが有効です。
3. SF・CHは継承または部分集合をチェックし、TP・CRはプルダウンで各1値を固定します。モデルライブラリでも同じ操作です。
4. 原方式との差分の説明とWarningを確認して実行します。複数TP/CRの継承をYAMLから指定すると、日本語Errorと修正ヒントを表示します。

```yaml
action_space:
  spreading_factors: [7, 8, 9, 10, 11, 12]
  frequencies_hz: [868100000, 868300000, 868500000]
  coding_rates: [1, 2, 3, 4]
  powers_dbm: [8, 11, 14]
groups:
  - count: 100
    policy:
      kind: adr  # badr / gadrも同じ契約
      action_space:
        spreading_factors: [8, 10, 11]
        frequencies_hz: [868300000, 868500000]
        coding_rates: [2]  # 4/6固定
        powers_dbm: [11]  # 11 dBm固定
      options: {history_range: 20}
```

この例は全体216行動からED側6行動を許可します。候補は「使用を許される集合」であり、ADR/BADRが全組み合わせを探索するという意味ではありません。BADRでは上記`options`を`sequence`などの固有定数へ変更してください。実行例は[15-adr-common-space.yaml](../examples/15-adr-common-space.yaml)。

## 制御則の差分

ADRの基準は[固定コミットのAdrComponent](https://github.com/signetlabdei/lorawan/blob/e45b4af428c92f30b2e6c201055809110758125f/model/adr-component.cc)。G-ADRの基準はユーザー提供Sensors 20(2020)6466の式1/2/4/5とAlgorithm 1です。元の実装規約・PDFハッシュは[ALGORITHMS.md](ALGORITHMS.md)と[PAPERS.md](PAPERS.md)にあります。BADRは規格で唯一の制御則が定められた方式ではなく、既存の巡回列モデルを基準とします。

許可SF集合を昇順のSとし、`P(x)`を「x以上の最小の許可SF、存在しなければmax(S)」と定義します。

| 項目 | 旧モードの実装 | 共通空間での比較用拡張 |
|---|---|---|
| ADRのSNR集約・step | GW/履歴のmax/min/avg、floor((SNR−必要SNR−margin)/3) | 同じ |
| G-ADRのフィルタ・step | 平均±標本標準偏差内のSNR平均、int((SNR−必要SNR−margin)/3) | 同じ。intは0方向への丸め |
| NSによるSF更新 | 正stepにつきSFを1下げ、SF7で停止 | `P(max(7, 現在SF−max(step,0)))`。候補間の空きを無料で飛び越えてSFを下げない |
| NSによるTP更新 | 残った正stepでTPを2 dBずつ下げ、負stepで上げる | 無効。指定TPに固定。負stepでもNS側でSFを増やす新しい規則は追加しない |
| ADR初期値 | options.initial | 候補の最大SF、最初のCH、指定TP/CR。initial全体は未使用、他のRadio項目は既定値 |
| ED backoff | 初回は14 dBm、その後1SFずつ増加、最大SFで既定3CH復帰と繰返し1回 | 初回は固定TP維持、その後次の許可SFへ。最大候補で許可CHを維持し、繰返し1回への復帰は維持 |
| CH | ADR/GADRは設定CH、backoff後は既定3CH巡回。BADRは設定CH | 各UL送信試行で候補から独立な一様選択。CHをSNRで最適化・学習する変更ではない |
| CR | 設定値 | ED側の固定値 |
| BADRのSF列 | 指定列を各送信試行で巡回 | 各要素をPで写像。列長・順番・初期位相の規則を維持。重複を削除しない |

例：S={8,10,11}で現在SF10、step=1なら原計算のSF9をSF10に切り上げ、step=2ならSF8です。BADR既定列`[12,7,10,7,10,7]`は`[11,8,10,8,10,8]`になります。max(S)より大きい元のSFは上限へ制限されるため、元の信頼性を保つ保証はありません。全6SFを許可すればSF列の写像による変更はありません。

LinkADRReqはADRでは有効SFが変わる場合だけ、G-ADRでは履歴充足後に値が同じでも生成します（既存のAlgorithm 1解釈）。指示は実際のDL受信成功後だけEDへ反映します。UL履歴、重複メッセージの除外、カウンタはED別です。再送時にもCHを選びますが、ADRの新規フレームカウンタは増やしません。Duty Cycle待機中の再選択はしません。

固定TPに11 dBmなどEUの2 dB段階外を指定することもできますが、この場合の指示はシミュレータ内の論理値です。標準LinkADRReqの電力インデックスで符号化できるという主張はしません。帯域の最大TPと全体集合の制約は常に検証します。原方式からの変更は`ADR_COMPARISON_EXTENSION`としてRunの診断に保存し、候補集合は`profile.yaml`、ソース版は`manifest.json`へ残します。

## 比較条件

同じ配置、移動、UL負荷、seed群、SF/CH候補、TP/CR固定値、confirmed/max_attemptsで**手法ごとに独立したRun**を作ります。各手法を混在させると相互干渉する別の実験になります。候補を揃えてもADR/GADRのネットワーク観測や制御DL、BADRの未学習という性質は同一になりません。制御DLとLinkADRAnsのairtime・干渉・エネルギーはそのまま計上します。

全体TP/CRを複数にしてED側だけ固定する場合、同じ固定値を他の比較対象選択器にも渡してください。最大SFに制約があり、かつTP固定の場合、リンク不足時に原方式が可能だった復旧ができない場合があります。
