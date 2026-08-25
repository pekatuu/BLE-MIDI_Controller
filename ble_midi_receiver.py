# -*- coding: utf-8 -*-
"""
BLE-MIDI Receiver for Windows  (Default Loopback edition)
=========================================================
Receives BLE-MIDI (Bluetooth LE MIDI 1.0) from the Pico 2W controller,
decodes packets and forwards each MIDI message as a UMP word into the
**Windows MIDI Services Default App Loopback (A)** endpoint.

DAW side: select "Default App Loopback (B)" as a MIDI input.
No virtual port pair needs to be created - the default loopback ships
with Windows MIDI Services.

Requirements:
    pip install bleak
    Windows MIDI Services + PowerShell 7 (for UMP forwarding)

Usage:
    python ble_midi_receiver.py               auto-detect BLE-MIDI device
    python ble_midi_receiver.py --address AA:BB:CC:DD:EE:FF
    python ble_midi_receiver.py --verbose     dump raw BLE packets
"""

import argparse
import asyncio
import subprocess
import sys
import time

try:
    from bleak import BleakScanner, BleakClient
except ImportError:
    sys.exit("bleak is required:  pip install bleak")

# --- BLE-MIDI standard UUIDs -------------------------------------------------
MIDI_SERVICE_UUID = "03b80e5a-ede8-4b33-a751-6ce34ec4c700"
MIDI_CHAR_UUID = "7772e5db-3868-4112-a1a9-f2669d106bf3"

# --- Windows MIDI Services ---------------------------------------------------
PWSH_PATH = (r"C:\Users\pekat\AppData\Local\Microsoft\WindowsApps"
             r"\Microsoft.PowerShell_8wekyb3d8bbwe\pwsh.exe")
MIDI_PSMODULE = (r"C:\Program Files\Windows MIDI Services\PowerShell"
                 r"\WindowsMidiServices\WindowsMidiServices.psd1")
LOOP_A_ID = r"\\?\swd#midisrv#midiu_loop_a_default#{e7cce071-3c03-423f-88d3-f1045d02552b}"
LOOP_B_NAME_HINT = "Default App Loopback (B)"

MSG_LEN = {0x80: 2, 0x90: 2, 0xA0: 2, 0xB0: 2, 0xC0: 1, 0xD0: 1, 0xE0: 2}
SYS_COMMON_LEN = {0xF1: 1, 0xF2: 2, 0xF3: 1}


# ============================================================================
# UMP forwarding via PowerShell (persistent session process)
# ============================================================================

class UmpForwarder:
    """Keeps one pwsh process alive with an open connection to loopback A,
    and feeds it MIDI messages as UMP words via stdin."""

    def __init__(self):
        self.proc = None

    def start(self):
        script = "\n".join([
            f"Import-Module '{MIDI_PSMODULE}'",
            "Start-Midi | Out-Null",
            f"$session = Start-MidiSession 'BLE Bridge Forwarder'",
            f"if ($session -eq $null) {{ Write-Output 'SESSION_FAIL'; exit 1 }}",
            f"$connection = Open-MidiEndpointConnection $session '{LOOP_A_ID}'",
            f"if ($connection -eq $null) {{ Write-Output 'CONNECT_FAIL'; exit 1 }}",
            "Write-Output 'READY'",
        ]) + "\n"
        # keep session alive: after READY, read lines from stdin, each line is
        # a UMP hex word to send.
        script += (
            "while ($true) {\n"
            "    $line = [Console]::In.ReadLine()\n"
            "    if ($null -eq $line) { break }\n"
            "    if ($line -eq 'EXIT') { break }\n"
            "    $word = [uint32]$line\n"
            "    Send-MidiMessage $connection $word -Timestamp 0 | Out-Null\n"
            "}\n"
            "Close-MidiEndpointConnection $session $connection | Out-Null\n"
            "Stop-MidiSession $session\n"
            "Stop-Midi\n"
        )
        with open("_ump_fwd.ps1", "w", encoding="utf-8") as f:
            f.write(script)
        try:
            self.proc = subprocess.Popen(
                [PWSH_PATH, "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", "_ump_fwd.ps1"],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True)
            # wait for READY
            deadline = time.time() + 30
            while time.time() < deadline:
                line = self.proc.stdout.readline()
                if not line:
                    break
                line = line.strip()
                if line == "READY":
                    return True
                if line in ("SESSION_FAIL", "CONNECT_FAIL"):
                    print("[forwarder]", line)
                    self.stop()
                    return False
            self.stop()
            return False
        except FileNotFoundError:
            print("[warn] pwsh not found - UMP forwarding disabled")
            return False

    def send(self, ump_word):
        if self.proc and self.proc.stdin:
            try:
                self.proc.stdin.write(f"0x{ump_word:08X}\n")
                self.proc.stdin.flush()
            except Exception:
                pass

    def stop(self):
        if self.proc:
            try:
                if self.proc.stdin:
                    self.proc.stdin.write("EXIT\n")
                    self.proc.stdin.flush()
                    self.proc.stdin.close()
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
            self.proc = None


