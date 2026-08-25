# BLE-MIDI Controller for Raspberry Pi Pico 2W

MicroPythonで実装されたBLE-MIDIコントローラーです。8つのスイッチ、2つのエクスプレッションペダル入力、2つのトグルスイッチを持ち、WiFi経由で設定を変更できます。

Windows PC向けのBLE-MIDI受信ツール(`tools/ble_midi_receiver.py`)も同梱しています。

## リポジトリ構成

```
MIDI_Controller_BLE/
├── midi_controller_ble.py   # Pico 2W本体のファームウェア(MicroPython)
├── web_ui.html              # WiFi設定用Web UI(Picoにコピー)
├── config.json              # 動作設定(Pico上で自動生成・保存)
├── ble_midi_receiver.py     # Windows用BLE-MIDI受信ブリッジ(後述)
├── tools/
│   ├── ump_forwarder.ps1    # UMP転送用PowerShellスクリプト(受信ツールが使用)
│   └── test_ble_midi_parser.py  # BLE-MIDIパーサのユニットテスト
├── examples/                # 参考実装(adafruit_ble_midi等)
└── README.md
```

## 機能

- 8つのスイッチによるMIDI送信(Note/CC/Program Change)
- 2つのエクスプレッションペダル入力(CC/Pitch Bend)
  - ローパスフィルタ(EMA)によるノイズ除去
  - デッドゾーン設定
  - 可変ポーリングレート
- 2つの設定バンク(トグルスイッチで切り替え)
- WiFi AP経由のWeb設定インターフェース
- BLE-MIDI接続
- 設定の永続化(JSON形式)

## ハードウェア要件

- Raspberry Pi Pico 2W
- 8つのプッシュスイッチ(GPIO 10, 11, 17, 20, 12, 13, 14, 15)
- 2つのエクスプレッションペダル入力(GPIO 26/ADC0, 27/ADC1)
- 2つのトグルスイッチ(GPIO 1, 5)
- すべてのスイッチはGNDに接続(内部プルアップ使用)
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

## インストール(Pico側)

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

---

# Windows受信ツール (ble_midi_receiver.py)

Windows PCでPicoのBLE-MIDIメッセージを受信し、**Windows MIDI Services**の仮想MIDIポートへ転送するツールです。

## 仕組み

```
Pico 2W ──BLE-MIDI──> ble_midi_receiver.py ──UMP──> Default App Loopback (A)
                                                          │ (Windows MIDI Services内部)
                                    DAW <── Default App Loopback (B) をMIDI入力に選択
```

- BLE-MIDIパケットをデコード(タイムスタンプ、ランニングステータス、13ビットタイムスタンプの桁上がり=ヘッダ再挿入に対応)
- MIDI 1.0バイト列をUMP(Universal MIDI Packet)32bitワードに変換
- PowerShellモジュール経由でDefault App Loopback (A)へ常時接続し、低遅延で転送

**独自の仮想ポート作成は不要**です。Windows MIDI Servicesに標準搭載の Default App Loopback ペアを使用します。

## 要件

