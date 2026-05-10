"""
BLE-MIDI Controller for Raspberry Pi Pico 2W
MicroPython implementation
"""

import asyncio
import json
import time
import machine
import network
from machine import Pin, ADC
import aioble
import bluetooth
import struct

# GPIO Configuration
SWITCH_PINS = [16, 17, 18, 19, 20, 21, 14, 15]
EXP_PINS = [26]  # ADC0, ADC1
TOGGLE_PINS = [1, 5]  # [WiFi ON/OFF, Bank Select]

# BLE MIDI UUIDs
MIDI_SERVICE_UUID = bluetooth.UUID("03B80E5A-EDE8-4B33-A751-6CE34EC4C700")
MIDI_CHAR_UUID = bluetooth.UUID("7772E5DB-3868-4112-A1A9-F2669D106BF3")

# Configuration
CONFIG_FILE = "config.json"
WIFI_SSID = "BLE-MIDI Controller"
WIFI_PASSWORD = "testdesuyo"

class ExpressionPedal:
    """Expression pedal handler with filtering and deadzone"""
    def __init__(self, adc_pin, config):
        self.adc = ADC(Pin(adc_pin))
        self.config = config
        self.filtered_value = 0
        self.last_sent_value = -1
        self.raw_max = 65535  # 16-bit ADC
        self.last_raw_value = 0
        self.filter_initialized = False
    
    def read_raw(self):
        """Read raw ADC value"""
        return self.adc.read_u16()
    
    def apply_deadzone(self, raw_value):
        """Apply deadzone to raw ADC value"""
        # Convert to 0-100%
        percent = (raw_value / self.raw_max) * 100
        
        deadzone_min = self.config.get("deadzone_min", 5)
        deadzone_max = self.config.get("deadzone_max", 5)
        
        # Apply deadzone
        if percent < deadzone_min:
            return 0
        elif percent > (100 - deadzone_max):
            return self.raw_max
        else:
            # Scale to full range
            adjusted = (percent - deadzone_min) / (100 - deadzone_min - deadzone_max)
            return int(adjusted * self.raw_max)
    
    def apply_filter(self, value):
        """Apply adaptive EMA (Exponential Moving Average) filter"""
        # Initialize filter on first call
        if not self.filter_initialized:
            self.filtered_value = value
            self.filter_initialized = True
            return value
        
        base_alpha = self.config.get("filter", 0.1)
        
        # Adaptive filtering: increase alpha (faster response) for large changes
        diff = abs(value - self.filtered_value)
        diff_percent = diff / self.raw_max
        
        # If change is > 20% of full range, jump immediately (no filter)
        if diff_percent > 0.2:
            self.filtered_value = value
            return value
        # If change is > 10% of full range, use much faster filter
        elif diff_percent > 0.1:
            alpha = min(1.0, base_alpha * 10)  # Up to 10x faster
        elif diff_percent > 0.05:
            alpha = min(1.0, base_alpha * 3)  # 3x faster
        else:
            alpha = base_alpha
        
        self.filtered_value = alpha * value + (1 - alpha) * self.filtered_value
        return int(self.filtered_value)
    
    def scale_to_range(self, value, min_val, max_val):
        """Scale filtered value to output range"""
        # Normalize to 0-1
        normalized = value / self.raw_max
        
        # Scale to min-max range (supports inverted ranges)
        if min_val <= max_val:
            scaled = min_val + normalized * (max_val - min_val)
        else:
            # Inverted range
            scaled = min_val - normalized * (min_val - max_val)
        
        return int(scaled)
    
    def process(self):
        """Process ADC reading through full pipeline"""
        raw = self.read_raw()
        self.last_raw_value = raw
        deadzone_applied = self.apply_deadzone(raw)
        filtered = self.apply_filter(deadzone_applied)
        return filtered
    
    def should_send(self, current_value, min_val, max_val):
        """Check if value changed enough to send MIDI (1% of range)"""
        if self.last_sent_value == -1:
            return True
        
        # Calculate 1% of the range
        value_range = abs(max_val - min_val)
        threshold = max(1, int(value_range * 0.01))  # At least 1
        
        return abs(current_value - self.last_sent_value) >= threshold
    
    def mark_sent(self, value):
        """Mark value as sent"""
        self.last_sent_value = value

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
        
        # Initialize expression pedals
        self.exp_pedals = [
            ExpressionPedal(ep, self.config.get("exp_common", {})) for ep in EXP_PINS
        ]
        
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
                            "note": 60 + i,
                            "velocity": 100,
                            "mode": "hold"
                        } for i in range(8)
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
                    ],
                    "exp_pedals": [
                        {
                            "type": "cc",
                            "cc": 1,
                            "min_value": 0,
                            "max_value": 127
                        },
                        {
                            "type": "cc",
                            "cc": 7,
                            "min_value": 0,
                            "max_value": 127
                        }
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
            
            # Update expression pedal common config
            if hasattr(self, 'exp_pedals'):
                exp_common = config.get("exp_common", {})
                for pedal in self.exp_pedals:
                    pedal.config = exp_common
            
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
                # Reduced logging for expression pedals
                if data[0] not in [0xB0, 0xE0]:  # Not CC or Bend
                    print(f"MIDI sent: {[hex(b) for b in data]}")
            except Exception as e:
                print(f"MIDI send error: {e}")
    
    async def send_pitch_bend(self, value):
        """Send pitch bend message (14-bit value 0-16383)"""
        # Pitch bend: 0xE0 + LSB + MSB
        lsb = value & 0x7F
        msb = (value >> 7) & 0x7F
        await self.send_midi([0xE0, lsb, msb])
    
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
    
    async def scan_expression_pedals(self):
        """Scan expression pedals and send MIDI"""
        polling_ms = self.config.get("exp_common", {}).get("polling", 5)
        last_send_time = [0, 0]  # Track last send time for each pedal
        min_send_interval_ms = 10  # Minimum 10ms between sends (max 100 msgs/sec per pedal)
        debug_counter = [0, 0]  # Debug: count sends per pedal
        
        while True:
            current_time = time.ticks_ms()
            
            for i, pedal in enumerate(self.exp_pedals):
                # Check if enough time has passed since last send
                time_since_last_send = time.ticks_diff(current_time, last_send_time[i])
                if time_since_last_send < min_send_interval_ms:
                    # Skip processing entirely during rate limit
                    continue
                
                exp_config = self.config["banks"][self.current_bank]["exp_pedals"][i]
                exp_type = exp_config.get("type", "cc")
                
                # Process pedal value ONLY when we can send
                filtered_value = pedal.process()
                
                if exp_type == "cc":
                    cc_num = exp_config.get("cc", 11)
                    min_val = exp_config.get("min_value", 0)
                    max_val = exp_config.get("max_value", 127)
                    
                    # Scale to CC range
                    cc_value = pedal.scale_to_range(filtered_value, min_val, max_val)
                    
                    # Send if changed (1% of range threshold)
                    if pedal.should_send(cc_value, min_val, max_val):
                        await self.send_midi([0xB0, cc_num, cc_value])
                        pedal.mark_sent(cc_value)
                        last_send_time[i] = current_time
                        debug_counter[i] += 1
                        # Debug log every 10 sends
                        if debug_counter[i] % 10 == 0:
                            print(f"EXP{i} CC#{cc_num}: {cc_value} (raw:{pedal.last_raw_value}, filtered:{filtered_value})")
                
                elif exp_type == "bend":
                    min_val = exp_config.get("min_value", 0)
                    max_val = exp_config.get("max_value", 16383)
                    
                    # Scale to bend range
                    bend_value = pedal.scale_to_range(filtered_value, min_val, max_val)
                    
                    # Send if changed (1% of range threshold)
                    if pedal.should_send(bend_value, min_val, max_val):
                        await self.send_pitch_bend(bend_value)
                        pedal.mark_sent(bend_value)
                        last_send_time[i] = current_time
                        debug_counter[i] += 1
                        # Debug log every 10 sends
                        if debug_counter[i] % 10 == 0:
                            print(f"EXP{i} Bend: {bend_value} (raw:{pedal.last_raw_value}, filtered:{filtered_value})")
            
            await asyncio.sleep_ms(polling_ms)
    
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
                
                # Reset expression pedal states on bank change
                for pedal in self.exp_pedals:
                    pedal.last_sent_value = -1
            
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
        try:
            with open('web_ui.html', 'r') as f:
                return f.read()
        except:
            # Fallback to minimal HTML if file not found
            return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>MIDI Controller</title></head>
<body><h1>Configuration UI file not found</h1>
<p>Please upload web_ui.html to the device.</p></body></html>"""
    
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
            asyncio.create_task(self.scan_expression_pedals()),
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