def midi1_to_ump(msg):
    """Convert a MIDI 1.0 byte message to one UMP 32-bit word (group 0).

    UMP MIDI 1.0 format: [2][group][status][d1][d2] packed into 32 bits.
    """
    st = msg[0]
    hi = st & 0xF0
    if hi in (0x80, 0x90, 0xA0, 0xB0, 0xE0):     # 2 data bytes
        d1 = msg[1] & 0x7F if len(msg) > 1 else 0
        d2 = msg[2] & 0x7F if len(msg) > 2 else 0
        return (0x20 << 24) | ((st & 0xFF) << 16) | (d1 << 8) | d2
    if hi in (0xC0, 0xD0):                        # 1 data byte
        d1 = msg[1] & 0x7F if len(msg) > 1 else 0
        return (0x20 << 24) | ((st & 0xFF) << 16) | (d1 << 8)
    if st >= 0xF8:                                # realtime: no data bytes
        return (0x20 << 24) | ((st & 0xFF) << 16)
    return None                                   # sysex etc: skip for now


# ============================================================================
# BLE-MIDI packet decoder
# ============================================================================

class MidiParser:
    """BLE-MIDI 1.0 decoder.

    Packet layout per the MIDI-BLE 1.0 spec:
        [timestampHeader] ([timestampLow] midi-message)* ...
    A new timestampHeader byte appears whenever the high 6 bits of the
    13-bit timestamp change within a packet. Running status is permitted.
    """

    def __init__(self, on_message):
        self.on_message = on_message      # callback(list_of_bytes)
        self.last_high = None

    def decode_packet(self, data):
        n = len(data)
        i = 0
        while i < n:
            b = data[i]
            if not (b & 0x80):            # stray data byte - skip defensively
                i += 1
                continue
            if self.last_high is None or i == 0:
                self.last_high = b & 0x3F   # packet header
                i += 1
                continue
            consumed = self._try_ts_and_message(data, i)
            if consumed:
                i += consumed
            else:
                self.last_high = b & 0x3F   # new header (rollover)
                i += 1

    def _try_ts_and_message(self, data, i):
        n = len(data)
        if i + 1 >= n:
            return 0
        status = data[i + 1]
        if not (status & 0x80):
            return 0                       # next byte is data -> was a header

        if status >= 0xF8:                 # system realtime: single byte
            self._emit([status])
            return 2

        if status in (0xF0, 0xF7):         # sysex
            return self._parse_sysex(data, i)

        hi = status & 0xF0
        length = MSG_LEN.get(hi)
        if length is None:
            length = SYS_COMMON_LEN.get(status)
            if length is None:
                return 0

        end = i + 2 + length               # tsLow(i) + status(i+1) + data...
        if n < end:
            return 0
        msg = [status] + list(data[i + 2:end])
        if any(d & 0x80 for d in msg[1:]):
            return 0
        self._emit(msg)
        return end - i

    def _parse_sysex(self, data, i):
        out = []
        k = i + 2
        while k < len(data):
            b = data[k]
            if b & 0x80:
                if b == 0xF7:
                    k += 1
                    break
                if b >= 0xF8:
                    out.append(b); k += 1; continue
                break
            out.append(b)
            k += 1
        self._emit([data[i + 1]] + out)
        return k - i

    def _emit(self, msg):
        try:
            self.on_message(msg)
        except Exception as e:
            print("callback error:", e)


# ============================================================================
# pretty printing
# ============================================================================

