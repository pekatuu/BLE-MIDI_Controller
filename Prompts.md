# MicroPython Pico2W用BLE-MIDIコントローラー

## 要件

- ハードウェア: Raspberry Pi Pico 2 W
- 開発環境: MicroPython（CircuitPythonではない）
- 8つのスイッチと2つのトグルスイッチを持つBLE-MIDIデバイス
    - SWITCH_PINS = [GP10, GP11, GP17,GP20, GP12, GP13, GP14, GP15]
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

- 2つのトグルスイッチそれぞれは以下の機能を持つ
    1. WiFi ON/OFF
        - WiFiおよびHTTPサーバの有効無効を切り替える
        - WiFi有効中はオンボードLEDが1秒おきに点滅
    2. 設定バンクの切り替え
        - トグルスイッチ2に応じて2つの設定バンクを切り替える

- 設定はWifi経由のWebサーバーで行う
    - 設定はトグル
    - スマートフォン用に最低限のCSS/JSで設計
    - 設定のWebアプリでNote/CC/ProgramChangeいずれかを変更した場合、動的に設定項目の入力欄を切り替えてください。現状は切り替えのものが残ります
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
