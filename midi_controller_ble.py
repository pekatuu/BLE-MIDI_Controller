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
    """Expression pedal handler with high-speed sampling and buffering"""
    def __init__(self, adc_pin, config):
        self.adc = ADC(Pin(adc_pin))
        self.config = config
        self.filtered_value = 0
        self.last_sent_value = -1
        self.raw_max = 65535  # 16-bit ADC
        self.last_raw_value = 0
        self.filter_initialized = False
        
        # Buffering for BLE-MIDI packet batching
        self.value_buffer = []  # [(timestamp_ms, scaled_value), ...]
        self.max_buffer_size = 15
        
        # Threshold: 512 ADC counts = 1 MIDI CC step (65536/128)
        self.adc_threshold = 512
        self.last_sent_filtered_adc = 0
        
        # Debug counters
        self.sample_count = 0
        self.buffer_count = 0
        
        # Decimation counter
        self.decimation_counter = 0
    
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
        normalized = (value - self.adc_threshold) / (self.raw_max - self.adc_threshold)
        
        # Scale to min-max range (supports inverted ranges)
        if min_val <= max_val:
            scaled = min_val + normalized * (max_val - min_val)
        else:
            # Inverted range
            scaled = min_val - normalized * (min_val - max_val)
        
        return int(scaled)
    
    def process_sample(self):
        """Process one ADC sample (called at 1kHz)"""
        raw = self.read_raw()
        self.last_raw_value = raw
        deadzone_applied = self.apply_deadzone(raw)
        filtered = self.apply_filter(deadzone_applied)
        self.sample_count += 1
        return filtered
    
    def should_buffer(self, current_filtered_adc):
        """Check if filtered ADC value changed enough to buffer (512 ADC counts threshold)"""
        if self.last_sent_value == -1:
            return True
        
        # Compare in ADC space (filtered values)
        if not hasattr(self, 'last_sent_filtered_adc'):
            self.last_sent_filtered_adc = 0
        
        return abs(current_filtered_adc - self.last_sent_filtered_adc) >= self.adc_threshold
    
    def add_to_buffer(self, timestamp_ms, midi_data, filtered_adc):
        """Add value to buffer for batched sending"""
        if len(self.value_buffer) < self.max_buffer_size:
            self.value_buffer.append((timestamp_ms, midi_data))
            self.last_sent_filtered_adc = filtered_adc
            self.buffer_count += 1
    
    def get_buffer_and_clear(self):
        """Get buffered values and clear buffer"""
        buffer = self.value_buffer.copy()
        self.value_buffer.clear()
        return buffer
    
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
                "polling": 1,  # Fixed at 1ms for high-speed sampling
                "deadzone_min": 5,
                "deadzone_max": 5,
                "send_mode": "individual",  # "individual" or "batch"
                "send_interval": 15,  # ms between sends
                "msg_interval": 2,  # ms between individual messages (individual mode only)
                "decimation": 1  # Send every Nth sample (1=all, 2=every other, 3=every third)
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
        """Get BLE MIDI timestamp (13-bit)"""
        ms = time.ticks_ms() & 0x1FFF  # 13-bit mask
        header = 0x80 | ((ms >> 7) & 0x3F)
        timestamp = 0x80 | (ms & 0x7F)
        return bytes([header, timestamp])
    
    def get_timestamp_parts(self, timestamp_ms):
        """Get BLE MIDI timestamp parts for batched messages"""
        ms = timestamp_ms & 0x1FFF  # 13-bit mask
        header = 0x80 | ((ms >> 7) & 0x3F)
        timestamp_low = 0x80 | (ms & 0x7F)
        return header, timestamp_low
    
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
    
    async def send_midi_batch(self, messages):
        """Send multiple MIDI messages in one BLE packet with timestamps"""
        if not self.ble_connection or not self.midi_characteristic or not messages:
            return
        
        try:
            # Build BLE-MIDI packet with multiple messages
            packet = bytearray()
            
            # CRITICAL FIX: Use FIRST message's timestamp for header
            # BLE-MIDI spec: header and timestamp_low must be from the SAME 13-bit timestamp
            first_ts_13bit = messages[0][0] & 0x1FFF
            header = 0x80 | ((first_ts_13bit >> 7) & 0x3F)
            packet.append(header)
            
            # Debug: log all MIDI values and timestamps
            midi_values = []
            timestamps_full = []
            last_header_bits = first_ts_13bit >> 7
            
            for timestamp_ms, midi_data in messages:
                # Ensure midi_data is a tuple or list
                if isinstance(midi_data, (tuple, list)):
                    midi_bytes = bytes(midi_data)
                else:
                    midi_bytes = midi_data
                
                # Use message's own timestamp
                ts_13bit = timestamp_ms & 0x1FFF
                current_header_bits = ts_13bit >> 7
                
                # If upper 6 bits changed, insert new header
                # This handles 13-bit timestamp wraparound correctly
                if current_header_bits != last_header_bits:
                    new_header = 0x80 | (current_header_bits & 0x3F)
                    packet.append(new_header)
                    last_header_bits = current_header_bits
                
                # Add timestamp low 7 bits (MUST have bit 7 set)
                timestamp_low = 0x80 | (ts_13bit & 0x7F)
                packet.append(timestamp_low)
                print(f"timestamp: {header} {timestamp_low}  ")
                
                # Add MIDI data bytes (MUST NOT have bit 7 set for data bytes)
                # Status byte (0xB0, 0xE0, etc.) DOES have bit 7 set
                for byte in midi_bytes:
                    packet.append(byte)
                
                timestamps_full.append(ts_13bit)
                
                # Extract MIDI value for logging
                if len(midi_bytes) >= 3:
                    if midi_bytes[0] == 0xB0:  # CC
                        midi_values.append(midi_bytes[2])
                    elif midi_bytes[0] == 0xE0:  # Bend
                        bend_val = (midi_bytes[2] << 7) | midi_bytes[1]
                        midi_values.append(bend_val)
                
                # Safety: limit packet size to avoid BLE MTU issues
                if len(packet) > 100:
                    break
            
            # Send batched packet
            self.midi_characteristic.write(bytes(packet), send_update=True)
            
            # Debug log for verification with full timestamps and packet hex
            if len(messages) > 0:
                packet_hex = ' '.join([f'{b:02X}' for b in packet[:min(30, len(packet))]])
                if len(packet) > 30:
                    packet_hex += '...'
                print(f"Batch: {len(messages)} msgs, values=[{midi_values[0] if midi_values else '?'}..{midi_values[-1] if midi_values else '?'}], ts=[{timestamps_full[0]}..{timestamps_full[-1]}]")
                print(f"  Packet: {packet_hex}")
            
        except Exception as e:
            print(f"MIDI batch send error: {e}")
    
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
    
    async def sample_expression_pedals(self):
        """High-speed sampling task (1kHz) for expression pedals"""
        while True:
            current_time = time.ticks_ms()
            
            # Get decimation setting
            decimation = self.config.get("exp_common", {}).get("decimation", 1)
            
            for i, pedal in enumerate(self.exp_pedals):
                exp_config = self.config["banks"][self.current_bank]["exp_pedals"][i]
                exp_type = exp_config.get("type", "cc")
                
                # Sample and filter at 1kHz
                filtered_value = pedal.process_sample()
                
                # Decimation: only process every Nth sample
                pedal.decimation_counter += 1
                if pedal.decimation_counter < decimation:
                    continue
                pedal.decimation_counter = 0
                
                # Check if change is significant (ADC threshold: 512 counts)
                if pedal.should_buffer(filtered_value):
                    if exp_type == "cc":
                        min_val = exp_config.get("min_value", 0)
                        max_val = exp_config.get("max_value", 127)
                        cc_value = pedal.scale_to_range(filtered_value, min_val, max_val)
                        cc_num = exp_config.get("cc", 11)
                        
                        # Debug: verify scaling
                        if i == 0 and cc_value > 120:
                            print(f"Sample: filtered={filtered_value}, cc={cc_value}, raw={pedal.last_raw_value}")
                        
                        pedal.add_to_buffer(current_time, (0xB0, cc_num, cc_value), filtered_value)
                        pedal.mark_sent(cc_value)
                    
                    elif exp_type == "bend":
                        min_val = exp_config.get("min_value", 0)
                        max_val = exp_config.get("max_value", 16383)
                        bend_value = pedal.scale_to_range(filtered_value, min_val, max_val)
                        lsb = bend_value & 0x7F
                        msb = (bend_value >> 7) & 0x7F
                        pedal.add_to_buffer(current_time, (0xE0, lsb, msb), filtered_value)
                        pedal.mark_sent(bend_value)
            
            await asyncio.sleep_ms(1)  # 1kHz sampling
    
    async def send_expression_pedals(self):
        """Send buffered expression pedal data with configurable interval and mode"""
        debug_counter = [0, 0]
        last_debug_time = time.ticks_ms()
        
        while True:
            current_time = time.ticks_ms()
            
            # Get send settings from config
            exp_common = self.config.get("exp_common", {})
            send_mode = exp_common.get("send_mode", "individual")  # "batch" or "individual"
            send_interval = exp_common.get("send_interval", 15)  # ms between sends
            msg_interval = exp_common.get("msg_interval", 2)  # ms between individual messages
            
            for i, pedal in enumerate(self.exp_pedals):
                # Get buffered values
                buffer = pedal.get_buffer_and_clear()
                
                if buffer:
                    if send_mode == "batch":
                        # Batch mode: send all messages in one BLE-MIDI packet
                        # WARNING: Some receivers may not handle this correctly
                        await self.send_midi_batch(buffer)
                        
                        # Debug: log last value in buffer
                        exp_config = self.config["banks"][self.current_bank]["exp_pedals"][i]
                        exp_type = exp_config.get("type", "cc")
                        last_msg = buffer[-1][1]
                        
                        if exp_type == "cc":
                            last_cc = last_msg[2]
                            if i == 0:  # Only log pedal 0
                                print(f"EXP{i} sent {len(buffer)} msgs (batch), last CC: {last_cc} (raw:{pedal.last_raw_value}, filtered:{int(pedal.filtered_value)}, last_sent_adc:{pedal.last_sent_filtered_adc})")
                        else:
                            last_bend = (last_msg[2] << 7) | last_msg[1]
                            if i == 0:
                                print(f"EXP{i} sent {len(buffer)} msgs (batch), last Bend: {last_bend} (raw:{pedal.last_raw_value}, filtered:{int(pedal.filtered_value)}, last_sent_adc:{pedal.last_sent_filtered_adc})")
                    else:
                        # Individual mode: send messages one by one with delay
                        # More compatible but slower
                        for timestamp_ms, midi_data in buffer:
                            await self.send_midi(list(midi_data))
                            if msg_interval > 0:
                                await asyncio.sleep_ms(msg_interval)
                        
                        # Debug: log last value in buffer
                        exp_config = self.config["banks"][self.current_bank]["exp_pedals"][i]
                        exp_type = exp_config.get("type", "cc")
                        last_msg = buffer[-1][1]
                        
                        if exp_type == "cc":
                            last_cc = last_msg[2]
                            if i == 0:  # Only log pedal 0
                                print(f"EXP{i} sent {len(buffer)} msgs (individual), last CC: {last_cc} (raw:{pedal.last_raw_value}, filtered:{int(pedal.filtered_value)}, last_sent_adc:{pedal.last_sent_filtered_adc})")
                        else:
                            last_bend = (last_msg[2] << 7) | last_msg[1]
                            if i == 0:
                                print(f"EXP{i} sent {len(buffer)} msgs (individual), last Bend: {last_bend} (raw:{pedal.last_raw_value}, filtered:{int(pedal.filtered_value)}, last_sent_adc:{pedal.last_sent_filtered_adc})")
                    
                    debug_counter[i] += len(buffer)
            
            # Debug statistics every 5 seconds
            if time.ticks_diff(current_time, last_debug_time) >= 5000:
                for i, pedal in enumerate(self.exp_pedals):
                    if pedal.sample_count > 0:
                        buffer_rate = (pedal.buffer_count / pedal.sample_count) * 100
                        # print(f"EXP{i} stats: samples={pedal.sample_count}, buffered={pedal.buffer_count} ({buffer_rate:.1f}%), sent={debug_counter[i]}")
                        pedal.sample_count = 0
                        pedal.buffer_count = 0
                        debug_counter[i] = 0
                last_debug_time = current_time
            
            await asyncio.sleep_ms(send_interval)
    
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
                
                # IMPORTANT: Clear expression pedal buffers and reset state on bank change
                for pedal in self.exp_pedals:
                    pedal.last_sent_value = -1
                    pedal.last_sent_filtered_adc = 0
                    pedal.value_buffer.clear()
                    print(f"Cleared pedal buffer and state")
            
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
            print("=== BLE Setup Start ===")
            
            # Register MIDI service
            print(f"Creating MIDI service: {MIDI_SERVICE_UUID}")
            midi_service = aioble.Service(MIDI_SERVICE_UUID)
            
            print(f"Creating MIDI characteristic: {MIDI_CHAR_UUID}")
            self.midi_characteristic = aioble.Characteristic(
                midi_service,
                MIDI_CHAR_UUID,
                read=True,
                write=True,
                notify=True,
                indicate=False,
            )
            
            print("Registering services...")
            aioble.register_services(midi_service)
            
            print("BLE MIDI service registered successfully")
            print("=== BLE Setup Complete ===")
            
            # Start advertising
            while True:
                print("Advertising BLE MIDI...")
                try:
                    # Advertise with proper MIDI appearance and connectable mode
                    # Use shorter interval for better discoverability
                    async with await aioble.advertise(
                        100_000,  # 100ms interval (faster discovery)
                        name="Pico MIDI",
                        services=[MIDI_SERVICE_UUID],
                        appearance=0x0000,
                        connectable=True,
                    ) as connection:
                        print(f"!!! BLE CONNECTED from: {connection.device} !!!")
                        self.ble_connection = connection
                        
                        print("Connection established, waiting for data...")
                        
                        try:
                            # Keep connection alive and handle disconnection
                            await connection.disconnected()
                        except Exception as e:
                            print(f"BLE connection exception: {e}")
                        finally:
                            print("!!! BLE DISCONNECTED !!!")
                            self.ble_connection = None
                            # Small delay before re-advertising
                            await asyncio.sleep(1)
                
                except Exception as e:
                    print(f"BLE advertising error: {e}")
                    import sys
                    sys.print_exception(e)
                    await asyncio.sleep(2)
        
        except Exception as e:
            print(f"BLE setup error: {e}")
            import sys
            sys.print_exception(e)
    
    async def run(self):
        """Main run loop"""
        print("Starting MIDI Controller...")
        
        # Create tasks
        tasks = [
            asyncio.create_task(self.setup_ble()),
            asyncio.create_task(self.scan_switches()),
            asyncio.create_task(self.sample_expression_pedals()),  # 1kHz sampling
            asyncio.create_task(self.send_expression_pedals()),    # 15ms batched sending
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
