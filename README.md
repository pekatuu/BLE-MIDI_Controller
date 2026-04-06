# BLE-MIDI Controller for Raspberry Pi Pico 2W

MicroPythonで実装されたBLE-MIDIコントローラーです。8つのスイッチと2つのトグルスイッチを持ち、WiFi経由で設定を変更できます。

## 機能

- 8つのスイッチによるMIDI送信（Note/CC/Program Change）
- 2つの設定バンク（トグルスイッチで切り替え）
- WiFi AP経由のWeb設定インターフェース
- BLE-MIDI接続
- 設定の永続化（JSON形式）

## ハードウェア要件

- Raspberry Pi Pico 2W
- 8つのプッシュスイッチ（GPIO 10, 11, 17, 20, 12, 13, 14, 15）
- 2つのトグルスイッチ（GPIO 1, 5）
- すべてのスイッチはGNDに接続（内部プルアップ使用）

## GPIO配置

```
スイッチ:
- Switch 0: GP10
- Switch 1: GP11
- Switch 2: GP17
- Switch 3: GP20
- Switch 4: GP12
- Switch 5: GP13
- Switch 6: GP14
- Switch 7: GP15

トグルスイッチ:
- Toggle 0 (WiFi ON/OFF): GP1
- Toggle 1 (Bank Select): GP5
```

## インストール

1. MicroPythonファームウェアをPico 2Wにインストール
   - [MicroPython公式サイト](https://micropython.org/download/RPI_PICO2/)から最新版をダウンロード
   - Picoをブートモードで接続し、UF2ファイルをコピー

2. 必要なファイルをPicoにコピー
   ```
   midi_controller_ble.py
   ```

3. Picoを再起動

## 使用方法

### 起動

Picoに電源を投入すると自動的にBLE-MIDIデバイスとして起動します。

```
デバイス名: Pico MIDI
```

### WiFi設定モード

1. トグルスイッチ0（GP1）をONにするとWiFi APが起動します
2. オンボードLEDが1秒ごとに点滅します
3. スマートフォンやPCから以下のWiFiに接続：
   - SSID: `PicoMIDI`
   - Password: `midi1234`
4. ブラウザで `http://192.168.4.1` にアクセス

### Web設定インターフェース

- バンク選択：Bank 0 / Bank 1を切り替え
- 各スイッチの設定：
  - タイプ選択：Note / CC / Program Change
  - タイプに応じた詳細設定

#### Note設定
- Note (0-127): 送信するノート番号
- Velocity (0-127): ベロシティ値
- Mode: Hold（押している間ON）/ Toggle（押すたびにON/OFF）

#### CC設定
- CC No (0-127): コントロールチェンジ番号
- Mode: Hold / Toggle
- Send Off Value: OFF値を送信するか
- On Value (0-127): ON時の値
- Off Value (0-127): OFF時の値
- Delay (ms): OFF値送信までの遅延時間

#### Program Change設定
- PC No (0-127): プログラムチェンジ番号

### 設定の保存

1. Web画面で設定を変更
2. 右上の「Save」ボタンをクリック
3. 成功メッセージが表示されます
4. 設定は `config.json` に保存され、再起動後も保持されます

## BLE-MIDI接続

### iOS/iPadOS
1. 設定 > Bluetooth でPico MIDIを検索
2. ペアリング
3. DAWやMIDIアプリで「Pico MIDI」を選択

### macOS
1. Audio MIDI設定を開く
2. MIDIスタジオでBluetooth設定
3. Pico MIDIを接続

### Windows
1. Bluetooth設定でPico MIDIをペアリング
2. DAWでBLE-MIDIデバイスとして認識

### Android
- MIDIに対応したアプリ（例：MIDI BLE Connect）を使用

## トラブルシューティング

### BLE接続できない
- Picoを再起動
- デバイス側のBluetoothをOFF/ON
- 他のBLEデバイスとの干渉を確認

### WiFiに接続できない
- トグルスイッチ0がONになっているか確認
- LEDが点滅しているか確認
- WiFi設定をリセット（SSID/パスワードを確認）

### 設定が保存されない
- config.jsonファイルの書き込み権限を確認
- シリアルコンソールでエラーメッセージを確認

## 設定ファイル形式

`config.json` の構造：

```json
{
  "banks": [
    {
      "switches": [
        {
          "type": "note",
          "note": 60,
          "velocity": 100,
          "mode": "hold"
        },
        {
          "type": "cc",
          "cc": 1,
          "mode": "toggle",
          "send_off": true,
          "on_value": 127,
          "off_value": 0,
          "delay": 0
        },
        {
          "type": "pc",
          "pc": 0
        }
      ]
    },
    {
      "switches": [ /* Bank 1の設定 */ ]
    }
  ]
}
```

## 技術仕様

- BLE MIDI Service UUID: `03B80E5A-EDE8-4B33-A751-6CE34EC4C700`
- BLE MIDI Characteristic UUID: `7772E5DB-3868-4112-A1A9-F2669D106BF3`
- WiFi: AP Mode (192.168.4.1)
- Web Server: Port 80
- デバウンス時間: 50ms

## 制限事項

- WiFiとBLEの同時使用により、パフォーマンスが若干低下する可能性があります
- BLE-MIDIの遅延は通常10-20ms程度です
- 同時に接続できるBLEデバイスは1台のみです

## ライセンス

MIT License

## 参考

- [MicroPython公式ドキュメント](https://docs.micropython.org/)
- [BLE-MIDI仕様](https://www.midi.org/specifications/midi-transports-specifications/bluetooth-le-midi)
- [Raspberry Pi Pico 2W](https://www.raspberrypi.com/documentation/microcontrollers/raspberry-pi-pico.html)