- Windows 10/11 + [Windows MIDI Services](https://www.microsoft.com/store/productId/9NB4VHN0GH86)(SDK & Toolsインストール済み)
- [PowerShell 7](https://learn.microsoft.com/powershell/scripting/install/installing-powershell-on-windows)(7.4以上)
- Python 3.11+ と `bleak`

```cmd
pip install bleak
```

`ble_midi_receiver.py` 内の `PWSH_PATH`(pwsh.exeのパス)が自身の環境と一致していることを確認してください。

## 使い方

```cmd
:: 自動検出(周囲のBLE-MIDIデバイスをスキャンして接続)
python ble_midi_receiver.py

:: アドレス指定
python ble_midi_receiver.py --address AA:BB:CC:DD:EE:FF

:: 生パケット表示
python ble_midi_receiver.py --verbose
```

起動すると以下が表示されます:

1. BLE-MIDIデバイスのスキャン → `Pico MIDI` を発見して接続
2. Default App Loopback (A) へのフォワーダー起動
3. MIDIメッセージのリアルタイム表示

DAW側では **「Default App Loopback (B)」** をMIDI入力に選択してください。

### 表示例

```
[14:32:05] Note On   ch1  note=60  (C4  ) vel=100  <90 3C 64>          #1 (3/s)
[14:32:06] CC        ch1  cc#7    value=64                            #2 (1/s)
[14:32:07] PitchBend ch1  value= 8192 ( 50.0%)                        #3 (1/s)
```

## トラブルシューティング(受信ツール)

| 症状 | 対処 |
|---|---|
| `No BLE-MIDI device found` | Picoの電源確認。電源投入後30秒以内に実行 |
| `Could not get GATT services: Unreachable` | Windowsの「Bluetoothとデバイス」設定に `Pico MIDI` のボンディング残骸があると接続不可。削除してからPicoを再起動 |
| `Connected but BLE-MIDI characteristic missing` | 接続先がPicoではない(BLE機器が複数ある環境)。`--address` で明示指定するか、Pico以外のBLE機器の電源を切る |
| フォワーダーが起動しない | pwshのパス確認 / Windows MIDI Servicesのサービス起動確認(`midi.exe service`) |
| DAWでメッセージが届かない | DAWのMIDI入力が **Loopback (B)** 側になっているか確認。(A)側ではなく(B)側 |

## パーサのユニットテスト

BLE-MIDIパケットデコーダの正確性(ノート、CC、ピッチベンド、バッチ、13ビットタイムスタンプの桁上がり)を検証するテストが含まれます:

```cmd
python tools\test_ble_midi_parser.py
```

---

# Picoファームウェア詳細

## WiFi設定モード

1. トグルスイッチ0(GP1)をONにするとWiFi APが起動します
2. オンボードLEDが1秒ごとに点滅します
3. スマートフォンやPCから以下のWiFiに接続:
   - SSID: `BLE-MIDI Controller`
   - Password: `testdesuyo`
4. ブラウザで `http://192.168.4.1` にアクセス

### Web設定インターフェース

- タブ切り替え:
  - **Switches**: 8つのスイッチの設定
  - **Expression**: 2つのエクスプレッションペダルの設定
  - **Common**: エクスプレッションペダル共通設定

#### Switch設定
- タイプ選択:Note / CC / Program Change

**Note設定**
- Note (0-127): 送信するノート番号
- Velocity (0-127): ベロシティ値
- Mode: Hold(押している間ON)/ Toggle(押すたびにON/OFF)

**CC設定**
- CC No (0-127): コントロールチェンジ番号
- Mode: Hold / Toggle
- Send Off Value: OFF値を送信するか
- On Value (0-127): ON時の値
- Off Value (0-127): OFF時の値
- Delay (ms): OFF値送信までの遅延時間

#### Expression Pedal設定
- タイプ選択:CC / Bend
- Min Value / Max Value(逆転設定でペダル方向を反転可能)

#### Common設定(エクスプレッションペダル共通)
- Filter (0.0-1.0): ローパスフィルタ係数(デフォルト0.1)
- Deadzone Min/Max (%): デッドゾーン(デフォルト5%)
- Send Mode: Individual(推奨)/ Batch
- Send Interval (ms): 送信間隔(デフォルト15ms)
- Msg Interval (ms): メッセージ間隔(Individualモードのみ)
- Decimation: 間引き設定

### 設定の保存

Web画面右上の「Save」ボタンで `config.json` に保存され、再起動後も保持されます。

## BLE-MIDI接続(iOS / macOS / Android)

### iOS/iPadOS
1. 設定 > Bluetooth でPico MIDIを検索・接続
2. DAWやMIDIアプリで「Pico MIDI」を選択

### macOS
Audio MIDI設定 > MIDIスタジオ > Bluetooth から接続

### Android
MIDI BLE Connect等の対応アプリを使用

> **Windowsの場合**は本リポジトリ同梱の `ble_midi_receiver.py` を推奨します(前述)。MIDIberry等の専用アプリでも接続可能ですが、本ツールは追加インストール不要でWindows MIDI Servicesネイティブの低遅延経路を使えます。

## エクスプレッションペダルのチューニング

受信機が追いつかない場合は Web UI または `config.json` で調整:

| 受信機 | Send Mode | Send Interval | Msg Interval | Decimation |
|---|---|---|---|---|
| 軽量(最新DAW等) | Individual | 15ms | 2ms | 1 |
| 中程度 | Individual | 20ms | 5ms | 2 |
| 重い(古い機材等) | Individual | 30ms | 10ms | 3 |

- **Batchモード**は低遅延ですが、一部の受信機でタイムスタンプ解釈の問題が発生する可能性があります。問題が出たらIndividualへ戻してください
- ファームウェア v最新では、Batchモードの13ビットタイムスタンプが8192を跨ぐ際にヘッダを再挿入する修正済みです(spec準拠)

## 技術仕様

- BLE MIDI Service UUID: `03B80E5A-EDE8-4B33-A751-6CE34EC4C700`
- BLE MIDI Characteristic UUID: `7772E5DB-3868-4112-A1A9-F2669D106BF3`
- WiFi: AP Mode (192.168.4.1)、Web Server Port 80
- デバウンス時間: 50ms
- ADC解像度: 16-bit (0-65535)
- エクスプレッションペダル処理:
  - 高速サンプリング: 1kHz (1ms周期)
  - 変化閾値: ADC 512カウント = MIDI CC 1ステップ
  - 適応型フィルタ: 急激な変化時は応答速度を自動的に向上
  - BLE-MIDIパケット: 最大15メッセージ/パケット、各メッセージに正確なタイムスタンプ

## 設定ファイル形式

`config.json` の構造:

```json
{
  "exp_common": {
    "filter": 0.3,
    "polling": 1,
    "deadzone_min": 5,
    "deadzone_max": 5,
    "send_mode": "individual",
    "send_interval": 15,
    "msg_interval": 2,
    "decimation": 1
  },
  "banks": [
    {
      "switches": [
        { "type": "note", "note": 60, "velocity": 100, "mode": "hold" },
        { "type": "cc", "cc": 1, "mode": "toggle", "send_off": true,
          "on_value": 127, "off_value": 0, "delay": 0 },
        { "type": "pc", "pc": 0 }
      ],
      "exp_pedals": [
        { "type": "cc", "cc": 11, "min_value": 0, "max_value": 127 },
        { "type": "bend", "min_value": 0, "max_value": 16383 }
      ]
    }
  ]
}
```

## ライセンス
examples/ を除き MIT License
