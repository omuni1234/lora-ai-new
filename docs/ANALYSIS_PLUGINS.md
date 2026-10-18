# 分析モジュール開発ガイド（v0.7）

## 構成と責務

- `lorasim/analysis.py`: Parquetのバッチ読み込み、区間集計、累積集計、Experiment間の重み付け、リクエスト内キャッシュ。
- `lorasim/analysis_modules/base.py`: `AnalysisModule`と表示データの補助関数。
- `lorasim/analysis_modules/<名前>.py`: 指標定義・Options・計算・表示データを1モジュールにまとめる。
- `analysis_modules/__init__.py`: `PLUGIN`の自動発見、Options検証、選択されたモジュールの実行。
- GUI: メタデータから設定フォームを作り、返された汎用表示データを描画。通常の分析追加でapp.jsを編集する必要はない。

API処理で解析し、シミュレーションのイベントループには分析・可視化処理を加えません。GUI中央上部に結果、下部に追加・削除・有効化トグルとモジュールごとの定数を表示します。左側のExperiment・送信回数区間・ED数・MCU指定は共通条件です。削除は今回の分析構成から外す操作で、結果データやPythonファイルは消しません。

## 追加例

次を`lorasim/analysis_modules/mean_prr.py`に保存し、APIを再起動します。

```python
from pydantic import Field
from lorasim.interfaces import Options
from lorasim.analysis_modules.base import AnalysisModule, bars

class Settings(Options):
    percent: bool = Field(True, title="百分率で表示", description="オンならPRRを100倍して表示します。")

class MeanPRR(AnalysisModule):
    Options = Settings
    label = "平均PRR"
    description = "判定済みULがあるEDを等しく重み付けした平均。"
    sources = []  # 根拠となる論文・仕様のHTTPS URL
    requires = frozenset()  # 基本のUL集計だけで計算できる

    def compute(self, context, options):
        scale = 100 if options.percent else 1
        values = [None if r['mean_ed_prr'] is None else scale*r['mean_ed_prr']
                  for r in context.runs]
        return {"views": [bars(self.label, context.runs, values,
                              '%' if options.percent else '割合')]}

PLUGIN = MeanPRR
```

内蔵と同じ場所へ配置しない場合は`my_package:MeanPRR`をGUIの「独自の分析モジュールを読み込む」に指定します。`AnalysisModule`を継承し、API環境にインストールし、`LORASIM_ALLOW_PLUGINS=1`を設定します。モジュール一覧のキャッシュはプロセス内で保持するため、コード追加・修正後はAPIを再起動してください。任意Pythonのサンドボックスではなく、サーバー管理者が配置したコードを実行します。

`Options`には型・既定値・日本語title・descriptionを設定します。現在のメタデータ取得では`Options()`で既定値を作るため、全項目に既定値を用意してください。

## Context API

| API | 用途 |
|---|---|
| `context.runs` | 選択したExperiment/ED数のRun別集計。id、experiment_name、ed_count、fractions、mean_ed_prrなど |
| `context.result['by_ed_count']` | Experiment×ED数のED等加重割合 |
| `context.result['by_time']` | 累積時系列。`requires={'series'}`が必要 |
| `context.start`, `context.end` | 共通の送信通番範囲。endは未指定ならNone |
| `context.selected(tx_index)` | 通番が共通区間に含まれるか |
| `context.roots[run_id]` | 対象データのPath。読み込み専用として使う |
| `context.batches(run_id, table, columns)` | Arrow RecordBatchを8192行ずつ返す。必要列だけ指定する |
| `context.cached(key, loader)` | 同一解析リクエスト内で同じ中間結果を再利用 |
| `context.radio(run)` | UL airtime、再送、SF/CH配分の共通バッチ集計 |
| `context.delivery(run)` | 区間内成功ULのペイロードbitと観測時間 |

`requires`は`energy`（エネルギー表を読む）、`series`（累積表を生成）、`prr`（内部のED別PRR配列を保持）、`fairness`（Jain/Gini等を計算）の集合です。基本のUL集計はRun摘要・ED数のグループ化のため共通で行います。`device_prr`は未送信/未判定のみのEDを除いた値であり、添字はED IDではありません。モジュール実行後、通信量を抑えるためAPI応答から取り除きます。必要なら自身の集計結果に分位点・ヒストグラム等を返してください。

別のParquetを必要とするモジュールは`compute()`内で`context.batches()`を呼びます。トグルがオフなら`compute()`自体を呼びません。`cached()`のキーには自分のモジュール名・Run ID・必要なオプションを含め、全パケットを保存せず集計値だけをキャッシュします。

## 表示データ

`compute()`はJSONへ変換可能な辞書を返します。共通キー`views`には以下を複数指定できます。数値が未定義なら`None`を返し、NaN/Infinityを使わないでください。

```python
{"type": "bars", "title": "指標名", "unit": "J / bit",
 "items": [{"label": "Experiment / Run", "value": 0.02}]}
{"type": "stacked", "title": "割合", "unit": "送信通番 t",
 "items": [{"Received": 0.8, "Interference": 0.2}], "labels": [200]}
{"type": "table", "title": "集計値", "columns": ["Run", "値"],
 "rows": [["abc", 0.8]]}
```

`stacked`は非負で合計1以下の割合専用、`bars`は非負のスカラー比較向けです。生データや計算条件は`rows`など任意キーへ格納でき、JSON出力に保存されます。画面は文字列をテキストとして扱い、HTML/JavaScriptをモジュールから実行しません。通常は表示点数を500以下、行数を必要な範囲に抑えてください。

各モジュールの例外はそのモジュールに日本語Error・ヒントとして表示し、他のモジュールは継続します。入力Options違反や基本データの破損は解析要求全体のErrorです。定義の曖昧さを隠すために例外を0へ置き換えないでください。

## APIと再現

```json
{
  "experiment_ids": ["experiment-id"],
  "tx_start": 200, "tx_end": 500, "bins": 100,
  "include_mcu": true,
  "modules": [
    {"kind": "convergence", "enabled": true, "options": {}},
    {"kind": "reliability", "options": {"threshold": 0.95}},
    {"kind": "airtime", "enabled": false}
  ]
}
```

`POST /api/analysis`で実行。`GET /api/analysis/modules`は内蔵メタデータ、`GET /api/analysis/modules/schema?kind=my_package:Metric`は独自メタデータを返します。modules省略は従来の4指標。全オフ・重複指定はErrorです。応答の`modules`にはkind、options、status、定義、参考文献とdataを保存します。GUIのJSON出力が新指標を含む完全な出力です。既存CSVはRun別の基本集計用で、全モジュールの任意データを表現する形式ではありません。

テストでは、分母0・未送信ED・Censored・unequal ED counts・再送・複数GW・MCUの切り替え・範囲の両端・中断horizonを確認してください。`tests/test_analysis_modules.py`には手計算・ランダムデータの素朴な累積計算との比較、選択モジュールだけのスキャン、独自モジュールと例外隔離の例があります。
