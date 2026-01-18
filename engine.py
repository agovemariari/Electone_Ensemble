import json
import time
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple, Any

import mido


NOTE_NAME_TO_SEMI = {
    "C": 0, "C#": 1, "Db": 1,
    "D": 2, "D#": 3, "Eb": 3,
    "E": 4,
    "F": 5, "F#": 6, "Gb": 6,
    "G": 7, "G#": 8, "Ab": 8,
    "A": 9, "A#": 10, "Bb": 10,
    "B": 11,
}


def note_to_int(note) -> int:
    """Accept int MIDI note or string like 'C4', 'F#3', 'Bb2'."""
    if isinstance(note, int):
        if 0 <= note <= 127:
            return note
        raise ValueError(f"Invalid MIDI note int: {note}")

    if isinstance(note, str):
        s = note.strip()
        # allow numeric string
        if s.isdigit():
            v = int(s)
            if 0 <= v <= 127:
                return v
            raise ValueError(f"Invalid MIDI note numeric string: {note}")

        # parse pitch+octave e.g. C#4, Bb2, C-1
        if len(s) < 2:
            raise ValueError(f"Invalid note string: {note}")

        # try last 2 chars as octave (for negative)
        pitch, octv_str = s[:-1], s[-1]
        try:
            octv = int(octv_str)
        except ValueError:
            pitch, octv_str = s[:-2], s[-2:]
            octv = int(octv_str)

        if pitch not in NOTE_NAME_TO_SEMI:
            raise ValueError(f"Invalid pitch name: {pitch} in {note}")

        sem = NOTE_NAME_TO_SEMI[pitch]
        midi = (octv + 1) * 12 + sem
        if not (0 <= midi <= 127):
            raise ValueError(f"Out-of-range MIDI note: {note} -> {midi}")
        return midi

    raise ValueError(f"Invalid note type: {type(note)}")


def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def list_midi_ports() -> Tuple[List[str], List[str]]:
    return mido.get_input_names(), mido.get_output_names()


