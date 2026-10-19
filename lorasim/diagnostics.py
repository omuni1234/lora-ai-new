"""Japanese boundary diagnostics. Never called from the simulation event loop.

Warnings are review rules, not additional PHY constraints. Original exceptions
remain available for developers; unknown failures are never assigned a guessed cause.
"""
from __future__ import annotations
import errno
from pydantic import ValidationError
import yaml
from .ui_metadata import FIELDS


def issue(code, message, hint, *, severity='error', path='', detail=''):
    return dict(code=code, severity=severity, path=path, message=message,
                hint=hint, detail=str(detail)[:8000])


# Match only known simulator messages; user/plugin exceptions fall back to a
# Japanese explanation plus the unmodified diagnostic, rather than a false translation.
RULES = [
    ('action_space requires fixed dimension', 'ACTION_FIXED_REQUIRED', 'この選択器が固定を要求する次元に複数の候補があります。', '詳細に記載されたpowers_dbm（送信電力）またはcoding_rates（符号化率）をEDの行動空間で1値に固定してください。全体集合には複数候補を登録できます。', 'groups'),
    ('waypoint speed_max_m_s', 'WAYPOINT_SPEED_RANGE', '移動速度の最大値が最小値を下回っています。', '出発速度を0より大きくし、最大値を最小値以上に指定してください。', ''),
    ('D-LoRa dBm reward', 'DLORA_POWER_UNIT', 'D-LoRaのdBm報酬式に0以下の電力候補が指定されています。', '正のdBm候補を指定するか、TP報酬の数値単位をmwへ変更してください。採用単位を比較条件に明記してください。', ''),
    ('hopping channels must', 'CHANNEL_DUPLICATE', 'ランダム選択するCH候補が重複しています。', '同じ中心周波数を重複登録しないでください。', ''),
    ('random waypoint mobility queries', 'WAYPOINT_TIME_ORDER', 'ウェイポイント位置を過去の時刻に戻って問い合わせました。', 'プラグインからの位置照会を時刻の昇順にしてください。', ''),
    ('random waypoint exceeds', 'WAYPOINT_TRANSITIONS', '1回の位置照会でウェイポイント遷移が10万回を超えました。', '極端に小さいエリア、高速移動、静止時間0、長時間の一括プレビューを見直してください。', ''),
    ('concurrent workers must', 'WORKER_LIMIT', '同時実行数が不正です。', '1〜256の整数を指定してください。CPU数と各Runのメモリ使用量に応じて調整してください。', ''),
    ('ED action_space must be a subset', 'ACTION_SUBSET', 'EDの候補に全体のパラメータ空間にない値があります。', '全体集合に値を追加するか、ED側を全体の部分集合にしてください。', 'groups'),
    ('action_space candidates must', 'ACTION_CANDIDATES', 'パラメータ候補が重複、範囲外、または有限値ではありません。', 'SFは7〜12、CRは1〜4、TPは−20〜27 dBmを重複なく指定してください。', 'action_space'),
    ('selector does not accept configurable', 'ACTION_UNSUPPORTED', 'この選択器は共通行動空間の受け取りに対応していません。', '共通行動空間を未指定に戻すか、対応する選択器へ変更してください。独自モデルはaccepts_action_space=Trueとコンストラクタ引数を実装します。', 'groups'),
    ('action_space legacy scalar conflicts', 'ACTION_CONFLICT', '旧形式の固定値と新形式の候補集合が矛盾しています。', '旧tp_dbm/crを削除し、powers_dbm/coding_ratesの候補集合に統一してください。', 'action_space'),
    ('action_space is only available', 'ACTION_CATEGORY', '行動空間はパラメータ選択器にだけ指定できます。', '移動・送信タイミングモデルのaction_spaceを削除してください。', 'groups'),
    ('legacy plugin requires fixed', 'ACTION_LEGACY', '旧形式の選択器はこの次元の複数候補に対応していません。', '候補を1値に固定するか、共通行動空間に対応した選択器を使用してください。', 'groups'),
    ('unknown analysis module', 'ANALYSIS_UNKNOWN', '指定した分析モジュールが見つかりません。', '分析モジュール一覧を更新するか、Pythonモジュールの配置とクラス名を確認してください。', ''),
    ('duplicate analysis module', 'ANALYSIS_DUPLICATE', '同じ分析モジュールが重複して指定されています。', '各モジュールは1つだけ追加してください。', ''),
    ('no enabled analysis modules', 'ANALYSIS_EMPTY', '有効な分析モジュールがありません。', '中央下部でモジュールを追加し、少なくとも1つをオンにしてください。', ''),
    ('DQN loss is non-finite', 'DQN_DIVERGED', 'DQNの学習損失が有限値ではなくなりました。', '学習率を下げる、RSSI入力を正規化するなどして数値の発散を確認してください。', 'groups'),
    ('LoRa-MAB action candidates', 'MAB_DUPLICATE', 'LoRa-MABの候補行動が重複しています。', 'SF・送信電力・周波数の各候補リストから重複を除いてください。', ''),
    ('unique powers in', 'TP_CANDIDATES', '送信電力の候補に重複または範囲外の値があります。', '−20〜27 dBmの異なる値を指定し、各サブバンドの上限も確認してください。', ''),
    ('initial TP must', 'INITIAL_TP', '初期送信電力が候補電力の一覧に含まれていません。', '初期送信電力をpowers_dbmに含まれる値にしてください。', ''),
    ('BADR sequence must', 'BADR_SEQUENCE', 'BADRのSF系列が不正です。', 'SF7〜SF12の値のみで系列を指定してください。', ''),
    ('ADR history_range', 'ADR_HISTORY', 'ADRの履歴数が範囲外です。', '履歴数を1〜100に指定してください。', ''),
    ('ADR combining method', 'ADR_COMBINE', 'ADRの集約方式が不正です。', 'max・min・avgのいずれかを指定してください。', ''),
    ('invalid ADR backoff or margin', 'ADR_PARAMETERS', 'ADRのbackoffまたはリンク余裕の値が不正です。', 'ack_limit・ack_delayを1以上、リンク余裕を有限値にしてください。', ''),
    ('actions cannot be empty', 'EMPTY_ACTIONS', '選択可能な行動がありません。', '無線設定の候補を1件以上登録してください。', ''),
    ('invalid bandit hyperparameters', 'BANDIT_PARAMETERS', 'バンディットのハイパーパラメータが制約を満たしていません。', '探索率・更新係数・候補行動をモデルの設定仕様に合わせてください。', ''),
    ('invalid random-walk parameters', 'RANDOM_WALK_PARAMETERS', 'ランダムウォークの移動パラメータが不正です。', '速度・時間刻み・履歴上限をモデルの設定仕様に合わせてください。', ''),
    ('mean_s must be positive', 'POISSON_INTERVAL', 'ポアソン送信の平均間隔は正の値である必要があります。', '平均間隔を0より大きい秒数で指定してください。', ''),
    ('invalid experiment name', 'EXPERIMENT_NAME', 'Experimentの名前または説明が不正です。', '空白だけの名前は使えません。名前は160文字、説明は10000文字以内にしてください。', ''),
    ('invalid category or model name', 'MODEL_NAME', 'モデルの種類または保存名が不正です。', '種類はpolicy・mobility・timing、名前は空白だけを除く120文字以内にしてください。', ''),
    ('catalog entries must', 'CATALOG_IMPLEMENTATION', 'ライブラリ定義には参照ではなく実装を指定する必要があります。', 'refを外し、kindとoptionsを指定してください。', ''),
    ('profile name must', 'PROFILE_NAME', '保存するプロファイル名が不正です。', '空白だけを除く1〜120文字の名前を指定してください。', 'name'),
    ('area_m must', 'AREA', 'エリアの幅と奥行きには、有限の正の数が必要です。', '幅・奥行きを0より大きいメートル値で指定してください。', 'area_m'),
    ('at least one gateway', 'EMPTY_NETWORK', 'GWとEDグループがそれぞれ1件以上必要です。', 'ゲートウェイとEDグループを追加してください。', 'gateways'),
    ('duplicate gateway', 'DUPLICATE_ID', 'GWの識別子が重複しています。', 'IDを空欄にすると自動採番されます。', 'gateways'),
    ('duplicate group', 'DUPLICATE_GROUP', 'EDグループ名が重複しています。', '名前を空欄にするか、グループごとに異なる名前を指定してください。', 'groups'),
    ('duplicate sub-band', 'DUPLICATE_BAND', 'サブバンドの識別子が重複しています。', 'IDを空欄にするか、異なるIDを指定してください。', 'sub_bands'),
    ('overlapping sub-bands', 'OVERLAPPING_BANDS', 'サブバンドの周波数範囲が重複しています。', '各帯域を重ならない半開区間 [下端, 上端) にしてください。', 'sub_bands'),
    ('invalid sub-band', 'INVALID_BAND', 'サブバンドが空、または下端が上端以上です。', '1件以上登録し、下端周波数 < 上端周波数にしてください。', 'sub_bands'),
    ('sub_bands must contain', 'BAND_IDS', '許可するサブバンドIDの指定が不正です。', '登録済みのIDを重複なく1件以上選ぶか、すべて許可に戻してください。', 'sub_bands'),
    ('channels must be unique', 'BAND_CHANNELS', 'チャネルに重複、または帯域外の周波数があります。', '下端以上・上端未満の中心周波数を、重複なく登録してください。', 'sub_bands'),
    ('outside configured sub-bands', 'FREQUENCY_OUTSIDE', '指定周波数が、どのサブバンドにも含まれていません。', '詳細の周波数と帯域の上下端を確認してください。単位はHzです。', 'sub_bands'),
    ('disabled sub-band', 'DIRECTION_BAND', '送信方向で許可されていないサブバンドが指定されました。', '詳細のUL/DL方向を確認し、許可帯域または送信・RX2周波数を修正してください。', 'sub_bands'),
    ('RX2 must follow', 'RX_WINDOWS', 'RX2の開始が、最長のRX1待受終了より後になっていません。', 'RX2待ち時間を RX1待ち時間 + 受信待ちシンボル数 × 4096 / 125000 より大きくしてください。', 'mac'),
    ('explicit positions must', 'POSITIONS_COUNT', '明示した初期座標の件数がED数と一致しません。', 'EDごとに1つの [x,y,z] 座標を設定してください。', 'groups'),
    ('assignment refers', 'ASSIGNMENT_TARGET', '割り当て条件が存在しないグループまたはEDを参照しています。', 'グループ名と0から始まるED IDを確認してください。', 'assignments'),
    ('model categories must', 'MODEL_CATEGORY', 'モデルの種類が不正です。', 'policy・mobility・timingのいずれかを指定してください。', 'models'),
    ('cyclic ', 'CYCLIC_MODEL', '再利用モデルの参照が循環しています。', '参照先をたどり、同じ定義に戻るrefを取り除いてください。', 'models'),
    (' reference:', 'MODEL_REFERENCE', '参照先のモデル定義が見つかりません。', 'ライブラリまたは埋め込み定義に同じ名前・種類のモデルを登録してください。', 'models'),
    ('LORASIM_ALLOW_PLUGINS', 'PLUGIN_DISABLED', '独自Pythonプラグインの読み込みが許可されていません。', '信頼するコードをAPIとworkerの両方に配置し、LORASIM_ALLOW_PLUGINS=1を設定して再起動してください。', 'groups'),
    ('plugin must', 'PLUGIN_INTERFACE', 'プラグインが必要なクラスまたはメソッドを公開していません。', 'docs/PLUGINS.mdの基底クラスと必須メソッドを確認してください。', 'groups'),
    ('plugin interface', 'PLUGIN_INTERFACE', 'プラグインの種類とインターフェースが一致しません。', '選択器・移動・送信タイミングに対応する基底クラスを使用してください。', 'groups'),
    ('requires observe_position', 'POSITION_REQUIRED', 'この選択モデルにはEDの位置観測が必要です。', '対象グループの「EDによる位置観測を許可」をオンにしてください。', 'groups'),
    ('requires ED position', 'POSITION_REQUIRED', 'この選択モデルにはEDの位置観測が必要です。', '対象グループの「EDによる位置観測を許可」をオンにしてください。', 'groups'),
    ('requires confirmed', 'ACK_REQUIRED', 'この選択モデルはACKによる学習を必要とします。', '対象グループの「ACKを要求する」をオンにしてください。', 'groups'),
    ('action_space SF', 'ACTION_SF', '候補SFが範囲外、または重複しています。', '7〜12のSFを重複なく指定してください。', 'action_space'),
    ('action_space channels', 'ACTION_CHANNELS', '共通行動空間のCHが重複しています。', '候補チャネルを重複なく指定してください。', 'action_space'),
    ('registered UL sub-band', 'ACTION_REGISTRATION', '共通行動空間のCHがULの中心周波数に登録されていません。', '許可ULサブバンドのチャネル一覧に登録してから選択してください。', 'action_space'),
    ('outside the common SF', 'ACTION_OUTSIDE', '選択器がEDに割り当てられた行動空間の外の送信設定を返しました。', 'SF・CH・CR・TPの全値がED側の候補集合に含まれるようにしてください。', 'action_space'),
    ('fixed SF/CH must', 'ACTION_OUTSIDE', '固定SFまたはCHが共通行動空間に含まれていません。', '候補SF・CHに含まれる組み合わせを選択してください。', 'action_space'),
    ('TP exceeds', 'TX_POWER_LIMIT', '送信電力が使用するサブバンドの上限を超えています。', '選択器の送信電力と、使用帯域の最大送信電力を確認してください。', 'groups'),
    ('payload exceeds EU DR', 'PAYLOAD_LIMIT', '選択SFに対するEU DRのペイロード上限を超えました。', 'SF10〜12は51 byte、SF9は115 byte、SF7〜8は222 byte以下にしてください。', 'groups'),
    ('timing must return', 'TIMING_RESULT', '送信タイミングモデルが不正な時刻を返しました。', 'next_timeはNoneまたは有限の将来時刻を返してください。初回は時刻0を使用できます。', 'groups'),
    ('timing preview must', 'TIMING_RESULT', '送信タイミングモデルが不正なプレビュー時刻を返しました。', 'next_timeの返値と開始時刻・間隔を確認してください。', 'groups'),
    ('cannot schedule an event', 'PAST_EVENT', '現在より過去の時刻にイベントが登録されました。', '独自モデルの返す時刻と、イベント登録処理を確認してください。', ''),
    ('mobility must return', 'MOBILITY_RESULT', '移動モデルが有効な3次元座標を返していません。', 'positionは有限の数値3つからなる [x,y,z] を返してください。', 'groups'),
    ('must be chronological', 'MOBILITY_TIME', '移動モデルが過去に戻る時刻で呼び出されました。', '呼び出し側の時刻順序とモデルの状態管理を確認してください。', 'groups'),
    ('per-query', 'MOBILITY_BUDGET', '1回の移動計算で遷移・周回数の上限を超えました。', '極端に短い滞在・遷移間隔や長い時刻ジャンプを見直してください。', 'groups'),
    ('active-air budget', 'AIR_MEMORY', '同時伝搬信号のメモリ保護上限に達しました。', '送信密度を下げるか、使用可能RAMを確認して記録設定のmax_air_bytesを増やしてください。', 'tracking'),
    ('batch_size exceeds', 'REPLAY_BATCH', 'ミニバッチ件数が経験再生容量を超えています。', 'batch_sizeをreplay_capacity以下にしてください。', ''),
    ('epsilon_end exceeds', 'EPSILON_ORDER', '最終探索率が初期探索率を超えています。', 'epsilon_endをepsilon_start以下にしてください。', ''),
    ('hidden layer size', 'HIDDEN_SIZE', '隠れ層のユニット数が範囲外です。', '各層を1〜512にしてください。', ''),
    ('start.max_s must', 'START_RANGE', '開始時刻の上限が下限より小さくなっています。', '一様乱数の上限を下限以上にしてください。', ''),
    ('jitter_s must', 'TIMING_JITTER', '周期の揺らぎが送信間隔以上です。', 'jitter_sをinterval_sより小さくしてください。', ''),
    ('speed jitter must', 'SPEED_JITTER', '移動速度の揺らぎが基本速度以上です。', '最小移動速度が正になるよう揺らぎを小さくしてください。', ''),
    ('pause_max_s must', 'PAUSE_RANGE', '静止時間の上限が下限より小さくなっています。', 'pause_max_sをpause_min_s以上にしてください。', ''),
    ('waypoint times must', 'WAYPOINT_TIME', 'ウェイポイントの時刻が不正です。', '時刻を0以上から始め、厳密に増加する順に並べてください。', ''),
    ('inside the area', 'POINT_OUTSIDE', '移動先の点が設定エリアの外にあります。', '各点のX・Y座標とエリアの幅・奥行きを確認してください。', ''),
    ('initial position outside', 'POINT_OUTSIDE', 'ランダムウォークの初期位置がエリア外です。', '初期座標のX・Yをエリア内にしてください。', ''),
    ('repeated route needs', 'ROUTE_ZERO', '繰り返し経路の移動・静止時間がすべて0です。', '異なる地点を指定するか、正の静止時間を設定してください。', ''),
    ('initial_point outside', 'MARKOV_INITIAL', 'マルコフ移動の初期点番号が存在しません。', '0から点の個数−1までの番号を指定してください。', ''),
    ('transition_matrix must', 'MARKOV_SHAPE', '遷移行列のサイズが点の個数と一致しません。', '点の個数×点の個数の正方行列を指定してください。', ''),
    ('transition probabilities', 'MARKOV_PROBABILITY', '遷移確率が非負でない、または各行の合計が1ではありません。', '各確率を0以上にし、各行の合計を1にしてください。', ''),
    ('MCU active_power', 'MCU_POWER', 'MCU動作電力が待機電力より小さくなっています。', '動作電力を待機電力以上にしてください。', 'energy'),
    ('ADR uses EU TX', 'ADR_POWER', 'ADRの送信電力がEUの2 dB刻みの設定と一致しません。', '2, 4, 6, 8, 10, 12, 14 dBmから選択してください。', ''),
    ('notification URL', 'NOTIFICATION_URL', '通知先URLの形式が不正です。', '認証情報を埋め込まないhttp://またはhttps://のURLを指定してください。', 'notifications'),
    ('model_files are CLI', 'WEB_MODEL_FILES', 'Webプロファイルから外部モデルファイルは読み込めません。', 'モデルライブラリまたはプロファイル内のmodelsに定義してください。', 'models'),
    ('profile must be a mapping', 'PROFILE_MAPPING', 'プロファイルの最上位はキーと値の組で指定してください。', 'name: experiment のようなYAMLオブジェクトにしてください。', ''),
    ('exceeds 2 MB', 'PROFILE_SIZE', '設定ファイルが2 MBの上限を超えています。', '不要な定義を除去するか再利用モデルに整理してください。', ''),
    ('unknown Experiment', 'EXPERIMENT_UNKNOWN', '選択したExperimentが見つかりません。', '一覧を再読み込みし、存在するExperimentを選択してください。', ''),
    ('invalid transmission range', 'ANALYSIS_RANGE', '送信回数の集計範囲またはビン数が不正です。', '開始を1以上、終了を開始以上、ビン数を1〜500にしてください。', ''),
    ('tracking schema v1', 'TRACKING_VERSION', 'このRunは送信回数別分析に対応していない旧記録形式です。', '現在のバージョンで再実行してください。', ''),
    ('at most 200 runs', 'ANALYSIS_LIMIT', '1回の分析の上限200 Runを超えています。', '選択するExperimentを減らしてください。', ''),
    ('output directory must be empty', 'OUTPUT_EXISTS', '出力先が空ではありません。', '既存結果を保存したうえで、新しい出力先または新しいRunを使用してください。', ''),
    ('Worker stopped', 'WORKER_STOPPED', 'worker停止後、実行を継続できませんでした。', 'workerを起動し、保存されたプロファイルから新しいRunを登録してください。途中結果は完了結果ではありません。', ''),
    ('Worker heartbeat expired', 'WORKER_HEARTBEAT', '実行プロセスの生存通知が30秒以上途絶えました。', 'workerの稼働・負荷・ログを確認してください。原因はこの通知だけでは特定できません。', ''),
    ('worker process exited', 'WORKER_EXIT', '実行プロセスが結果を保存する前に終了しました。', '終了コード、workerログ、OSのメモリ・プロセス終了ログを確認してください。原因は未確定です。', ''),
]


