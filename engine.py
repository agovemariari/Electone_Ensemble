import json
import time
import threading
from dataclasses import dataclass, field
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
    phrase_steps: List["PhraseStep"] = field(default_factory=list)
    phrase_gate_clocks: Optional[int] = None


@dataclass
class PhraseStep:
    offset_clocks: int
    notes: List[int]
    velocity: Optional[int] = None
    gate_clocks: Optional[int] = None


@dataclass
class ScheduledOn:
    due_clock: int
    channel: int
    notes: List[int]
    velocity: int
    gate_clocks: Optional[int]


@dataclass
class ScheduledOff:
    due_clock: int
    channel: int
    note: int


@dataclass
class ScheduledCC:
    due_clock: int
    channel: int
    control: int
    value: int


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

        self.active_notes: Dict[int, List[int]] = {}
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
        self.clock_total = 0

        self._scheduled_on: List[ScheduledOn] = []
        self._scheduled_off: List[ScheduledOff] = []
        self._scheduled_cc: List[ScheduledCC] = []

        self.score_bars = 1
        self.score_division = 16
        self.score_steps_per_bar = 16
        self.score_clocks_per_step = 6
        self.score_gate_clocks: Optional[int] = None
        self.score_cues: Dict[str, Dict[int, List[List[int]]]] = {}
        self.score_cc: Dict[str, Dict[int, Dict[int, List[Tuple[int, int]]]]] = {}
        self.score_gates: Dict[str, Dict[int, List[int]]] = {}

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
        self._load_score()

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

            notes = [note_to_int(n) for n in act.get("notes", [])]
            vel = int(act.get("velocity", 80))
            name = m.get("name", "")

            phrase_steps, phrase_gate = self._parse_phrase(act)

            self.mappings[(ch0, note)] = Action(
                notes=notes,
                velocity=vel,
                name=name,
                phrase_steps=phrase_steps,
                phrase_gate_clocks=phrase_gate,
            )

        self.log(f"[MAP] エントリ数: {len(self.mappings)} / 出力CH: {self.out_channel}")

    def _parse_unit_clocks(self, unit: Any) -> Optional[int]:
        if unit is None:
            return None
        if isinstance(unit, int):
            return max(1, unit)
        if isinstance(unit, str):
            s = unit.strip().lower()
            if s.isdigit():
                return max(1, int(s))
            if s.endswith("n") and s[:-1].isdigit():
                note_value = int(s[:-1])
                if note_value > 0:
                    return max(1, int(round(self.ppqn * 4 / note_value)))
        return None

    def _coerce_notes(self, notes_field: Any) -> List[int]:
        if isinstance(notes_field, (int, str)):
            notes = [notes_field]
        elif isinstance(notes_field, list):
            notes = notes_field
        else:
            return []
        return [note_to_int(n) for n in notes]

    def _parse_phrase(self, act: Dict[str, Any]) -> Tuple[List[PhraseStep], Optional[int]]:
        phrase = act.get("phrase")
        if not isinstance(phrase, dict):
            return [], None

        phrase_gate = self._parse_unit_clocks(phrase.get("gate"))
        if phrase_gate is None:
            phrase_gate = self._parse_unit_clocks(phrase.get("gate_clocks"))

        unit_clocks = self._parse_unit_clocks(phrase.get("unit"))
        if unit_clocks is None:
            unit_clocks = self._parse_unit_clocks(phrase.get("unit_clocks"))

        steps: List[PhraseStep] = []

        if "steps" in phrase:
            for step in phrase.get("steps", []):
                if not isinstance(step, dict):
                    continue
                offset = step.get("offset_clocks")
                if offset is None:
                    offset = step.get("offset")
                if offset is None and unit_clocks is not None and "offset_units" in step:
                    offset = int(step.get("offset_units", 0)) * unit_clocks
                if offset is None:
                    offset = 0

                notes = self._coerce_notes(step.get("notes", []))
                if not notes:
                    continue
                vel = step.get("velocity")
                gate = self._parse_unit_clocks(step.get("gate"))
                if gate is None:
                    gate = self._parse_unit_clocks(step.get("gate_clocks"))

                steps.append(PhraseStep(offset_clocks=int(offset), notes=notes, velocity=vel, gate_clocks=gate))

        elif "pattern" in phrase and unit_clocks is not None:
            for i, item in enumerate(phrase.get("pattern", [])):
                if item in (None, "", "-", "rest"):
                    continue
                vel = None
                gate = None
                if isinstance(item, dict):
                    notes = self._coerce_notes(item.get("notes", []))
                    vel = item.get("velocity")
                    gate = self._parse_unit_clocks(item.get("gate"))
                    if gate is None:
                        gate = self._parse_unit_clocks(item.get("gate_clocks"))
                else:
                    notes = self._coerce_notes(item)
                if not notes:
                    continue
                steps.append(PhraseStep(
                    offset_clocks=i * unit_clocks,
                    notes=notes,
                    velocity=vel,
                    gate_clocks=gate,
                ))

        return steps, phrase_gate

    def _parse_step_notes(self, step: Any) -> List[int]:
        if step is None:
            return []
        if isinstance(step, list):
            items = step
        elif isinstance(step, str):
            s = step.strip()
            if not s:
                return []
            items = [x for x in s.replace(",", " ").split() if x]
        else:
            items = [step]
        notes: List[int] = []
        for n in items:
            try:
                notes.append(note_to_int(n))
            except Exception as e:
                self.log(f"[SCORE] invalid note '{n}': {e}")
        return notes

    def _parse_cc_points(self, raw_points: Any, total_steps: int) -> List[Tuple[int, int]]:
        points: List[Tuple[int, int]] = []
        if raw_points is None:
            return points
        if isinstance(raw_points, dict):
            items = raw_points.items()
        elif isinstance(raw_points, list):
            items = raw_points
        else:
            return points

        def add_point(step_val: Any, value_val: Any):
            try:
                step = int(step_val)
                value = clamp(int(value_val), 0, 127)
            except Exception:
                return
            if step > 0:
                step_idx = step - 1
            else:
                step_idx = step
            if 0 <= step_idx < total_steps:
                points.append((step_idx, value))

        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    add_point(item.get("step", 0), item.get("value", 0))
                elif isinstance(item, (list, tuple)) and len(item) >= 2:
                    add_point(item[0], item[1])
        else:
            for k, v in items:
                add_point(k, v)

        points.sort(key=lambda x: x[0])
        return points

    def _load_score(self):
        score = self.song.get("score", {})
        try:
            bars = int(score.get("bars", 1))
        except Exception:
            bars = 1
        try:
            division = int(score.get("division", 16))
        except Exception:
            division = 16
        try:
            beats_per_bar = int(score.get("beats_per_bar", self.beats_per_bar))
        except Exception:
            beats_per_bar = self.beats_per_bar

        bars = max(1, bars)
        division = max(1, division)

        self.score_bars = bars
        self.score_division = division
        self.score_steps_per_bar = division
        clocks_per_bar = self.ppqn * beats_per_bar
        self.score_clocks_per_step = max(1, int(round(clocks_per_bar / division)))
        self.beats_per_bar = beats_per_bar

        score_gate = score.get("gate")
        if score_gate is None:
            score_gate = score.get("gate_clocks")
        gate_clocks = self._parse_unit_clocks(score_gate)
        if gate_clocks is None:
            gate_clocks = self.score_clocks_per_step
        self.score_gate_clocks = gate_clocks

        total_steps = bars * self.score_steps_per_bar
        cues_raw = score.get("cues", {})
        cues: Dict[str, Dict[int, List[List[int]]]] = {}
        cc_map: Dict[str, Dict[int, Dict[int, List[Tuple[int, int]]]]] = {}
        gates_map: Dict[str, Dict[int, List[int]]] = {}

        if isinstance(cues_raw, dict):
            for cue_name, cue_data in cues_raw.items():
                if not isinstance(cue_data, dict):
                    continue
                ch_map = cue_data.get("channels", {})
                if not isinstance(ch_map, dict):
                    continue
                cue_channels: Dict[int, List[List[int]]] = {}
                for ch_key, steps in ch_map.items():
                    try:
                        ch = int(ch_key)
                    except Exception:
                        continue
                    if not (1 <= ch <= 16):
                        continue
                    if not isinstance(steps, list):
                        continue
                    norm_steps: List[List[int]] = []
                    for step in steps[:total_steps]:
                        notes = self._parse_step_notes(step)
                        norm_steps.append(notes)
                    while len(norm_steps) < total_steps:
                        norm_steps.append([])
                    cue_channels[ch] = norm_steps
                cue_key = str(cue_name)
                cues[cue_key] = cue_channels

                gates_raw = cue_data.get("gates", {})
                cue_gates: Dict[int, List[int]] = {}
                if isinstance(gates_raw, dict):
                    for ch_key, gates_list in gates_raw.items():
                        try:
                            ch = int(ch_key)
                        except Exception:
                            continue
                        if not (1 <= ch <= 16):
                            continue
                        if not isinstance(gates_list, list):
                            continue
                        norm_gates: List[int] = []
                        for gate in gates_list[:total_steps]:
                            try:
                                gate_val = int(gate)
                            except Exception:
                                gate_val = 0
                            norm_gates.append(max(0, gate_val))
                        while len(norm_gates) < total_steps:
                            norm_gates.append(0)
                        cue_gates[ch] = norm_gates
                if cue_gates:
                    gates_map[cue_key] = cue_gates

                cc_raw = cue_data.get("cc", {})
                cue_cc: Dict[int, Dict[int, List[Tuple[int, int]]]] = {}
                if isinstance(cc_raw, dict):
                    for ch_key, ch_data in cc_raw.items():
                        try:
                            ch = int(ch_key)
                        except Exception:
                            continue
                        if not (1 <= ch <= 16):
                            continue
                        if not isinstance(ch_data, dict):
                            continue
                        cc_by_num: Dict[int, List[Tuple[int, int]]] = {}
                        for cc_key, points_raw in ch_data.items():
                            try:
                                cc_num = int(cc_key)
                            except Exception:
                                continue
                            if not (0 <= cc_num <= 127):
                                continue
                            points = self._parse_cc_points(points_raw, total_steps)
                            if points:
                                cc_by_num[cc_num] = points
                        if cc_by_num:
                            cue_cc[ch] = cc_by_num
                if cue_cc:
                    cc_map[cue_key] = cue_cc

        self.score_cues = cues
        self.score_cc = cc_map
        self.score_gates = gates_map

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
        channel_configs: Dict[int, Dict[str, Any]] = {}
        if isinstance(patch.get("channels"), dict):
            for k, v in patch.get("channels", {}).items():
                try:
                    ch = int(k)
                except Exception:
                    continue
                if 1 <= ch <= 16 and isinstance(v, dict):
                    channel_configs[ch] = v

        out_channels = patch.get("out_channels")
        if channel_configs:
            channels = sorted(channel_configs.keys())
        elif out_channels is None:
            channels = [self.out_channel]
        elif isinstance(out_channels, str):
            parts = [p.strip() for p in out_channels.replace(",", " ").split() if p.strip()]
            channels = [int(p) for p in parts]
        elif isinstance(out_channels, list):
            channels = [int(c) for c in out_channels]
        else:
            channels = [self.out_channel]

        # GM System On: F0 7E 7F 09 01 F7
        self.out_port.send(mido.Message("sysex", data=[0x7E, 0x7F, 0x09, 0x01]))
        self.log("[GM] GM System On")
        time.sleep(0.05)

        # Reset All Controllers: CC121 = 0
        for ch1 in channels:
            ch = ch1 - 1
            self.out_port.send(mido.Message("control_change", channel=ch, control=121, value=0))
        self.log("[GM] Reset All Controllers")

        def _cfg_val(cfg: Dict[str, Any], key: str, default: int) -> int:
            try:
                return clamp(int(cfg.get(key, default)), 0, 127)
            except Exception:
                return clamp(int(default), 0, 127)

        # Bank Select
        for ch1 in channels:
            cfg = channel_configs.get(ch1, {})
            bank_msb = _cfg_val(cfg, "bank_msb", int(patch.get("bank_msb", 0)))
            bank_lsb = _cfg_val(cfg, "bank_lsb", int(patch.get("bank_lsb", 0)))
            ch = ch1 - 1
            self.out_port.send(mido.Message("control_change", channel=ch, control=0, value=bank_msb))
            self.out_port.send(mido.Message("control_change", channel=ch, control=32, value=bank_lsb))
        self.log("[GM] Bank Select")

        # Program Change
        for ch1 in channels:
            cfg = channel_configs.get(ch1, {})
            program = _cfg_val(cfg, "program", int(patch.get("program", 48)))
            ch = ch1 - 1
            self.out_port.send(mido.Message("program_change", channel=ch, program=program))
        self.log("[GM] Program Change")

        # Volume / Expression / Pan
        for ch1 in channels:
            cfg = channel_configs.get(ch1, {})
            volume = _cfg_val(cfg, "volume", int(patch.get("volume", 100)))
            expression = _cfg_val(cfg, "expression", int(patch.get("expression", 127)))
            pan = _cfg_val(cfg, "pan", int(patch.get("pan", 64)))
            ch = ch1 - 1
            self.out_port.send(mido.Message("control_change", channel=ch, control=7, value=volume))
            self.out_port.send(mido.Message("control_change", channel=ch, control=11, value=expression))
            self.out_port.send(mido.Message("control_change", channel=ch, control=10, value=pan))
        self.log("[GM] Vol/Expr/Pan")

        # Reverb / Chorus
        for ch1 in channels:
            cfg = channel_configs.get(ch1, {})
            reverb = _cfg_val(cfg, "reverb", int(patch.get("reverb", 40)))
            chorus = _cfg_val(cfg, "chorus", int(patch.get("chorus", 0)))
            ch = ch1 - 1
            self.out_port.send(mido.Message("control_change", channel=ch, control=91, value=reverb))
            self.out_port.send(mido.Message("control_change", channel=ch, control=93, value=chorus))
        self.log("[GM] Reverb/Chorus")

    # ---------- Note control ----------
    def all_notes_off(self):
        if not self.out_port:
            return
        total = 0
        for ch, notes in list(self.active_notes.items()):
            for n in notes:
                self.out_port.send(mido.Message("note_off", channel=ch, note=n, velocity=0))
                total += 1
        if total:
            self.log(f"[OFF] {total} 音を停止")
        self.active_notes.clear()
        self._scheduled_on.clear()
        self._scheduled_off.clear()
        self._scheduled_cc.clear()

    def _note_on(self, channel: int, note: int, vel: int):
        if not self.out_port:
            return
        self.out_port.send(mido.Message("note_on", channel=channel, note=note, velocity=vel))
        self.active_notes.setdefault(channel, []).append(note)

    def _note_off(self, channel: int, note: int):
        if not self.out_port:
            return
        self.out_port.send(mido.Message("note_off", channel=channel, note=note, velocity=0))
        notes = self.active_notes.get(channel)
        if notes and note in notes:
            notes.remove(note)
            if not notes:
                self.active_notes.pop(channel, None)

    def _emit_notes(self, channel: int, notes: List[int], vel: int, gate_clocks: Optional[int]):
        for n in notes:
            self._note_on(channel, n, vel)
            if gate_clocks is not None and gate_clocks > 0:
                self._scheduled_off.append(ScheduledOff(
                    due_clock=self.clock_total + gate_clocks,
                    channel=channel,
                    note=n,
                ))

    def _send_cc(self, channel: int, control: int, value: int):
        if not self.out_port:
            return
        self.out_port.send(mido.Message("control_change", channel=channel, control=control, value=value))

    def play_action(self, action: Action):
        if not self.out_port:
            raise RuntimeError("MIDI OUTが開かれていません。")
        self.all_notes_off()

        vel = clamp(int(action.velocity), 1, 127)
        self._emit_notes(self.out_channel_0, action.notes, vel, None)

        nm = f" ({action.name})" if action.name else ""
        self.log(f"[ON] CH{self.out_channel} notes={action.notes} vel={vel}{nm}")

    def test_play_notes(self, notes: List[int], velocity: int = 90, duration_ms: int = 400):
        """UIの[Test]用。短く鳴らして止める。"""
        if not self.out_port:
            raise RuntimeError("MIDI OUTが開かれていません。")
        self.all_notes_off()
        vel = clamp(int(velocity), 1, 127)
        for n in notes:
            self._note_on(self.out_channel_0, n, vel)
        self.log(f"[TEST] notes={notes} vel={vel} {duration_ms}ms")
        time.sleep(duration_ms / 1000.0)
        self.all_notes_off()

    def _start_phrase(self, action: Action):
        self.all_notes_off()
        base_clock = self.clock_total
        gate_default = action.phrase_gate_clocks

        for step in action.phrase_steps:
            vel = clamp(int(step.velocity if step.velocity is not None else action.velocity), 1, 127)
            gate = step.gate_clocks if step.gate_clocks is not None else gate_default
            offset = max(0, int(step.offset_clocks))
            due = base_clock + offset
            if offset <= 0:
                self._emit_notes(self.out_channel_0, step.notes, vel, gate)
            else:
                self._scheduled_on.append(ScheduledOn(
                    due_clock=due,
                    channel=self.out_channel_0,
                    notes=step.notes,
                    velocity=vel,
                    gate_clocks=gate,
                ))

        nm = f" ({action.name})" if action.name else ""
        self.log(f"[PHRASE] CH{self.out_channel} steps={len(action.phrase_steps)}{nm}")

    def _start_score(self, cue_name: str, velocity: int) -> bool:
        cue = self.score_cues.get(cue_name)
        cue_cc = self.score_cc.get(cue_name, {})
        cue_gates = self.score_gates.get(cue_name, {})
        if not cue and not cue_cc:
            return False
        self.all_notes_off()
        base_clock = self.clock_total
        gate = self.score_gate_clocks
        total_steps = self.score_bars * self.score_steps_per_bar

        if cue:
            for ch, steps in cue.items():
                gates = cue_gates.get(ch, [])
                for idx in range(min(len(steps), total_steps)):
                    notes = steps[idx]
                    if not notes:
                        continue
                    gate_override = None
                    if idx < len(gates) and gates[idx] > 0:
                        gate_override = gates[idx] * self.score_clocks_per_step
                    due = base_clock + (idx * self.score_clocks_per_step)
                    if due <= base_clock:
                        self._emit_notes(ch - 1, notes, velocity, gate_override or gate)
                    else:
                        self._scheduled_on.append(ScheduledOn(
                            due_clock=due,
                            channel=ch - 1,
                            notes=notes,
                            velocity=velocity,
                            gate_clocks=gate_override or gate,
                        ))

        if cue_cc:
            for ch, cc_by_num in cue_cc.items():
                for cc_num, points in cc_by_num.items():
                    for step_idx, value in points:
                        due = base_clock + (step_idx * self.score_clocks_per_step)
                        if due <= base_clock:
                            self._send_cc(ch - 1, cc_num, value)
                        else:
                            self._scheduled_cc.append(ScheduledCC(
                                due_clock=due,
                                channel=ch - 1,
                                control=cc_num,
                                value=value,
                            ))

        self.log(f"[SCORE] cue={cue_name} steps={total_steps} gate={gate}")
        return True

    def _process_scheduled(self):
        if self._scheduled_on:
            due_on = [s for s in self._scheduled_on if s.due_clock <= self.clock_total]
            if due_on:
                self._scheduled_on = [s for s in self._scheduled_on if s.due_clock > self.clock_total]
                for s in due_on:
                    self._emit_notes(s.channel, s.notes, s.velocity, s.gate_clocks)

        if self._scheduled_off:
            due_off = [s for s in self._scheduled_off if s.due_clock <= self.clock_total]
            if due_off:
                self._scheduled_off = [s for s in self._scheduled_off if s.due_clock > self.clock_total]
                for s in due_off:
                    self._note_off(s.channel, s.note)

        if self._scheduled_cc:
            due_cc = [s for s in self._scheduled_cc if s.due_clock <= self.clock_total]
            if due_cc:
                self._scheduled_cc = [s for s in self._scheduled_cc if s.due_clock > self.clock_total]
                for s in due_cc:
                    self._send_cc(s.channel, s.control, s.value)

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
                    self.clock_total += 1
                    clocks_per_bar = self.ppqn * self.beats_per_bar  # 24 * 4 = 96
                    if self.clock_count >= clocks_per_bar:
                        self.clock_count = 0
                        self.current_bar += 1
                        if self.on_bar:
                            self.on_bar(self.current_bar)
                        self.log(f"[BAR] {self.current_bar}")
                    self._process_scheduled()
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
                        if action.name and self._start_score(action.name, clamp(int(action.velocity), 1, 127)):
                            continue
                        if action.phrase_steps:
                            self._start_phrase(action)
                        else:
                            self.play_action(action)

        except Exception as e:
            self.log(f"[ERR] {e}")
        finally:
            self.all_notes_off()
