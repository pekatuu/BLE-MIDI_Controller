# -*- coding: utf-8 -*-
"""Tests for timestamp recovery + paced forwarding (Method A)."""
import sys, types, os, asyncio, time

fake = types.ModuleType("bleak")
fake.BleakScanner = fake.BleakClient = None
sys.modules["bleak"] = fake
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from ble_midi_receiver import MidiParser

failures = 0


def check(name, cond):
    global failures
    print("%-42s: %s" % (name, "OK" if cond else "FAIL"))
    if not cond:
        failures += 1


# --- 1: timestamps recovered for a batch within one packet -------------------
msgs = []
p = MidiParser(on_message=lambda ts, m: msgs.append((ts, tuple(m))))

pkt = bytearray([0xBE])                       # header high=62 -> base 62*128=7936
for ts_low in (0x10, 0x20, 0x30):             # ts = 7936+16, +32, +48
    pkt += bytes([0x80 | ts_low, 0xB0, 7, ts_low])
p.decode_packet(pkt)

check("3 msgs decoded", len(msgs) == 3)
t0, t1, t2 = [m[0] for m in msgs]
check("monotonic timestamps", t1 > t0 and t2 > t1)
check("delta 16ms between messages", (t1 - t0) == 16 and (t2 - t1) == 16)

# --- 2: wrap across packets (ts 8191 then wrapped to small value) ------------
msgs.clear()
p.decode_packet(bytes([0xBF, 0xFF, 0xB0, 7, 1]))   # header high=63, ts=8191
p.decode_packet(bytes([0x80, 0x80, 0xB0, 7, 2]))   # header high=0, tsLow=0 -> wrap
check("wrap across packets decoded", len(msgs) == 2)
check("wrap delta is small (<=4ms)", msgs[1][0] - msgs[0][0] <= 4)

# --- 3: rollover inside one packet with re-inserted header -------------------
msgs.clear()
p2 = MidiParser(on_message=lambda ts, m: msgs.append((ts, tuple(m))))
pkt = bytearray()
seq = [(8180, 1), (8191, 2), (8200, 3)]       # crosses 8192 mid-packet
lh, first = None, True
for ts, v in seq:
    ts &= 0x1FFF
    hi = ts >> 7
    if first or hi != lh:
        pkt.append(0x80 | hi); lh = hi; first = False
    pkt.append(0x80 | (ts & 0x7F))
    pkt += bytes([0xB0, 7, v])
p2.decode_packet(pkt)
deltas = [msgs[i + 1][0] - msgs[i][0] for i in range(len(msgs) - 1)]
check("rollover in-packet deltas correct",
      len(msgs) == 3 and deltas == [11, 9])


# --- 4: paced forwarder timing ------------------------------------------------
async def paced_run():
    q = asyncio.Queue()
    sent = []

    async def paced_forwarder():
        prev_ts = None
        max_catchup = 100
        while True:
            ts_ms, msg = await q.get()
            if prev_ts is not None:
                delta = ts_ms - prev_ts
                if 0 < delta <= max_catchup:
                    await asyncio.sleep(delta / 1000.0)
            sent.append((time.monotonic(), msg))
            prev_ts = ts_ms

    task = asyncio.create_task(paced_forwarder())
    q.put_nowait((100, (0xB0, 7, 1)))         # batch at +0ms
    q.put_nowait((115, (0xB0, 7, 2)))         #            +15ms
    q.put_nowait((130, (0xB0, 7, 3)))         #            +30ms
    await asyncio.sleep(0.12)
    task.cancel()
    return sent


sent = asyncio.run(paced_run())
check("paced forwarder sent all", len(sent) == 3)
if len(sent) == 3:
    d1 = sent[1][0] - sent[0][0]
    d2 = sent[2][0] - sent[1][0]
    check("~15ms pacing msg1->msg2", 0.008 < d1 < 0.05)
    check("~15ms pacing msg2->msg3", 0.008 < d2 < 0.05)
    print("   measured gaps: %.1f ms, %.1f ms" % (d1 * 1000, d2 * 1000))

print()
print("RESULT:", "ALL PASS" if failures == 0 else "%d FAILURES" % failures)
sys.exit(1 if failures else 0)
