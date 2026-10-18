"""Human-readable profile field documentation shared by the schema API and UI."""
from copy import deepcopy
FIELDS = {
'name':('名前','実験・グループ・再利用モデルを区別する表示名です。グループ名は空欄なら自動採番します。'),
'id':('識別子（自動採番）','空欄なら既存IDと重複しない名前を自動生成します。明示したIDは維持します。'),
'seed':('乱数シード','同じ設定とシードで乱数列を再現します。各EDと各モデルには独立した乱数系列を渡します。'),
'duration_s':('シミュレーション時間 [s]','仮想時刻の終了時刻です。実際の処理時間ではありません。終了時の未確定パケットは別集計です。'),
'area_m':('エリアの幅・奥行き [m]','x方向・y方向の長さ。初期配置および移動境界に用います。'),
'channel':('伝搬・干渉','受信電力、伝搬遅延、SF間干渉に用いる物理モデルです。'),
'mac':('Class A MAC','受信ウィンドウ、RX2、Duty cycleの設定です。'),
'sub_bands':('サブバンドとチャネル','周波数範囲・送信電力上限・Duty cycle・GUIに表示するチャネル中心周波数を定義します。'),
'ul_sub_bands':('ULで許可するサブバンド','未設定は全サブバンド。選択モデルが許可外周波数を返した場合、実行をエラーで停止します。'),
'dl_sub_bands':('DLで許可するサブバンド','未設定は全サブバンド。RX1が許可外ならDL送信を見送りRX2へ進みます。RX2は許可内でなければなりません。'),
'low_hz':('下端周波数 [Hz]','この周波数を含みます。サブバンド同士の範囲は重複できません。'),
'high_hz':('上端周波数 [Hz]','この周波数自体は含みません。下端より大きく指定してください。'),
'duty_cycle':('Duty cycle','MACでは制約の有効・無効。サブバンドでは送信時間の割合（例：0.01＝1%）を指定します。'),
'max_tp_dbm':('最大送信電力 [dBm]','サブバンドの送信電力上限。ULはこれを超える設定を拒否し、DLはこの電力で送信します。'),
'channels_hz':('チャネル中心周波数 [Hz]','周波数プルダウンの候補を登録します。サブバンド範囲内で重複しない値を指定してください。無線モデルは連続するサブバンド範囲も受け付けます。'),
'gateways':('ゲートウェイ','固定位置、UL受信周波数、同時復調数を指定します。IDは自動採番できます。'),
'groups':('EDグループ','台数・配置・3種類のモデルをまとめて設定します。割り当て条件では対象のグループ名を選択します。'),
'assignments':('条件によるモデル割り当て','グループ、ED ID、タグの条件をANDで適用。空条件は全件、複数の割り当ては後の指定を優先します。'),
'execution':('実行方式・高速化','因果関係を保つ高速化と、試験的なマルチプロセス保存の設定です。'),
 'tracking':('記録とメモリ上限','Parquetのバッチサイズと同時伝搬信号のメモリ上限です。'),
'notifications':('進捗Webhook','指定の進捗間隔でHTTP POSTします。シミュレーションのイベント処理とは別スレッドです。'),
'channel_backend':('伝搬計算バックエンド','numpyはベクトル演算、scalarは比較検証向けの逐次演算です。'),
'energy':('消費エネルギー','無線状態モデルと、別計上のMCU計算モデルを設定します。'),
'models':('埋め込みモデル定義','プロファイル内の名前付きモデル。ライブラリの同名定義より優先し、実行時に固定されます。'),
'policy':('パラメータ選択モデル','各送信でSF、送信電力、符号化率、周波数などを選択します。'),
'mobility':('移動モデル','シミュレーション時刻に対するEDの座標を返します。'),
 'timing':('送信タイミングモデル','次のアプリケーションメッセージ生成時刻を返します。MAC制約によって実送信は遅れる場合があります。'),
'count':('ED数','グループ内の端末数。ED IDはグループ順・端末順に0から自動採番します。'),
'placement':('初期配置方法','uniform：面内の一様乱数、grid：格子、explicit：各EDの座標を明示します。'),
'positions':('初期座標の一覧 [m]','explicit配置の各EDの [x,y,z] 座標。ED数と同じ件数が必要です。'),
'position':('座標 [x,y,z] [m]','シミュレーション座標系での位置と高さです。'),
'payload_bytes':('アプリケーションペイロード [byte]','LoRaWANヘッダを含まない長さ。SFごとのEU DR上限も送信時に検証します。'),
'confirmed':('ACKを要求する','有効ならConfirmed ULとしてDLのACKを要求します。無効時のACK観測値はnullです。'),
'max_attempts':('最大送信試行数','初回を含む最大回数。ACK/DL受信時または上限に達すると次のアプリケーション生成へ進みます。'),
'observe_position':('EDによる位置観測を許可','有効な場合のみ選択モデルのObservationに現在位置を渡します。'),
'tags':('タグ','割り当て条件の照合に用いる自由な文字列です。'),
'ids':('対象EDのID','0から始まるED ID。未指定はすべてのEDが対象です。'),
'radio':('無線の電力モデル','LoRaRadioEnergyModelに対応する状態別電流と電圧です。'),
'mcu':('MCUの電力モデル','推論・学習の計算時間から無線とは別に消費エネルギーを計上します。グループの未設定は全体設定を継承します。'),
'enabled':('有効にする','この機能の計算または記録を有効にします。'),
'voltage_v':('駆動電圧 [V]','無線状態の電流に掛けて消費電力を求める電圧です。'),
'initial_state':('無線の初期状態','時刻0から最初の状態遷移までSTANDBYまたはSLEEP電流を使います。'),
'sleep_current_a':('スリープ電流 [A]','SLEEP状態にある間の電流です。'),
'standby_current_a':('待機電流 [A]','STANDBY状態の電流。受信ウィンドウで信号を待つ状態にも適用します。'),
'rx_current_a':('受信電流 [A]','RX_ACTIVEでパケットを受信中の電流です。'),
 'tx_current_model':('送信電流モデル','constantは一定電流、linearは出力電力・効率・送信電圧から電流を求めます。'),
 'tx_current_a':('一定送信電流 [A]','constantモデルでTX状態に適用する電流です。'),
 'tx_voltage_v':('送信増幅器の電圧 [V]','linear送信電流モデルでRF出力電力を電流へ変換する電圧です。'),
 'eta':('送信増幅器の効率','linearモデルの効率（0より大きく1以下）。'),
 'base_current_a':('送信回路の基礎電流 [A]','linearモデルでRF出力によらず加算する電流です。'),
 'mode':('計算方式','fixed：設定時間、declared：モデル申告時間、measured：ホスト上の実測時間。measuredの結果は環境に依存します。'),
 'active_power_w':('MCU動作電力 [W]','推論・学習中の電力。idleとの差分を計算時間に掛け、idle電力は時間全体に計上します。'),
 'idle_power_w':('MCU待機電力 [W]','全シミュレーション時間にわたり基礎電力として計上します。'),
 'inference_s':('推論時間 [s]','fixedモードで1回のselect呼び出しに帰属させる時間です。'),
 'training_s':('学習時間 [s]','fixedモードで1回のobserve呼び出しに帰属させる時間です。'),
 'sf':('拡散率（SF）','7〜12。大きいほど通信時間が長くなり、受信感度が改善します。'),
 'tp_dbm':('送信電力 [dBm]','アンテナからのRF出力。サブバンド上限以下にしてください。'),
 'cr':('符号化率の指定値','1,2,3,4はそれぞれ4/5,4/6,4/7,4/8に対応します。'),
 'frequency_hz':('送信周波数 [Hz]','UL許可サブバンドに登録したチャネルから選択できます。'),
 'bandwidth_hz':('帯域幅 [Hz]','現バージョンはEU868の125 kHzを対象とします。'),
 'preamble_symbols':('プリアンブル長 [symbol]','LoRa Time-on-Airの計算に用いるプリアンブルシンボル数です。'),
 'implicit_header':('暗黙ヘッダを使用','有効時は明示ヘッダを省くTime-on-Air計算になります。'),
 'crc':('CRCを付与','ペイロードCRCに対応する送信時間を計上します。'),
 'demodulators':('同時復調数','GWで同時にロック可能なUL信号数です。'),
 'frequencies_hz':('GWのUL受信周波数 [Hz]','GWが受信できるチャネル中心周波数の一覧です。'),
 'exponent':('伝搬損失指数','対数距離モデルの距離に対する減衰率です。'),
 'reference_distance_m':('基準距離 [m]','対数距離モデルの基準距離です。'),
 'reference_loss_db':('基準伝搬損失 [dB]','基準距離における伝搬損失です。'),
 'speed_m_s':('伝搬速度 [m/s]','距離から信号の到着遅延を計算します。移動モデルの同名項目はEDの移動速度です。'),
 'collision_matrix':('SF間干渉モデル','goursaudはSF間捕捉閾値行列、alohaは比較用の衝突モデルです。'),
 'rx1_delay_s':('RX1待ち時間 [s]','UL送信終了から第1受信ウィンドウを開くまでの時間です。'),
 'rx2_delay_s':('RX2待ち時間 [s]','UL送信終了から第2受信ウィンドウを開くまでの時間です。'),
 'rx_symbols':('受信待ちシンボル数','信号が届かないときに受信ウィンドウを閉じるまでのシンボル数です。'),
 'rx1_sf_offset':('RX1のSFオフセット','ULのSFに加算してRX1のSFを求めます（最大12）。'),
 'rx2_sf':('RX2の拡散率（SF）','第2受信ウィンドウで用いるSFです。'),
 'rx2_frequency_hz':('RX2の受信周波数 [Hz]','DL許可サブバンドに登録したチャネルから選択します。'),
 'backhaul_delay_s':('バックホール遅延 [s]','GW側の受信完了からDLスケジューリングに加える遅延です。'),
 'batch_rows':('記録バッチの行数','各追跡テーブルがこの行数に達するとParquetへ書き出します。'),
 'max_air_signals':('同時伝搬信号数の上限','上限に達したらメモリ枯渇を避けるためエラーで停止します。'),
 'max_air_bytes':('伝搬バッファの上限 [byte]','同時信号に関連する配列の推定メモリ量の上限です。'),
 'url':('通知先URL','進捗をJSONとしてPOSTするHTTP(S)のURLです。'),
 'every_percent':('通知する進捗間隔 [%]','この進捗差に達するごとに通知します。終了・失敗は別途通知します。'),
 'timeout_s':('通知のタイムアウト [s]','curl送信の最大待ち時間です。'),
 'options':('モデルの定数','モデル自身が公開した型・既定値・制約に従って設定します。参照モデルでは必要な値だけ上書きできます。'),
 'kind':('実装クラス','組み込み名またはPythonの module:Class を指定します。'),
 'ref':('再利用定義の参照','ライブラリまたはプロファイルに保存した名前付き設定です。'),
 'schema_version':('プロファイル形式','プロファイルの互換性識別番号です。'),
 'model_files':('外部モデル定義ファイル','CLIで読み込む相対YAMLパスです。Webではライブラリを使用します。'),
}


def describe_schema(value):
    result = deepcopy(value)
    def walk(node):
        if not isinstance(node,dict): return
        for key,field in node.get('properties',{}).items():
            if key in FIELDS:
                title,desc=FIELDS[key]
                if not field.get('description'):
                    field['title'],field['description']=title,desc
            else:
                field.setdefault('description',f'{field.get("title",key)} の設定値です。')
        for child in node.values():
            if isinstance(child,dict): walk(child)
            elif isinstance(child,list):
                for item in child: walk(item)
    walk(result)
    return result
