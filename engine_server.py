import asyncio
import json
import websockets
import mido
from mido import Message

MIDI_IN = None
MIDI_OUT = None

async def handle(ws):
    global MIDI_IN, MIDI_OUT
    print("UI connected")

    async def send_ports():
        await ws.send(json.dumps({
            "type": "ports",
            "inputs": mido.get_input_names(),
            "outputs": mido.get_output_names()
        }))

    await send_ports()


    async for msg in ws:
        print("RX RAW:", msg)

        data = json.loads(msg)
        msg_type = data.get("type") or data.get("cmd") or data.get("action")

        print("RX RAW:", data)

        # --- デバイス一覧要求 ---
        if msg_type == "get_devices":
            inputs = mido.get_input_names()
            outputs = mido.get_output_names()
            await ws.send(json.dumps({
                "type": "devices",
                "inputs": inputs,
                "outputs": outputs,
                # 保険（UI側の実装揺れ対策）
                "midiInputs": inputs,
                "midiOutputs": outputs,
            }))
            continue

        # --- MIDI接続 ---
        if msg_type in ("connect", "midi_connect"):
            in_name = data.get("midiIn") or data.get("input")
            out_name = data.get("midiOut") or data.get("output")

            if not in_name or not out_name:
                await ws.send(json.dumps({
                    "type": "status",
                    "ok": False,
                    "message": "Select MIDI IN / OUT"
                }))
                continue

            MIDI_IN = mido.open_input(in_name)
            MIDI_OUT = mido.open_output(out_name)

            await ws.send(json.dumps({
                "type": "status",
                "ok": True,
                "message": f"MIDI connected: {in_name} -> {out_name}"
            }))
            print("MIDI connected:", in_name, out_name)
            continue

        # --- GM System Reset ---
        if msg_type in ("gm_reset", "gm_init", "gmSystemReset"):
            MIDI_OUT.send(
                Message('sysex', data=[0x7E, 0x7F, 0x09, 0x01])
            )
            await ws.send(json.dumps({"type": "log", "message": "GM Init sent"}))
            continue

        # --- Note On / Off（将来 Cue から来る）---
        if msg_type == "note_on":
            MIDI_OUT.send(Message(
                'note_on',
                channel=data["ch"] - 1,
                note=data["note"],
                velocity=data.get("vel", 80)
            ))
            continue

        if msg_type == "note_off":
            MIDI_OUT.send(Message(
                'note_off',
                channel=data["ch"] - 1,
                note=data["note"],
                velocity=0
            ))
            continue

        # --- Panic ---
        if msg_type == "panic":
            for ch in range(16):
                MIDI_OUT.send(
                    Message('control_change', channel=ch, control=123, value=0)
                )
            continue

async def main():
    print("MIDI Engine WS listening on ws://localhost:8787")
    async with websockets.serve(handle, "localhost", 8787):
        await asyncio.Future()

asyncio.run(main())
