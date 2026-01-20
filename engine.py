import json
import time
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple, Any

# Try to import mido, but allow graceful fallback
try:
    import mido
    HAS_MIDI = True
except ImportError:
    HAS_MIDI = False


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
    if not HAS_MIDI:
        return ["[Mock] Virtual Input"], ["[Mock] Virtual Output"]
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
    countermelody: Optional[Dict[str, Any]] = None


class CountermelodyScheduler:
    """
    Manages the countermelody timing and playback.
    Countermelody consists of notes with specific timing (in MIDI clocks).
    """

    def __init__(self, countermelody_config: Dict[str, Any], ppqn: int = 24):
        self.config = countermelody_config
        self.ppqn = ppqn
        self.bar_length = countermelody_config.get("bar_length", 1)
        self.sequence = countermelody_config.get("sequence", [])
        
        # Calculate total clocks in the bar (24 clocks/beat * 4 beats/bar = 96 clocks)
        self.bar_duration_clocks = ppqn * 4 * self.bar_length
        
        self.start_clock: Optional[int] = None
        self.active_notes: Dict[Tuple[int, int], int] = {}  # (note, velocity) -> end_clock
        self.current_index = 0
        self.is_active = False

    def start(self, current_clock: int):
        """Start the countermelody at the given clock."""
        self.start_clock = current_clock
        self.current_index = 0
        self.active_notes.clear()
        self.is_active = True

    def stop(self):
        """Stop the countermelody and clear active notes."""
        self.active_notes.clear()
        self.is_active = False

    def get_events_at_clock(self, current_clock: int) -> Tuple[List[Tuple[int, int]], List[int]]:
        """
        Returns (note_ons, note_offs) at the given clock.
        note_ons: list of (note, velocity)
        note_offs: list of notes to turn off
        """
        if not self.is_active or self.start_clock is None:
            return [], []

        elapsed = current_clock - self.start_clock
        
        # Check if countermelody has ended
        if elapsed >= self.bar_duration_clocks:
            self.stop()
            return [], []

        note_ons = []
        note_offs = []

        # Check for notes that should turn off at this clock
        for (note, vel), end_clock in list(self.active_notes.items()):
            if current_clock >= end_clock:
                note_offs.append(note)
                del self.active_notes[(note, vel)]

        # Check for new notes to turn on
        while self.current_index < len(self.sequence):
            event = self.sequence[self.current_index]
            event_abs_clock = event.get("timing_clock", 0)
            
            if event_abs_clock == elapsed:
                # This event should happen now
                notes_list = event.get("notes", [])
                event_vel = event.get("velocity", 70)
                duration = event.get("duration_clock", 12)
                end_clock = current_clock + duration

                for note in notes_list:
                    note_ons.append((note, event_vel))
                    self.active_notes[(note, event_vel)] = end_clock

                self.current_index += 1
            elif event_abs_clock > elapsed:
                break
            else:
                self.current_index += 1

        return note_ons, note_offs


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

        # --- Countermelody ---
        self.countermelody_scheduler: Optional[CountermelodyScheduler] = None
        self.countermelody_active_notes: List[int] = []

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
            countermelody = m.get("countermelody", None)

            self.mappings[(ch0, note)] = Action(
                notes=notes,
                velocity=vel,
                name=name,
                countermelody=countermelody
            )

        self.log(f"[MAP] エントリ数: {len(self.mappings)} / 出力CH: {self.out_channel}")

    # ---------- MIDI setup ----------
    def open_ports(self, in_port: str, out_port: str):
        self.in_port_name = in_port
        self.out_port_name = out_port

        if not HAS_MIDI:
            self.log(f"[WARN] MIDIが利用不可（mido/python-rtmidiなし）。モード: {in_port} → {out_port}")
            self.in_port = None
            self.out_port = None
            return

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

    def _start_countermelody(self, countermelody_config: Dict[str, Any]):
        """Start a new countermelody sequence."""
        if not countermelody_config.get("enabled", False):
            return
        self.countermelody_scheduler = CountermelodyScheduler(countermelody_config, self.ppqn)
        self.countermelody_scheduler.start(self.clock_count)
        self.log(f"[CM] 対旋律開始 @ clock={self.clock_count}")

    def _stop_countermelody(self):
        """Stop the current countermelody."""
        if self.countermelody_scheduler:
            # Turn off active countermelody notes
            for n in self.countermelody_active_notes:
                if self.out_port:
                    self.out_port.send(mido.Message("note_off", channel=self.out_channel_0, note=n, velocity=0))
            self.countermelody_active_notes.clear()
            self.countermelody_scheduler.stop()
            self.countermelody_scheduler = None
            self.log("[CM] 対旋律停止")

    def _update_countermelody(self):
        """Update countermelody at current clock. Called from main loop."""
        if not self.countermelody_scheduler:
            return
        if not self.out_port:
            return

        note_ons, note_offs = self.countermelody_scheduler.get_events_at_clock(self.clock_count)

        # Turn off notes
        for note in note_offs:
            self.out_port.send(mido.Message("note_off", channel=self.out_channel_0, note=note, velocity=0))
            if note in self.countermelody_active_notes:
                self.countermelody_active_notes.remove(note)

        # Turn on notes
        for note, velocity in note_ons:
            vel = clamp(int(velocity), 1, 127)
            self.out_port.send(mido.Message("note_on", channel=self.out_channel_0, note=note, velocity=vel))
            self.countermelody_active_notes.append(note)

        # Check if countermelody ended
        if not self.countermelody_scheduler.is_active:
            self._stop_countermelody()

    def play_action(self, action: Action):
        if not self.out_port:
            raise RuntimeError("MIDI OUTが開かれていません。")
        self.all_notes_off()
        self._stop_countermelody()

        vel = clamp(int(action.velocity), 1, 127)
        for n in action.notes:
            self.out_port.send(mido.Message("note_on", channel=self.out_channel_0, note=n, velocity=vel))
            self.active_notes.append(n)

        nm = f" ({action.name})" if action.name else ""
        self.log(f"[ON] CH{self.out_channel} notes={action.notes} vel={vel}{nm}")

        # Start countermelody if enabled
        if action.countermelody and action.countermelody.get("enabled", False):
            self._start_countermelody(action.countermelody)

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
        self._stop_countermelody()
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

                # --- Clock -> Bar counter + Countermelody update ---
                if msg.type == "clock":
                    self.clock_count += 1
                    
                    # Update countermelody timing
                    self._update_countermelody()
                    
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
            self._stop_countermelody()