def known_error(message, path=''):
    for text, code, jp, hint, default_path in RULES:
        if text in message:
            return issue(code, jp, hint, path=path or default_path, detail=message)
    if any(f'unknown {k}:' in message for k in ('policy', 'mobility', 'timing', 'plugin category')):
        return issue('UNKNOWN_MODEL', '指定したモデル実装が見つかりません。', 'モデルの種類と実装名をライブラリで確認してください。', path=path, detail=message)
    return None


def field_label(path):
    return ' / '.join(f'項目{int(k)+1}' if k.isdigit() else FIELDS.get(k, (k,))[0] for k in path.split('.') if k)


def exception_diagnostics(exc, *, path=''):
    path = getattr(exc, 'diagnostic_path', path)
    if isinstance(exc, ValidationError) or hasattr(exc, 'errors'):
        results = []
        for e in exc.errors():
            loc = '.'.join(str(x) for x in e['loc'] if x != 'body')
            loc = '.'.join(x for x in (path, loc) if x)
            typ, ctx = e['type'], e.get('ctx') or {}
            known = known_error(e['msg'], loc)
            if known:
                results.append(known)
                continue
            limits = {'greater_than': ('gt','より大きい'), 'greater_than_equal': ('ge','以上の'),
                      'less_than': ('lt','より小さい'), 'less_than_equal': ('le','以下の')}
            if typ in limits:
                key, suffix = limits[typ]
                msg, hint = f'{ctx.get(key)}{suffix}値が必要です。', '入力値と単位を確認し、指定範囲に修正してください。'
            elif typ == 'missing':
                msg, hint = '必須項目が指定されていません。', 'この項目を設定してください。'
            elif typ == 'extra_forbidden':
                msg, hint = 'この設定項目は定義されていません。', 'キー名の誤記や古い設定を確認してください。モデルのOptionsに公開された項目だけが使用できます。'
            elif typ == 'literal_error' or typ == 'enum':
                msg, hint = '選択可能な値に含まれていません。', f'許可される値: {ctx.get("expected", "設定画面の候補を確認してください")}。'
            elif typ in {'too_short','too_long','string_too_short','string_too_long'}:
                msg, hint = '項目数または文字数が制約を満たしていません。', f'最小: {ctx.get("min_length", "指定なし")}、最大: {ctx.get("max_length", "指定なし")}を確認してください。'
            elif typ == 'string_pattern_mismatch':
                msg, hint = '文字列が指定の形式に一致しません。', f'許可される形式（正規表現）: {ctx.get("pattern", "モデルの仕様を確認")}。'
            elif typ == 'finite_number':
                msg, hint = '有限の数値が必要です。', 'NaNやInfinityを除き、有限の値を指定してください。'
            elif typ.startswith(('int_', 'float_', 'decimal_')):
                msg, hint = '数値の形式が正しくありません。', '整数項目には整数を、数値項目には有効な数値を入力してください。'
            elif typ == 'json_invalid':
                msg, hint = 'リクエストのJSON形式が不正です。', 'JSONの区切り・引用符を確認してください。'
            elif typ == 'value_error':
                msg, hint = '項目間の整合性チェックに失敗しました。', '技術詳細の検証条件と、モデルの設定項目の説明を確認してください。'
            else:
                msg, hint = '値の型または形式が設定仕様と一致しません。', '数値・文字列・配列・オブジェクトの形式と必須項目を確認してください。'
            results.append(issue('VALIDATION_'+typ.upper(), f'{field_label(loc) or "設定"}: {msg}', hint, path=loc, detail=e['msg']))
        return results
    if isinstance(exc, yaml.YAMLError):
        mark = getattr(exc, 'problem_mark', None)
        where = f'（{mark.line+1}行・{mark.column+1}列）' if mark else ''
        return [issue('YAML_SYNTAX', f'YAMLの構文を読み取れませんでした{where}。', 'インデント、コロン、括弧、引用符を確認してください。', path=path, detail=str(exc))]
    detail = str(exc) if isinstance(exc, str) else f'{type(exc).__name__}: {exc}'
    known = known_error(detail, path)
    if known:
        return [known]
    if isinstance(exc, MemoryError) or detail.startswith('MemoryError:'):
        return [issue('MEMORY', '処理に必要なメモリを確保できませんでした。', 'ED数、モデルのバッファ容量、並列保存数を確認し、空きRAMを確保してください。', path=path, detail=detail)]
    if isinstance(exc, OSError):
        if exc.errno == errno.ENOSPC:
            return [issue('DISK_FULL', '保存先の空き容量が不足しています。', '出力先の空きディスク容量を確保してから再実行してください。', detail=detail)]
        if isinstance(exc, PermissionError):
            return [issue('PERMISSION', 'ファイルまたはディレクトリへのアクセス権がありません。', 'API・worker実行ユーザーの読み書き権限とDockerのマウント権限を確認してください。', detail=detail)]
    if isinstance(exc, ImportError) or detail.startswith(('ModuleNotFoundError:', 'ImportError:')):
        return [issue('IMPORT', '必要なPythonモジュールを読み込めませんでした。', '詳細に示すモジュールをAPI・workerの実行環境へインストールしてください。', path=path, detail=detail)]
    return [issue('EXCEPTION', '処理中に例外が発生しました。原因の詳細は技術情報を確認してください。', '設定と技術詳細を確認してください。実験中の失敗は出力のerror.logにも記録されます。独自モデルの場合は該当メソッドを確認してください。', path=path, detail=detail)]


