"""
BLE-MIDI Controller for Raspberry Pi Pico 2W
MicroPython implementation
"""

import asyncio
import json
import time
import machine
import network
from machine import Pin
import aioble
import bluetooth
import struct

# GPIO Configuration
SWITCH_PINS = [10, 11, 17, 20, 12, 13, 14, 15]
TOGGLE_PINS = [1, 5]  # [WiFi ON/OFF, Bank Select]

# BLE MIDI UUIDs
MIDI_SERVICE_UUID = bluetooth.UUID("03B80E5A-EDE8-4B33-A751-6CE34EC4C700")
MIDI_CHAR_UUID = bluetooth.UUID("7772E5DB-3868-4112-A1A9-F2669D106BF3")

# Configuration
CONFIG_FILE = "config.json"
WIFI_SSID = "BLE-MIDI Controller"
WIFI_PASSWORD = "testdesuyo"

class MIDIController:
    def __init__(self):
        self.led = Pin("LED", Pin.OUT)
        self.switches = [Pin(pin, Pin.IN, Pin.PULL_UP) for pin in SWITCH_PINS]
        self.toggles = [Pin(pin, Pin.IN, Pin.PULL_UP) for pin in TOGGLE_PINS]
        
        self.switch_states = [False] * len(SWITCH_PINS)
        self.last_switch_time = [0] * len(SWITCH_PINS)
        self.debounce_ms = 50
        
        self.wifi_enabled = False
        self.current_bank = 0
        
        self.config = self.load_config()
        self.ble_connection = None
        self.midi_characteristic = None
        
        self.wlan = None
        self.web_server_task = None
        
        print("MIDI Controller initialized")
    
    def load_config(self):
        """Load configuration from file"""
        try:
            with open(CONFIG_FILE, 'r') as f:
                config = json.load(f)
                print("Configuration loaded")
                return config
        except:
            print("No config found, using defaults")
            return self.default_config()
    
    def default_config(self):
        """Create default configuration"""
        config = {
            "banks": [
                {
                    "switches": [
                        {
                            "type": "note",
                            "note": 60 + i,
                            "velocity": 100,
                            "mode": "hold"
                        } for i in range(8)
                    ]
                },
                {
                    "switches": [
                        {
                            "type": "cc",
                            "cc": 58 + i,
                            "mode": "toggle",
                            "send_off": True,
                            "on_value": 127,
                            "off_value": 0,
                            "delay": 0
                        } for i in range(8)
                    ]
                }
            ]
        }
        self.save_config(config)
        return config
    
    def save_config(self, config=None):
        """Save configuration to file"""
        if config is None:
            config = self.config
        try:
            with open(CONFIG_FILE, 'w') as f:
                json.dump(config, f)
            print("Configuration saved")
            return True
        except Exception as e:
            print(f"Failed to save config: {e}")
            return False
    
    def get_timestamp(self):
        """Get BLE MIDI timestamp"""
        ms = time.ticks_ms() & 0x1FFF
        header = 0x80 | ((ms >> 7) & 0x3F)
        timestamp = 0x80 | (ms & 0x7F)
        return bytes([header, timestamp])
    
    async def send_midi(self, data):
        """Send MIDI message via BLE"""
        if self.ble_connection and self.midi_characteristic:
            try:
                timestamp = self.get_timestamp()
                message = timestamp + bytes(data)
                self.midi_characteristic.write(message, send_update=True)
                print(f"MIDI sent: {[hex(b) for b in data]}")
            except Exception as e:
                print(f"MIDI send error: {e}")
    
    async def handle_switch(self, switch_idx):
        """Handle switch press/release"""
        bank_config = self.config["banks"][self.current_bank]["switches"][switch_idx]
        switch_type = bank_config["type"]
        
        if switch_type == "note":
            note = bank_config["note"]
            velocity = bank_config["velocity"]
            mode = bank_config["mode"]
            
            if self.switch_states[switch_idx]:  # Pressed
                await self.send_midi([0x90, note, velocity])  # Note On
            else:  # Released
                if mode == "hold":
                    await self.send_midi([0x80, note, 0])  # Note Off
                # toggle mode: note stays on until next press
        
        elif switch_type == "cc":
            cc = bank_config["cc"]
            mode = bank_config["mode"]
            send_off = bank_config.get("send_off", False)
            on_value = bank_config.get("on_value", 127)
            off_value = bank_config.get("off_value", 0)
            delay = bank_config.get("delay", 0)
            
            if mode == "hold":
                # Hold mode: send on_value when pressed, off_value when released
                if self.switch_states[switch_idx]:  # Pressed
                    await self.send_midi([0xB0, cc, on_value])
                    print(f"CC {cc}: ON ({on_value})")
                else:  # Released
                    if send_off:
                        if delay > 0:
                            await asyncio.sleep_ms(delay)
                        await self.send_midi([0xB0, cc, off_value])
                        print(f"CC {cc}: OFF ({off_value}) after {delay}ms")
            
            elif mode == "toggle":
                # Toggle mode: alternate between on_value and off_value on each press
                if self.switch_states[switch_idx]:  # Only on press
                    # Check current state (we need to track this)
                    if not hasattr(self, 'cc_toggle_states'):
                        self.cc_toggle_states = {}
                    
                    key = f"{self.current_bank}_{switch_idx}"
                    current_state = self.cc_toggle_states.get(key, False)
                    
                    if current_state:
                        # Currently ON, send OFF
                        if send_off:
                            await self.send_midi([0xB0, cc, off_value])
                            print(f"CC {cc}: Toggle OFF ({off_value})")
                        self.cc_toggle_states[key] = False
                    else:
                        # Currently OFF, send ON
                        await self.send_midi([0xB0, cc, on_value])
                        print(f"CC {cc}: Toggle ON ({on_value})")
                        self.cc_toggle_states[key] = True
        
        elif switch_type == "pc":
            pc = bank_config["pc"]
            if self.switch_states[switch_idx]:  # Only on press
                await self.send_midi([0xC0, pc])  # Program Change
                print(f"PC: {pc}")
    
    async def scan_switches(self):
        """Scan switches for state changes"""
        while True:
            current_time = time.ticks_ms()
            
            for i, switch in enumerate(self.switches):
                if time.ticks_diff(current_time, self.last_switch_time[i]) > self.debounce_ms:
                    new_state = not switch.value()  # Inverted (pull-up)
                    
                    if new_state != self.switch_states[i]:
                        self.switch_states[i] = new_state
                        self.last_switch_time[i] = current_time
                        await self.handle_switch(i)
            
            await asyncio.sleep_ms(10)
    
    async def scan_toggles(self):
        """Scan toggle switches"""
        last_wifi_state = self.toggles[0].value()
        last_bank_state = self.toggles[1].value()
        
        while True:
            # WiFi toggle
            wifi_state = self.toggles[0].value()
            if wifi_state != last_wifi_state:
                last_wifi_state = wifi_state
                self.wifi_enabled = not wifi_state  # Inverted
                print(f"WiFi {'enabled' if self.wifi_enabled else 'disabled'}")
                
                if self.wifi_enabled:
                    asyncio.create_task(self.start_wifi())
                else:
                    await self.stop_wifi()
            
            # Bank toggle
            bank_state = self.toggles[1].value()
            if bank_state != last_bank_state:
                last_bank_state = bank_state
                self.current_bank = 0 if bank_state else 1
                print(f"Bank switched to {self.current_bank}")
            
            await asyncio.sleep_ms(100)
    
    async def blink_led(self):
        """Blink LED when WiFi is enabled"""
        while True:
            if self.wifi_enabled and self.wlan and self.wlan.isconnected():
                self.led.toggle()
                await asyncio.sleep(1)
            else:
                self.led.off()
                await asyncio.sleep(0.1)
    
    async def start_wifi(self):
        """Start WiFi AP and web server"""
        if self.wlan is None:
            self.wlan = network.WLAN(network.AP_IF)
        
        try:
            self.wlan.config(essid=WIFI_SSID, password=WIFI_PASSWORD)
            self.wlan.active(True)
            
            # Wait for AP to be active
            for _ in range(10):
                if self.wlan.active():
                    break
                await asyncio.sleep(0.5)
            
            if self.wlan.active():
                print(f"WiFi AP started: {WIFI_SSID}")
                print(f"IP: {self.wlan.ifconfig()[0]}")
                
                if self.web_server_task is None:
                    self.web_server_task = asyncio.create_task(self.web_server())
            else:
                print("Failed to start WiFi AP")
        except Exception as e:
            print(f"WiFi start error: {e}")
    
    async def stop_wifi(self):
        """Stop WiFi and web server"""
        if self.web_server_task:
            self.web_server_task.cancel()
            self.web_server_task = None
        
        if self.wlan:
            self.wlan.active(False)
            print("WiFi stopped")

    
    async def web_server(self):
        """Simple web server for configuration"""
        import socket
        
        try:
            addr = socket.getaddrinfo('0.0.0.0', 80)[0][-1]
            s = socket.socket()
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(addr)
            s.listen(1)
            s.setblocking(False)
            
            print("Web server started on port 80")
            
            while self.wifi_enabled:
                try:
                    await asyncio.sleep(0.1)
                    
                    try:
                        cl, addr = s.accept()
                        print(f"Client connected from {addr}")
                        asyncio.create_task(self.handle_client(cl))
                    except OSError:
                        pass
                
                except asyncio.CancelledError:
                    break
            
            s.close()
        except Exception as e:
            print(f"Web server error: {e}")
    
    async def handle_client(self, cl):
        """Handle HTTP client request"""
        try:
            cl.setblocking(False)
            request = b""
            timeout = 100  # 10 seconds timeout
            
            # Read request with timeout
            for _ in range(timeout):
                try:
                    chunk = cl.recv(512)
                    if chunk:
                        request += chunk
                        # Check if we have complete headers
                        if b"\r\n\r\n" in request:
                            # Check if there's a Content-Length header
                            header_end = request.find(b"\r\n\r\n")
                            headers = request[:header_end].decode()
                            
                            if "Content-Length:" in headers:
                                # Extract content length
                                for line in headers.split("\r\n"):
                                    if line.startswith("Content-Length:"):
                                        content_length = int(line.split(":")[1].strip())
                                        body_received = len(request) - header_end - 4
                                        
                                        # Continue reading until we have full body
                                        while body_received < content_length:
                                            chunk = cl.recv(512)
                                            if chunk:
                                                request += chunk
                                                body_received += len(chunk)
                                            else:
                                                await asyncio.sleep(0.05)
                                        break
                            else:
                                # No body expected
                                break
                except OSError:
                    pass
                await asyncio.sleep(0.1)
            
            if not request:
                cl.close()
                return
            
            request_str = request.decode()
            request_line = request_str.split("\r\n")[0] if "\r\n" in request_str else request_str
            print(f"Request: {request_line}")
            
            if "GET / " in request_str or "GET /index.html" in request_str:
                print("Serving HTML page")
                response = self.get_html_page()
                response_bytes = response.encode()
                header = b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: "
                header += str(len(response_bytes)).encode() + b"\r\n\r\n"
                cl.send(header)
                
                # Send in chunks to avoid buffer overflow
                chunk_size = 1024
                for i in range(0, len(response_bytes), chunk_size):
                    chunk = response_bytes[i:i+chunk_size]
                    cl.send(chunk)
                    await asyncio.sleep(0.01)  # Small delay between chunks
                print(f"HTML sent: {len(response_bytes)} bytes")
            
            elif "GET /config" in request_str:
                print("Serving config")
                response = json.dumps(self.config)
                response_bytes = response.encode()
                header = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                header += str(len(response_bytes)).encode() + b"\r\n\r\n"
                cl.send(header)
                cl.send(response_bytes)
                print(f"Config sent: {len(response_bytes)} bytes")
            
            elif "POST /config" in request_str:
                print("Receiving config update")
                # Extract JSON body
                body_start = request_str.find("\r\n\r\n")
                if body_start != -1:
                    body = request_str[body_start + 4:]
                    print(f"Body length: {len(body)}")
                    try:
                        new_config = json.loads(body)
                        self.config = new_config
                        success = self.save_config()
                        
                        if success:
                            response = b'{"status":"success"}'
                            header = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                            header += str(len(response)).encode() + b"\r\n\r\n"
                            cl.send(header)
                            cl.send(response)
                            print("Config saved successfully")
                        else:
                            cl.send(b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: application/json\r\n\r\n")
                            cl.send(b'{"status":"error"}')
                            print("Config save failed")
                    except Exception as e:
                        print(f"Config parse error: {e}")
                        cl.send(b"HTTP/1.1 400 Bad Request\r\nContent-Type: application/json\r\n\r\n")
                        cl.send(b'{"status":"error"}')
            
            else:
                print(f"404 Not Found")
                cl.send(b"HTTP/1.1 404 Not Found\r\n\r\n")
            
            cl.close()
        except Exception as e:
            print(f"Client handler error: {e}")
            try:
                cl.close()
            except:
                pass
    
    def get_html_page(self):
        """Generate HTML configuration page"""
        return """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>MIDI Controller Config</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: Arial, sans-serif; background: #f5f5f5; padding-bottom: 20px; }
        .header { background: #333; color: white; padding: 15px; position: sticky; top: 0; z-index: 100; display: flex; justify-content: space-between; align-items: center; }
        .header h1 { font-size: 20px; }
        .save-btn { background: #4CAF50; color: white; border: none; padding: 10px 20px; border-radius: 5px; cursor: pointer; font-size: 14px; }
        .save-btn:active { background: #45a049; }
        .container { padding: 15px; max-width: 600px; margin: 0 auto; }
        .bank-selector { background: white; padding: 15px; margin-bottom: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .bank-selector label { display: block; margin-bottom: 5px; font-weight: bold; }
        .bank-selector select { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 5px; font-size: 16px; }
        .switch-config { background: white; padding: 15px; margin-bottom: 15px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }
        .switch-config h3 { margin-bottom: 10px; color: #333; font-size: 16px; }
        .form-group { margin-bottom: 12px; }
        .form-group label { display: block; margin-bottom: 5px; font-size: 14px; color: #666; }
        .form-group input, .form-group select { width: 100%; padding: 8px; border: 1px solid #ddd; border-radius: 5px; font-size: 14px; }
        .radio-group { display: flex; gap: 15px; margin-bottom: 10px; }
        .radio-group label { display: flex; align-items: center; gap: 5px; font-size: 14px; }
        .radio-group input[type="radio"] { width: auto; }
        .message { padding: 10px; margin: 10px 0; border-radius: 5px; text-align: center; display: none; }
        .message.success { background: #d4edda; color: #155724; }
        .message.error { background: #f8d7da; color: #721c24; }
        .hidden { display: none !important; }
        .loading { opacity: 0.6; pointer-events: none; }
    </style>
</head>
<body>
    <div class="header">
        <h1>MIDI Controller</h1>
        <button class="save-btn" onclick="saveConfig()">Save</button>
    </div>
    
    <div class="container">
        <div id="message" class="message"></div>
        
        <div class="bank-selector">
            <label for="bank-select">Bank:</label>
            <select id="bank-select" onchange="loadBank()">
                <option value="0">Bank 0</option>
                <option value="1">Bank 1</option>
            </select>
        </div>
        
        <div id="switches-container"></div>
    </div>

    <script>
        let config = null;
        let currentBank = 0;

        function log(msg) {
            console.log('[MIDI Config] ' + msg);
        }

        async function loadConfig() {
            log('Loading config...');
            try {
                const response = await fetch('/config');
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                config = await response.json();
                log('Config loaded: ' + JSON.stringify(config).substring(0, 100));
                loadBank();
            } catch (e) {
                log('Failed to load config: ' + e);
                showMessage('Failed to load config: ' + e, 'error');
            }
        }

        function loadBank() {
            log('Loading bank ' + currentBank);
            currentBank = parseInt(document.getElementById('bank-select').value);
            const container = document.getElementById('switches-container');
            container.innerHTML = '';
            
            if (!config || !config.banks || !config.banks[currentBank]) {
                log('Invalid config structure');
                showMessage('Invalid configuration', 'error');
                return;
            }
            
            for (let i = 0; i < 8; i++) {
                const switchConfig = config.banks[currentBank].switches[i];
                container.innerHTML += createSwitchHTML(i, switchConfig);
            }
            
            log('Bank loaded, adding event listeners');
            // Add event listeners after DOM is updated
            setTimeout(function() {
                for (let i = 0; i < 8; i++) {
                    updateFieldVisibility(i);
                    const radios = document.querySelectorAll('input[name="type-' + i + '"]');
                    radios.forEach(function(radio) {
                        radio.addEventListener('change', function() {
                            updateFieldVisibility(i);
                        });
                    });
                }
                log('Event listeners added');
            }, 100);
        }

        function createSwitchHTML(index, cfg) {
            const noteVal = cfg.note !== undefined ? cfg.note : 60;
            const velocityVal = cfg.velocity !== undefined ? cfg.velocity : 100;
            const ccVal = cfg.cc !== undefined ? cfg.cc : 0;
            const pcVal = cfg.pc !== undefined ? cfg.pc : 0;
            const modeVal = cfg.mode || 'hold';
            const sendOffVal = cfg.send_off ? 'checked' : '';
            const onValueVal = cfg.on_value !== undefined ? cfg.on_value : 127;
            const offValueVal = cfg.off_value !== undefined ? cfg.off_value : 0;
            const delayVal = cfg.delay !== undefined ? cfg.delay : 0;
            
            return '<div class="switch-config">' +
                '<h3>Switch ' + (index + 1) + '</h3>' +
                '<div class="radio-group">' +
                '<label><input type="radio" name="type-' + index + '" value="note" ' + (cfg.type === 'note' ? 'checked' : '') + '> Note</label>' +
                '<label><input type="radio" name="type-' + index + '" value="cc" ' + (cfg.type === 'cc' ? 'checked' : '') + '> CC</label>' +
                '<label><input type="radio" name="type-' + index + '" value="pc" ' + (cfg.type === 'pc' ? 'checked' : '') + '> PC</label>' +
                '</div>' +
                '<div id="fields-' + index + '">' +
                '<div class="note-fields">' +
                '<div class="form-group"><label>Note (0-127):</label><input type="number" id="note-' + index + '" min="0" max="127" value="' + noteVal + '"></div>' +
                '<div class="form-group"><label>Velocity (0-127):</label><input type="number" id="velocity-' + index + '" min="0" max="127" value="' + velocityVal + '"></div>' +
                '<div class="form-group"><label>Mode:</label><select id="note-mode-' + index + '">' +
                '<option value="hold" ' + (modeVal === 'hold' ? 'selected' : '') + '>Hold</option>' +
                '<option value="toggle" ' + (modeVal === 'toggle' ? 'selected' : '') + '>Toggle</option>' +
                '</select></div></div>' +
                '<div class="cc-fields">' +
                '<div class="form-group"><label>CC No (0-127):</label><input type="number" id="cc-' + index + '" min="0" max="127" value="' + ccVal + '"></div>' +
                '<div class="form-group"><label>Mode:</label><select id="cc-mode-' + index + '">' +
                '<option value="hold" ' + (modeVal === 'hold' ? 'selected' : '') + '>Hold</option>' +
                '<option value="toggle" ' + (modeVal === 'toggle' ? 'selected' : '') + '>Toggle</option>' +
                '</select></div>' +
                '<div class="form-group"><label><input type="checkbox" id="send-off-' + index + '" ' + sendOffVal + '> Send Off Value</label></div>' +
                '<div class="form-group"><label>On Value (0-127):</label><input type="number" id="on-value-' + index + '" min="0" max="127" value="' + onValueVal + '"></div>' +
                '<div class="form-group"><label>Off Value (0-127):</label><input type="number" id="off-value-' + index + '" min="0" max="127" value="' + offValueVal + '"></div>' +
                '<div class="form-group"><label>Delay (ms):</label><input type="number" id="delay-' + index + '" min="0" value="' + delayVal + '"></div>' +
                '</div>' +
                '<div class="pc-fields">' +
                '<div class="form-group"><label>PC No (0-127):</label><input type="number" id="pc-' + index + '" min="0" max="127" value="' + pcVal + '"></div>' +
                '</div></div></div>';
        }

        function updateFieldVisibility(index) {
            const typeRadio = document.querySelector('input[name="type-' + index + '"]:checked');
            if (!typeRadio) {
                log('No radio selected for switch ' + index);
                return;
            }
            const type = typeRadio.value;
            const container = document.querySelector('#fields-' + index);
            
            if (!container) {
                log('Container not found for switch ' + index);
                return;
            }
            
            const noteFields = container.querySelector('.note-fields');
            const ccFields = container.querySelector('.cc-fields');
            const pcFields = container.querySelector('.pc-fields');
            
            if (noteFields) noteFields.classList.add('hidden');
            if (ccFields) ccFields.classList.add('hidden');
            if (pcFields) pcFields.classList.add('hidden');
            
            if (type === 'note' && noteFields) {
                noteFields.classList.remove('hidden');
            } else if (type === 'cc' && ccFields) {
                ccFields.classList.remove('hidden');
            } else if (type === 'pc' && pcFields) {
                pcFields.classList.remove('hidden');
            }
        }

        async function saveConfig() {
            log('Saving config...');
            document.body.classList.add('loading');
            
            try {
                for (let i = 0; i < 8; i++) {
                    const typeRadio = document.querySelector('input[name="type-' + i + '"]:checked');
                    if (!typeRadio) continue;
                    
                    const type = typeRadio.value;
                    const switchConfig = { type: type };
                    
                    if (type === 'note') {
                        switchConfig.note = parseInt(document.getElementById('note-' + i).value);
                        switchConfig.velocity = parseInt(document.getElementById('velocity-' + i).value);
                        switchConfig.mode = document.getElementById('note-mode-' + i).value;
                    } else if (type === 'cc') {
                        switchConfig.cc = parseInt(document.getElementById('cc-' + i).value);
                        switchConfig.mode = document.getElementById('cc-mode-' + i).value;
                        switchConfig.send_off = document.getElementById('send-off-' + i).checked;
                        switchConfig.on_value = parseInt(document.getElementById('on-value-' + i).value);
                        switchConfig.off_value = parseInt(document.getElementById('off-value-' + i).value);
                        switchConfig.delay = parseInt(document.getElementById('delay-' + i).value);
                    } else if (type === 'pc') {
                        switchConfig.pc = parseInt(document.getElementById('pc-' + i).value);
                    }
                    
                    config.banks[currentBank].switches[i] = switchConfig;
                }
                
                log('Sending config: ' + JSON.stringify(config).substring(0, 100));
                
                const response = await fetch('/config', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(config)
                });
                
                if (!response.ok) {
                    throw new Error('HTTP ' + response.status);
                }
                
                const result = await response.json();
                log('Save result: ' + JSON.stringify(result));
                
                if (result.status === 'success') {
                    showMessage('Configuration saved!', 'success');
                } else {
                    showMessage('Failed to save', 'error');
                }
            } catch (e) {
                log('Save error: ' + e);
                showMessage('Error: ' + e, 'error');
            } finally {
                document.body.classList.remove('loading');
            }
        }

        function showMessage(text, type) {
            const msg = document.getElementById('message');
            msg.textContent = text;
            msg.className = 'message ' + type;
            msg.style.display = 'block';
            setTimeout(function() { 
                msg.style.display = 'none'; 
            }, 3000);
        }

        // Start loading config when page loads
        window.addEventListener('load', function() {
            log('Page loaded, loading config');
            loadConfig();
        });
    </script>
</body>
</html>"""
    
    async def setup_ble(self):
        """Setup BLE MIDI service"""
        try:
            # Register MIDI service
            midi_service = aioble.Service(MIDI_SERVICE_UUID)
            self.midi_characteristic = aioble.Characteristic(
                midi_service,
                MIDI_CHAR_UUID,
                read=True,
                write=True,
                notify=True,
                indicate=False,
            )
            
            aioble.register_services(midi_service)
            
            print("BLE MIDI service registered")
            
            # Start advertising
            while True:
                print("Advertising BLE MIDI...")
                try:
                    # Advertise with proper MIDI appearance and connectable mode
                    # Appearance 0x0000 = Unknown, but we'll keep it simple
                    # For Windows compatibility, we need to advertise as connectable
                    async with await aioble.advertise(
                        250_000,  # 250ms interval
                        name="Pico MIDI",
                        services=[MIDI_SERVICE_UUID],
                        appearance=0x0000,
                        connectable=True,
                    ) as connection:
                        print(f"BLE connected: {connection.device}")
                        self.ble_connection = connection
                        
                        try:
                            # Keep connection alive and handle disconnection
                            await connection.disconnected()
                        except Exception as e:
                            print(f"BLE connection exception: {e}")
                        finally:
                            print("BLE disconnected")
                            self.ble_connection = None
                            # Small delay before re-advertising
                            await asyncio.sleep(1)
                
                except Exception as e:
                    print(f"BLE advertising error: {e}")
                    await asyncio.sleep(2)
        
        except Exception as e:
            print(f"BLE setup error: {e}")
    
    async def run(self):
        """Main run loop"""
        print("Starting MIDI Controller...")
        
        # Create tasks
        tasks = [
            asyncio.create_task(self.setup_ble()),
            asyncio.create_task(self.scan_switches()),
            asyncio.create_task(self.scan_toggles()),
            asyncio.create_task(self.blink_led()),
        ]
        
        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            print("Shutting down...")
            for task in tasks:
                task.cancel()

# Main entry point
async def main():
    controller = MIDIController()
    await controller.run()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Program terminated")