def load_song(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_song(path: str, song: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(song, f, ensure_ascii=False, indent=2)


@dataclass
class Action:
    notes: List[int]
    velocity: int
    name: str = ""


class DuetEngine:
    """
    Trigger-based accompaniment engine.
    NOTE OFF policy: next trigger -> all off -> new on.

    - Runs in a background thread (start/stop).
    - Provides callbacks for UI: on_log, on_trigger (learn mode).
    """

    def __init__(self):
        self.song: Dict[str, Any] = {}
        self.in_port_name: Optional[str] = None
        self.out_port_name: Optional[str] = None

        self.in_port = None
        self.out_port = None

        self._thread: Optional[threading.Thread] = None
        self._stop_flag = threading.Event()

        self.active_notes: List[int] = []
        self.mappings: Dict[Tuple[int, int], Action] = {}

        self.out_channel = 4
        self.out_channel_0 = 3

        self.on_log: Optional[Callable[[str], None]] = None

        # Learn trigger mode
        self._learn_enabled = False
        self.on_learn: Optional[Callable[[int, int, int], None]] = None  # (ch1..16, note, vel)

        # --- Bar (measure) counter ---
        self.ppqn = 24            # MIDI Clock: 24 per quarter note
        self.beats_per_bar = 4    # ひとまず4/4固定
        self.clock_count = 0
        self.current_bar = 1

        # UIへ通知（barが進んだら呼ぶ）
        self.on_bar: Optional[Callable[[int], None]] = None

    # ---------- Logging ----------
    def log(self, s: str):
        if self.on_log:
            self.on_log(s)

    # ---------- Song / Mapping ----------
    def set_song(self, song: Dict[str, Any]):
        self.song = song
        self._rebuild_mapping()

    def _rebuild_mapping(self):
        self.mappings.clear()
        patch = self.song.get("patch", {})
        self.out_channel = int(patch.get("out_channel", 4))
        self.out_channel_0 = self.out_channel - 1

        for m in self.song.get("mappings", []):
            trig = m["trigger"]
            act = m["action"]
            ch0 = int(trig["ch"]) - 1
            note = note_to_int(trig["note"])

            notes = [note_to_int(n) for n in act["notes"]]
            vel = int(act.get("velocity", 80))
            name = m.get("name", "")

            self.mappings[(ch0, note)] = Action(notes=notes, velocity=vel, name=name)

        self.log(f"[MAP] エントリ数: {len(self.mappings)} / 出力CH: {self.out_channel}")

    # ---------- MIDI setup ----------
    def open_ports(self, in_port: str, out_port: str):
        self.in_port_name = in_port
        self.out_port_name = out_port

        if self.in_port:
            self.in_port.close()
            self.in_port = None
        if self.out_port:
            self.out_port.close()
            self.out_port = None

        self.in_port = mido.open_input(in_port)
        self.out_port = mido.open_output(out_port)
        self.log(f"[MIDI] IN='{in_port}' / OUT='{out_port}'")

    # ---------- GM Init (Domino相当) ----------
    def gm_reset_and_init(self):
        if not self.out_port:
            raise RuntimeError("MIDI OUTが開かれていません。")

        patch = self.song.get("patch", {})
        ch = self.out_channel_0

        # GM System On: F0 7E 7F 09 01 F7
        self.out_port.send(mido.Message("sysex", data=[0x7E, 0x7F, 0x09, 0x01]))
        self.log("[GM] GM System On")
        time.sleep(0.05)

        # Reset All Controllers: CC121 = 0
        self.out_port.send(mido.Message("control_change", channel=ch, control=121, value=0))
        self.log("[GM] Reset All Controllers")

        bank_msb = clamp(int(patch.get("bank_msb", 0)), 0, 127)
        bank_lsb = clamp(int(patch.get("bank_lsb", 0)), 0, 127)
        program = clamp(int(patch.get("program", 48)), 0, 127)

        volume = clamp(int(patch.get("volume", 100)), 0, 127)
        expression = clamp(int(patch.get("expression", 127)), 0, 127)
        pan = clamp(int(patch.get("pan", 64)), 0, 127)
        reverb = clamp(int(patch.get("reverb", 40)), 0, 127)
        chorus = clamp(int(patch.get("chorus", 0)), 0, 127)

        # Bank Select
        self.out_port.send(mido.Message("control_change", channel=ch, control=0, value=bank_msb))
        self.out_port.send(mido.Message("control_change", channel=ch, control=32, value=bank_lsb))
        self.log(f"[GM] Bank MSB={bank_msb} LSB={bank_lsb}")

        # Program Change
        self.out_port.send(mido.Message("program_change", channel=ch, program=program))
        self.log(f"[GM] Program Change={program}")

        # Volume / Expression / Pan
        self.out_port.send(mido.Message("control_change", channel=ch, control=7, value=volume))
        self.out_port.send(mido.Message("control_change", channel=ch, control=11, value=expression))
        self.out_port.send(mido.Message("control_change", channel=ch, control=10, value=pan))
        self.log(f"[GM] Vol={volume} Expr={expression} Pan={pan}")

        # Reverb / Chorus
        self.out_port.send(mido.Message("control_change", channel=ch, control=91, value=reverb))
        self.out_port.send(mido.Message("control_change", channel=ch, control=93, value=chorus))
        self.log(f"[GM] Reverb={reverb} Chorus={chorus}")

    # ---------- Note control ----------
    def all_notes_off(self):
        if not self.out_port:
            return
        for n in self.active_notes:
            self.out_port.send(mido.Message("note_off", channel=self.out_channel_0, note=n, velocity=0))
        if self.active_notes:
            self.log(f"[OFF] {len(self.active_notes)} 音を停止")
        self.active_notes.clear()

    def play_action(self, action: Action):
        if not self.out_port:
            raise RuntimeError("MIDI OUTが開かれていません。")
        self.all_notes_off()

        vel = clamp(int(action.velocity), 1, 127)
        for n in action.notes:
            self.out_port.send(mido.Message("note_on", channel=self.out_channel_0, note=n, velocity=vel))
            self.active_notes.append(n)

        nm = f" ({action.name})" if action.name else ""
        self.log(f"[ON] CH{self.out_channel} notes={action.notes} vel={vel}{nm}")

    def test_play_notes(self, notes: List[int], velocity: int = 90, duration_ms: int = 400):
        """UIの[Test]用。短く鳴らして止める。"""
        if not self.out_port:
            raise RuntimeError("MIDI OUTが開かれていません。")
        self.all_notes_off()
        vel = clamp(int(velocity), 1, 127)
        for n in notes:
            self.out_port.send(mido.Message("note_on", channel=self.out_channel_0, note=n, velocity=vel))
            self.active_notes.append(n)
        self.log(f"[TEST] notes={notes} vel={vel} {duration_ms}ms")
        time.sleep(duration_ms / 1000.0)
        self.all_notes_off()

    # ---------- Learn trigger ----------
    def set_learn(self, enabled: bool, callback: Optional[Callable[[int, int, int], None]]):
        self._learn_enabled = enabled
        self.on_learn = callback
        self.log(f"[LEARN] {'ON' if enabled else 'OFF'}")

    # ---------- Running loop ----------
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running():
            return
        if not self.in_port or not self.out_port:
            raise RuntimeError("MIDI IN/OUTを先に開いてください。")

        self._stop_flag.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()
        self.log("[RUN] 開始")

    def stop(self):
        if not self.is_running():
            return
        self._stop_flag.set()
        # Wait a bit for thread to exit
        if self._thread:
            self._thread.join(timeout=1.0)
        self._thread = None
        self.all_notes_off()
        self.log("[RUN] 停止")

    def _run_loop(self):
        # NOTE: We rely on mido input iterator blocking; stop_flag checked per message.
        try:
            for msg in self.in_port:
                if self._stop_flag.is_set():
                    break

                # ignore Active Sensing spam
                if msg.type == "active_sensing":
                    continue

                # --- Clock -> Bar counter ---
                if msg.type == "clock":
                    self.clock_count += 1
                    clocks_per_bar = self.ppqn * self.beats_per_bar  # 24 * 4 = 96
                    if self.clock_count >= clocks_per_bar:
                        self.clock_count = 0
                        self.current_bar += 1
                        if self.on_bar:
                            self.on_bar(self.current_bar)
                        self.log(f"[BAR] {self.current_bar}")
                    continue

                # Learn trigger: first note_on only
                if self._learn_enabled and msg.type == "note_on" and msg.velocity and msg.velocity > 0:
                    if self.on_learn:
                        self.on_learn(msg.channel + 1, msg.note, msg.velocity)
                    # keep learn on until UI turns it off
                    continue

                # Trigger on note_on
                if msg.type == "note_on" and msg.velocity and msg.velocity > 0:
                    key = (msg.channel, msg.note)
                    action = self.mappings.get(key)
                    if action:
                        self.log(f"[TRIG] ch{msg.channel+1} note={msg.note} vel={msg.velocity}")
                        self.play_action(action)

        except Exception as e:
            self.log(f"[ERR] {e}")
        finally:
            self.all_notes_off()