NOTE_NAMES = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def describe(msg):
    st = msg[0]
    if st >= 0xF8:
        return f"Realtime  {st:02X}"
    hi, ch = st & 0xF0, (st & 0x0F) + 1
    if hi == 0x90 and len(msg) >= 3 and msg[2]:
        name = NOTE_NAMES[msg[1] % 12] + str(msg[1] // 12 - 1)
        return f"Note On   ch{ch:<2} note={msg[1]:3d} ({name:<4}) vel={msg[2]}"
    if hi in (0x80, 0x90) and len(msg) >= 3:
        name = NOTE_NAMES[msg[1] % 12] + str(msg[1] // 12 - 1)
        return f"Note Off  ch{ch:<2} note={msg[1]:3d} ({name:<4}) vel={msg[2]}"
    if hi == 0xB0 and len(msg) >= 3:
        return f"CC        ch{ch:<2} cc#{msg[1]:<4d} value={msg[2]}"
    if hi == 0xE0 and len(msg) >= 3:
        val = ((msg[2] & 0x7F) << 7) | (msg[1] & 0x7F)
        return f"PitchBend ch{ch:<2} value={val:5d} ({val / 16383 * 100:5.1f}%)"
    if hi == 0xC0:
        return f"ProgChg   ch{ch:<2} program={msg[1]}"
    return " ".join(f"{b:02X}" for b in msg)


# ============================================================================
# main
# ============================================================================

async def run(address=None, verbose=False):
    print("=" * 64)
    print(" BLE-MIDI Receiver  ->  Windows MIDI Services Default Loopback")
    print("=" * 64)

    # ---- discovery ---------------------------------------------------------
    if address is None:
        print("\nScanning 10 s for BLE-MIDI devices ...")
        found = {}

        def adv_cb(device, adv):
            uuids = [str(u).lower() for u in (adv.service_uuids or [])]
            if MIDI_SERVICE_UUID in uuids:
                found[device.address] = device.name or "(BLE-MIDI device)"

        scanner = BleakScanner(detection_callback=adv_cb)
        await scanner.start()
        await asyncio.sleep(10)
        await scanner.stop()
        if not found:
            sys.exit("\nNo BLE-MIDI device found.\n"
                     "- Is the Pico powered on and advertising ('Pico MIDI')?\n"
                     "- If it is bonded/paired in Windows Bluetooth settings, "
                     "remove that entry so this app can connect directly.")
        address, name = next(iter(found.items()))
        print(f"Found: {name} [{address}]")

    # ---- UMP forwarder -----------------------------------------------------
    print("\nOpening Windows MIDI Services Default App Loopback (A) ...")
    fwd = UmpForwarder()
    if fwd.start():
        print('  OK - messages will appear on "Default App Loopback (B)".')
        print(f'     Select "{LOOP_B_NAME_HINT}" as a MIDI input in your DAW.')
    else:
        print("  WARNING: forwarding unavailable - monitor-only mode.")

    # ---- connect & listen --------------------------------------------------
    stats = {"packets": 0, "messages": 0}
    t0 = time.time()

    def sink(msg):
        stats["messages"] += 1
        rate = stats["messages"] / max(time.time() - t0, 1e-9)
        stamp = time.strftime("%H:%M:%S")
        hexs = " ".join(f"{b:02X}" for b in msg)
        print(f"[{stamp}] {describe(msg):<46s} <{hexs}>  #{stats['messages']} ({rate:.0f}/s)")
        ump = midi1_to_ump(msg)
        if ump is not None:
            fwd.send(ump)

    parser = MidiParser(on_message=sink)

    def on_packet(handle, data):
        stats["packets"] += 1
        if verbose:
            print("PKT ", " ".join(f"{b:02X}" for b in data))
        parser.decode_packet(bytearray(data))

    print(f"\nConnecting to {address} ...")
    client = BleakClient(address, timeout=15.0)
    try:
        await client.connect()

        char = None
        for s in client.services:
            for c in s.characteristics:
                if str(c.uuid).lower() == MIDI_CHAR_UUID:
                    char = c
                    break
        if char is None:
            raise RuntimeError(
                "Connected but BLE-MIDI characteristic missing - "
                "this device is not the Pico MIDI controller.")

        print("Connected! Listening for MIDI messages (Ctrl+C to stop)\n")
        await client.start_notify(char, on_packet)
        while True:
            await asyncio.sleep(3600)

    except KeyboardInterrupt:
        pass
    except Exception as e:
        m = str(e)
        print(f"\nERROR: {type(e).__name__}: {m}")
        if "Unreachable" in m or "not found" in m.lower():
            print("\nHints:")
            print(" - Power-cycle the Pico (unplug/replug USB), then rerun soon.")
            print(" - Remove any stale 'Pico MIDI' bond in Settings > Bluetooth.")
            print(" - The Pico accepts ONE connection; close other MIDI apps.")
    finally:
        try:
            if client.is_connected:
                await client.disconnect()
        except Exception:
            pass
        fwd.stop()
        dt = max(time.time() - t0, 1e-9)
        print(f"\nSession summary: {stats['packets']} BLE packets, "
              f"{stats['messages']} MIDI messages in {dt:.0f}s")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="BLE-MIDI receiver -> Windows MIDI Services default loopback")
    ap.add_argument("--address", help="BLE MAC address of the Pico (skips scanning)")
    ap.add_argument("--verbose", action="store_true", help="dump raw BLE packets")
    args = ap.parse_args()
    try:
        asyncio.run(run(args.address, args.verbose))
    except KeyboardInterrupt:
        print("\nStopped.")
