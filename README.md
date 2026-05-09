# BLE-MIDI Controller for Raspberry Pi Pico 2W

MicroPythonで実装されたBLE-MIDIコントローラーです。8つのスイッチ、2つのエクスプレッションペダル入力、2つのトグルスイッチを持ち、WiFi経由で設定を変更できます。

## 機能

- 8つのスイッチによるMIDI送信（Note/CC/Program Change）
- 2つのエクスプレッションペダル入力（CC/Pitch Bend）
  - ローパスフィルタ（EMA）によるノイズ除去
  - デッドゾーン設定
  - 可変ポーリングレート
- 2つの設定バンク（トグルスイッチで切り替え）
- WiFi AP経由のWeb設定インターフェース
- BLE-MIDI接続
- 設定の永続化（JSON形式）

## ハードウェア要件

- Raspberry Pi Pico 2W
- 8つのプッシュスイッチ（GPIO 10, 11, 17, 20, 12, 13, 14, 15）
- 2つのエクスプレッションペダル入力（GPIO 26/ADC0, 27/ADC1）
- 2つのトグルスイッチ（GPIO 1, 5）
- すべてのスイッチはGNDに接続（内部プルアップ使用）
- エクスプレッションペダルはTip=信号、Sleeve=GND

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

エクスプレッションペダル:
- Expression 1: GP26 (ADC0)
- Expression 2: GP27 (ADC1)

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
   web_ui.html
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
- タブ切り替え：
  - **Switches**: 8つのスイッチの設定
  - **Expression**: 2つのエクスプレッションペダルの設定
  - **Common**: エクスプレッションペダル共通設定

#### Switch設定
- タイプ選択：Note / CC / Program Change
- タイプに応じた詳細設定

**Note設定**
- Note (0-127): 送信するノート番号
- Velocity (0-127): ベロシティ値
- Mode: Hold（押している間ON）/ Toggle（押すたびにON/OFF）

**CC設定**
- CC No (0-127): コントロールチェンジ番号
- Mode: Hold / Toggle
- Send Off Value: OFF値を送信するか
- On Value (0-127): ON時の値
- Off Value (0-127): OFF時の値
- Delay (ms): OFF値送信までの遅延時間

**Program Change設定**
- PC No (0-127): プログラムチェンジ番号

#### Expression Pedal設定
- タイプ選択：CC / Bend

**CC設定**
- CC No (0-127): コントロールチェンジ番号
- Min Value (0-127): かかと側の値
- Max Value (0-127): つま先側の値

**Bend設定**
- Min Value (0-16383): かかと側の値
- Max Value (0-16383): つま先側の値

#### Common設定（エクスプレッションペダル共通）
- Filter (0.0-1.0): ローパスフィルタ係数（デフォルト0.1）
- Polling Interval (ms): ADC読み取り間隔（デフォルト5ms）
- Deadzone Min (%): かかと側のデッドゾーン（デフォルト5%）
- Deadzone Max (%): つま先側のデッドゾーン（デフォルト5%）

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

### Windows 10/11
**重要**: Windows 10/11では標準のBluetoothペアリングではなく、MIDI専用のアプリを使用する必要があります。

#### 方法1: MIDIberry（推奨）
1. Microsoft Storeから「MIDIberry」をインストール
2. アプリを起動
3. 「Pico MIDI」を検索して接続
4. DAWでMIDIberryの仮想MIDIポートを選択

#### 方法2: Bluetooth MIDI Connect
1. [Bluetooth MIDI Connect](https://www.microsoft.com/store/productId/9NBLGGH4R5BT)をインストール
2. アプリを起動
3. 「Pico MIDI」を検索して接続

#### 方法3: loopMIDI + BLE MIDI Driver（上級者向け）
1. [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html)をインストール
2. BLE MIDI対応ドライバをインストール
3. 仮想MIDIポートを作成して接続

**注意**: Windows標準のBluetooth設定画面からは接続できません。必ず上記のMIDI専用アプリを使用してください。

### Android
- MIDIに対応したアプリ（例：MIDI BLE Connect）を使用

## トラブルシューティング

### BLE接続できない
- Picoを再起動
- デバイス側のBluetoothをOFF/ON
- 他のBLEデバイスとの干渉を確認

### Windows 11で接続できない
**これは正常な動作です**。Windows 11の標準Bluetooth設定画面からはBLE-MIDIデバイスに接続できません。

**解決方法**:
1. MIDIberry、Bluetooth MIDI Connect等のMIDI専用アプリを使用
2. これらのアプリがBLE-MIDIプロトコルを処理し、仮想MIDIポートとして公開します
3. DAWではその仮想MIDIポートを選択します

**理由**: 
- BLE-MIDIは特殊なBLEプロファイルを使用
- Windows標準のBluetoothスタックはBLE-MIDIプロファイルに対応していない
- 専用アプリがプロトコル変換を行う必要がある

### マルチエフェクターには接続できるがPCに接続できない
- マルチエフェクター等の音楽機器はBLE-MIDIプロファイルをネイティブサポート
- PC（特にWindows）は専用アプリが必要
- これは仕様であり、デバイス側の問題ではありません

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
  "exp_common": {
    "filter": 0.1,
    "polling": 5,
    "deadzone_min": 5,
    "deadzone_max": 5
  },
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
      ],
      "exp_pedals": [
        {
          "type": "cc",
          "cc": 11,
          "min_value": 0,
          "max_value": 127
        },
        {
          "type": "bend",
          "min_value": 0,
          "max_value": 16383
        }
      ]
    },
    {
      "switches": [ /* Bank 1の設定 */ ],
      "exp_pedals": [ /* Bank 1のエクスプレッションペダル設定 */ ]
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
- ADC解像度: 16-bit (0-65535)
- エクスプレッションペダル処理:
  - デッドゾーン適用 → EMAフィルタ → スケーリング
  - 変化閾値: 設定範囲の1%以上で送信（最小1）

## エクスプレッションペダルについて

### 接続方法
- ステレオジャック（TRS）を使用
- Tip: 信号（ADC入力）
- Ring: 未使用
- Sleeve: GND

### ノイズ対策
- **EMAフィルタ**: 指数移動平均フィルタでノイズを除去
  - Filter値が小さいほど滑らか（遅延大）
  - Filter値が大きいほど応答速度が速い（ノイズ多）
- **デッドゾーン**: ペダルの端での不安定な値を除外
- **変化閾値**: 設定範囲（Max-Min）の1%未満の変化では送信しない
  - 例: CC 0-127の場合、約1.27以上の変化で送信
  - 例: Bend 0-16383の場合、約164以上の変化で送信
  - MIDI負荷を軽減し、安定した動作を実現

### 推奨設定
- 一般的な用途: Filter=0.1, Polling=5ms, Deadzone=5%
- 高速応答: Filter=0.3, Polling=3ms, Deadzone=3%
- 滑らか重視: Filter=0.05, Polling=10ms, Deadzone=7%

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
