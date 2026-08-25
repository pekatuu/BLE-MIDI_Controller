# -*- coding: utf-8 -*-
"""
Unit tests for the BLE-MIDI parser in ble_midi_receiver.py.

Run:  python tools/test_ble_midi_parser.py
"""

import sys
import types
import os

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

# stub bleak so the module imports without the dependency installed
fake = types.ModuleType("bleak")
fake.BleakScanner = fake.BleakClient = None
sys.modules["bleak"] = fake

from ble_midi_receiver import MidiParser, describe


def run():
    msgs = []
    p = MidiParser(on_message=msgs.append)

    def feed(hexstr):
        msgs.clear()
        p.decode_packet(bytearray.fromhex(hexstr))
        return list(msgs)

    failures = 0

    r = feed("8080903C64")
    ok = r == [[0x90, 0x3C, 0x64]]
    failures += 0 if ok else 1
    print("1 note-on packet          :", "OK" if ok else "FAIL %s" % (r,))

    r = feed("BEF2903C64F4803C00")
    ok = r == [[0x90, 0x3C, 0x64], [0x80, 0x3C, 0x00]]
    failures += 0 if ok else 1
    print("2 note on+off in packet   :", "OK" if ok else "FAIL %s" % (r,))

    msgs.clear()
    pkt = bytearray()
    seq = [(8180, 1), (8191, 2), (8200, 3), (8240, 4)]
    last_high, first = None, True
    for ts, v in seq:
        ts &= 0x1FFF
        hi = ts >> 7
        if first or hi != last_high:
            pkt.append(0x80 | hi)
            last_high = hi
            first = False
        pkt.append(0x80 | (ts & 0x7F))
        pkt += bytes([0xB0, 7, v])
    p.decode_packet(pkt)
    ok = [m[2] for m in msgs] == [1, 2, 3, 4]
    failures += 0 if ok else 1
    print("3 batch across rollover   :", "OK" if ok else "FAIL %s" % (msgs,))

    r = feed("BEF2B0070AF4B00714")
    ok = r == [[0xB0, 7, 10], [0xB0, 7, 20]]
    failures += 0 if ok else 1
    print("4 CC batch                :", "OK" if ok else "FAIL %s" % (r,))

    r = feed("BEF2E00040")
    ok = r == [[0xE0, 0x00, 0x40]]
    failures += 0 if ok else 1
    print("5 pitch bend              :", "OK" if ok else "FAIL %s" % (r,))

    r = feed("BDF2C005")
    ok = r == [[0xC0, 0x05]]
    failures += 0 if ok else 1
    print("6 program change          :", "OK" if ok else "FAIL %s" % (r,))

    good = True
    for v in range(0, 128, 32):
        msgs.clear()
        p.decode_packet(bytearray.fromhex("BEF2") + bytes([0xB0, 7, v]))
        good = good and msgs[-1][2] == v
    failures += 0 if good else 1
    print("7 CC sweep (individual)   :", "OK" if good else "FAIL")

    print()
    print("describe() samples:")
    for m in ([0x90, 60, 100], [0xB0, 7, 64], [0xE0, 0, 64]):
        print("  ", describe(m))

    print()
    if failures == 0:
        print("ALL PARSER TESTS PASS")
        return 0
    print("%d FAILURES" % failures)
    return 1


if __name__ == "__main__":
    sys.exit(run())
