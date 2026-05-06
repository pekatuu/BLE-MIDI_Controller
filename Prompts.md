# MicroPython Pico2W用BLE-MIDIコントローラー

## 要件

- ハードウェア: Raspberry Pi Pico 2 W
- 開発環境: MicroPython（CircuitPythonではない）
- 8つのスイッチと2つのエクスプレッションペダル接続用ステレオジャック、2つのトグルスイッチを持つBLE-MIDIデバイス
    - SWITCH_PINS = [GP10, GP11, GP17,GP20, GP12, GP13, GP14, GP15]
    - EXP_PINS = [GP26 (ADC0), GP27 (ADC1)]
    - TOGGLE_PINS = [GP1, GP5]
- 8つのスイッチそれぞれは以下のいずれかの機能を設定できる
    1. Noteの送出
        - 設定項目
            - Note[0-127]
            - Velocity[0-127]
            - toggle/hold[toggle|hold]

    2. CCの送出
        - 設定項目
            - CC No[0-127].
            - toggle/hold[toggle|hold]
            - Send off value[bool]: 
            - On value[0-127]: ボタン有効時の送出値
            - Off value[0-127]: Send off valueが真のときのみ有効。ボタン無効化時に送出する値
            - Delay (ms) [int]: holdかつSend off valueが真のときのみ有効。スイッチのOff後、実際のボタンのOffの後、Off valueの送出までのディレイ

    3. ProgramChangeの送出
        - 設定項目
            - PC No.

- 2つのエクスプレッションペダル接続用ステレオジャックは以下のいずれかの機能を設定でき、また共通設定を持つ
    - 共通設定: ノイズ対策やエクスプレッションペダルの特性を吸収するための共通機能の設定を行う。
        - ノイズ対策でローパスフィルタ（EMA：指数移動平均）をソフトウェアで実装する
        - ADCのポーリングインターバルを実装する
        - 遊び（デッドゾーン）を実装する
        - 負荷軽減のためエクスプレッションペダルの変化がCC/Bendにおいて+-1以上の時にCC/Bendを送出するようにする
        - デッドゾーン適用 → フィルタ適用 → スケーリング（Min/Max変換）の順で適用する
        - 設定項目
            - Filter[0.0-1.0]: ローパスフィルタのデフォルト0.1
            - Polling[0-100ms]: 初期値5ms。ADCのポーリングインターバル
            - Deadzone min[0-100%]: エクスプレッションペダルのかかと側のデッドゾーン。デフォルト5%
            - Deadzone max[0-100%]: エクスプレッションペダルのつま先側のデッドゾーン。デフォルト5%

    - 機能1. CCの送出
        - 送出するCCのMin Maxの値を設定できる。Min > Maxは許容する
        - 設定項目
            - CC No[0-127]
            - Min value[0-127]: 送出するCCの最小値。エクスプレッションペダルのかかと側を踏み込んだ時に送出する値
            - Max value[0-127]: 送出するCCの最小値。エクスプレッションペダルのつま先側を踏み込んだ時に送出する値
    - 機能2. Bendの送出
        - 送出するBendのMin Maxの値を設定できる。Min > Maxは許容する
        - 設定項目
            - Min value[0-16383]: 初期値8192。送出するCCの最小値。エクスプレッションペダルのかかと側を踏み込んだ時に送出する値
            - Max value[0-16383]: 初期値16383。送出するCCの最小値。エクスプレッションペダルのつま先側を踏み込んだ時に送出する値
    

- 2つのトグルスイッチそれぞれは以下の機能を持つ
    1. WiFi ON/OFF
        - WiFiおよびHTTPサーバの有効無効を切り替える
        - WiFi有効中はオンボードLEDが1秒おきに点滅
    2. 設定バンクの切り替え
        - トグルスイッチ2に応じて2つの設定バンクを切り替える

- 設定はWifi経由のWebサーバーで行う
    - 設定はトグル
    - スマートフォン用に最低限のCSS/JSで設計
    - 設定のWebアプリでNote/CC/ProgramChangeいずれかを変更した場合、動的に設定項目の入力欄を切り替えてください
    - Note/CC/Programchangeの切り替えはラジオボタン
    - アプリヘッダはStickey
    - アプリヘッダの右端にSaveボタンで設定保存
    - 設定保存は画面遷移せず、保存成否を表示
     
- シリアルポートに適宜ログを出すこと
- Wifi有効中は1秒ごとに内臓LEDを点滅させてください
- Wifi接続NGの場合であっても起動するようにしてください
- README.mdを適宜メンテナンスしてください


## ルール

### ディレクトリ構成
```
midi_controller_ble.py     # メインコード
lib/        # ライブラリ
examples/   # 参考ファイル
README.md 
```

## 参考ファイル

[examples](./examples) フォルダに参考になると想定される実装を配置しています。
- adafruit_ble_midi.py: MicroPythonではBLE-MIDIライブラリ不在の想定であり、参考としてCircuitPythonのadafruitデバイス用BLE-MIDI実装を配置しています

## Prompt1

[ルール](#ルール) に従い [要件](#要件) を確認し、実現可能であれば実装してください。
実装にあたっては [examples](./examples) フォルダに参考になると想定される実装を配置しています。
特にBLEとWiFiの併用が可能か未検証です。