def validate_options(profile):
    """Validate model schemas once per declaration; never allocate ED/model state."""
    from .registry import constructor
    for section in ('groups', 'assignments'):
        for i, item in enumerate(getattr(profile, section)):
            for category in ('policy', 'timing', 'mobility'):
                spec = getattr(item, category)
                if spec is None:
                    continue
                path = f'{section}.{i}.{category}'
                location = path + '.ref'
                try:
                    resolved = profile.component(spec, category)
                    if category!='policy' and resolved.action_space is not None:
                        raise ValueError('action_space is only available for policy plugins')
                    location = path + '.kind'
                    options = getattr(constructor(category, resolved.kind), 'Options', None)
                    if options:
                        location = path + '.options'
                        options.model_validate(resolved.options)
                except Exception as exc:
                    exc.diagnostic_path = location
                    raise


def configuration_warnings(p):
    """Bounded by declarations, not transmissions. See docs/DIAGNOSTICS.md."""
    from .registry import constructor
    warnings = []
    def warn(code, path, message, hint):
        warnings.append(issue(code, message, hint, severity='warning', path=path))
    if not p.mac.duty_cycle:
        warn('DUTY_DISABLED', 'mac.duty_cycle', 'Duty cycle制約が無効です。実送信回数や衝突率が制約有効時と変わります。', '制約を含めた比較では有効にしてください。意図的な無制約実験なら、この条件を結果に明記してください。')
    if p.channel.speed_m_s != 299792458:
        warn('PROPAGATION_SPEED', 'channel.speed_m_s', f'伝搬速度が真空中の光速と異なる {p.channel.speed_m_s:g} m/sです。到着順序や受信時刻に影響します。', 'EDの移動速度との取り違えを確認してください。既定値は299792458 m/sです。')
    if p.channel.exponent < 2 or p.channel.exponent > 6:
        warn('PATH_LOSS_EXPONENT', 'channel.exponent', f'伝搬損失指数 {p.channel.exponent:g} が確認目安の2〜6から外れています。', 'この範囲はGUIの注意喚起用で、理論上の許容範囲ではありません。想定環境の測定・文献との対応を確認してください。')
    if p.channel.collision_matrix == 'aloha':
        warn('ALOHA_MATRIX', 'channel.collision_matrix', '比較用のaloha干渉行列が選ばれています。goursaudとはSF間干渉の条件が異なります。', '比較するRunの干渉モデルをそろえ、採用した行列を記録してください。')
    if not p.energy.radio.enabled:
        warn('RADIO_ENERGY_OFF', 'energy.radio.enabled', '無線の消費エネルギー計算が無効です。結果に無線消費量が計上されません。', '消費電力を比較する場合は有効にしてください。')
    for path, mcu in [('energy.mcu', p.energy.mcu)]+[(f'groups.{i}.mcu', g.mcu) for i,g in enumerate(p.groups) if g.mcu]:
        if mcu.enabled and mcu.mode == 'measured':
            warn('MCU_MEASURED', path+'.mode', 'MCU計算時間にホスト上の実測処理時間を使用します。ED実機の処理時間とは限らず、負荷・環境で結果が変わります。', '実機評価値で比較する場合はfixedまたはdeclaredを使い、時間の根拠を記録してください。')
    gw_channels = {f for g in p.gateways for f in g.frequencies_hz}
    if not gw_channels:
        warn('NO_GW_CHANNELS', 'gateways', '全GWのUL受信周波数が空です。ULを受信できません。', '少なくとも1台のGWに、使用するUL周波数を登録してください。')
    for section in ('groups', 'assignments'):
        for i, item in enumerate(getattr(p, section)):
            path = f'{section}.{i}'
            policy = item.policy
            if policy:
                spec = p.component(policy, 'policy')
                cls = constructor('policy', spec.kind)
                if cls.__module__.startswith('lorasim.plugins.'):
                    o = cls.Options.model_validate(spec.options).model_dump() if getattr(cls,'Options',None) else spec.options
                    kind = cls.__module__.rsplit('.',1)[-1]
                    bound = spec.action_space is not None or getattr(cls,'uses_action_space',False)
                    space = p.action_space.restrict(spec.action_space) if spec.action_space is not None else p.action_space
                    ignored = set(spec.options)&set(getattr(cls,'space_options',[])) if bound else set()
                    if bound and kind in {'adr','badr','gadr'}:
                        warn('ADR_COMPARISON_EXTENSION', path+'.policy.action_space', cls.space_description,
                             '原方式の再現ではなく共通条件での比較用拡張です。SF候補・CH候補・固定TP/CRを揃え、docs/ADR_COMPARISON.mdの制御則の差分を研究報告に記載してください。')
                    if kind == 'd_lora':
                        warn('DLORA_FEEDBACK', path+'.policy', 'D-LoRaの式19〜24を使用しますが、成功指標はEDが受信したACKです。論文のGW受信成否とはDL不達時に異なります。', 'ACK要求を有効にしてください。TP報酬の単位、初期探索・同点処理、既存PHYとの差分はdocs/PAPERS_V09.mdを確認してください。')
                        if o['exploration_c'] == 0:
                            warn('DLORA_NO_EXPLORATION', path+'.policy.options.exploration_c', 'D-LoRaは初回の全基底腕探索後、探索ボーナスを使用しません。', '移動・非定常環境で継続探索を意図する場合はcを正の値にしてください。')
                    if ignored:
                        warn('ACTION_OPTIONS_IGNORED', path+'.policy.options', '共通行動空間を使うためモデル側の候補設定は適用されません: '+', '.join(sorted(ignored)), '候補は全体・EDの行動空間に統一し、optionsには学習定数だけを指定してください。')
                    if bound and (kind.startswith('linucb') or kind in {'dqn','sema'}) and (len(space.coding_rates)>1 or len(space.powers_dbm)>1 or (kind=='linucb_sf' and len(space.frequencies_hz)>1)):
                        warn('ACTION_RESEARCH_EXTENSION', path+'.policy.action_space', '原論文から行動の次元を拡張しています。学習式・報酬式は元の実装を使用します。', 'LinUCBの複数TPはTP差分のSNR補正を使用します。CRに応じた新しいContextや報酬項は追加していません。論文再現と比較用拡張を区別して記録してください。')
                    if kind in {'dqn','sema'}:
                        if o['learning_rate'] > .1:
                            warn('LARGE_LEARNING_RATE', path+'.policy.options.learning_rate', f'学習率 {o["learning_rate"]:g} が注意目安0.1を超えています。大きな更新で学習が不安定になる可能性があります。', 'これは経験的な確認目安です。既定0.01を基準に損失・報酬の推移を比較してください。')
                        if o['epsilon_start'] == 0:
                            warn('NO_EXPLORATION', path+'.policy.options.epsilon_start', 'DQNのランダム探索が初期から無効です。未選択行動の評価が偏る可能性があります。', '探索を意図している場合は初期探索率を0より大きくしてください。')
                        if o['failure_reward'] >= min((o['reward_sf_offset']-sf)/o['reward_divisor'] for sf in p.action_space.spreading_factors):
                            warn('REWARD_ORDER', path+'.policy.options.failure_reward', '不達時報酬が、候補SFの少なくとも1つの成功報酬以上です。', '成功報酬と不達報酬の大小が研究目的に合っているか確認してください。')
                    if kind.startswith('linucb') and section == 'groups' and not item.observe_position:
                        warn('LINUCB_NO_POSITION', path+'.observe_position', '位置観測が無効なため、LinUCBの速度特徴量は0になります。', '移動速度をContextに用いる場合は位置観測を有効にしてください。条件割り当てで別モデルに置換する場合はその条件も確認してください。')
                    if kind.startswith('linucb') and o.get('alpha_ucb') == 0 and o.get('epsilon') == 0:
                        warn('LINUCB_NO_EXPLORATION', path+'.policy.options', 'LinUCBのUCB探索係数とランダム探索率が両方0です。', '意図した貪欲方策か確認してください。未観測行動の探索が制限されます。')
                    if kind.startswith('lora_mab') and o.get('gamma') == 1:
                        warn('EXP3_UNIFORM', path+'.policy.options.gamma', 'Exp3の探索率が1のため、学習後も全候補を等確率で選択します。', '報酬に応じた選択確率の適応を比較するなら、探索率を1未満にするか自動計算に戻してください。')
                    if kind in {'adr','gadr'} and o.get('margin_db',0) < 0:
                        warn('NEGATIVE_LINK_MARGIN', path+'.policy.options.margin_db', 'リンク余裕が負で、必要SNRを下回る方向にADR判断が変わります。', '符号や単位の取り違えでないことを確認し、意図的な条件なら記録してください。')
                    radios = [o] if 'frequency_hz' in o or 'cr' in o else list(o.get('actions', []))
                    if o.get('initial'):
                        radios.append(o['initial'])
                    if bound:
                        radios=[r.model_dump() for r in space.radios()]
                    freqs = set(o.get('frequencies_hz', []))
                    if bound:
                        freqs=set(space.frequencies_hz)
                    if not o.get('frequencies_hz') and not bound:
                        freqs.update(r.get('frequency_hz',868100000) for r in radios)
                    for r in radios:
                        if r.get('cr',1) != 1 or r.get('preamble_symbols',8) != 8 or r.get('implicit_header',False) or not r.get('crc',True):
                            warn('NONDEFAULT_RADIO', path+'.policy.options', '候補の符号化率・プリアンブル・ヘッダ・CRCに既定値と異なる設定があります。Time-on-Airが変わります。', '比較する手法で物理層条件がそろっているか確認してください。これ自体は設定違反ではありません。')
                            break
                    missing = sorted(freqs - gw_channels)
                    if missing:
                        warn('UNLISTENED_CHANNEL', path+'.policy.options', 'モデル定義の候補CHに、どのGWも受信しない周波数があります: '+', '.join(f'{f/1e6:g} MHz' for f in missing), 'GWのUL受信周波数と候補CHをそろえてください。割り当てで置換する定義なら適用条件を確認してください。')
            timing = item.timing
            if timing:
                spec = p.component(timing, 'timing')
                cls = constructor('timing', spec.kind)
                if cls.__module__ == 'lorasim.plugins.application.periodic':
                    o = cls.Options.model_validate(spec.options)
                    first = (o.start.at_s if o.start.mode == 'fixed' else o.start.min_s) if o.start else o.first_s
                    if first >= p.duration_s:
                        warn('START_AFTER_END', path+'.timing.options', f'この周期送信定義の最も早い開始時刻 {first:g} sが実験終了時刻以降です。', 'この定義を使うEDは実験中に送信できません。開始時刻または実験時間を見直してください。')
    return warnings
