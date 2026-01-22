import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


PITCH_CLASS = {
    "C": 0,
    "D": 2,
    "E": 4,
    "F": 5,
    "G": 7,
    "A": 9,
    "B": 11,
}

TEMPO_WORDS = ("rit", "ritard", "rall", "accel")


@dataclass
class ParseOptions:
    max_tpq: int = 480


def _lcm(a: int, b: int) -> int:
    return abs(a * b) // math.gcd(a, b) if a and b else 0


def _compute_tpq(divisions: List[int], max_tpq: int) -> int:
    tpq = 1
    for d in divisions:
        tpq = _lcm(tpq, max(1, d))
    if tpq <= 0:
        tpq = 1
    if tpq > max_tpq:
        tpq = max_tpq
    return tpq


def _text(elem: Optional[ET.Element]) -> str:
    if elem is None or elem.text is None:
        return ""
    return elem.text.strip()


def _parse_pitch(pitch_el: Optional[ET.Element]) -> Optional[int]:
    if pitch_el is None:
        return None
    step = _text(pitch_el.find("step"))
    if step not in PITCH_CLASS:
        return None
    alter = _text(pitch_el.find("alter"))
    octave = _text(pitch_el.find("octave"))
    try:
        alter_val = int(alter) if alter else 0
        octave_val = int(octave)
    except Exception:
        return None
    midi = (octave_val + 1) * 12 + PITCH_CLASS[step] + alter_val
    if 0 <= midi <= 127:
        return midi
    return None


def _parse_time_sig(time_el: Optional[ET.Element]) -> Optional[Tuple[int, int]]:
    if time_el is None:
        return None
    beats = _text(time_el.find("beats"))
    beat_type = _text(time_el.find("beat-type"))
    try:
        return int(beats), int(beat_type)
    except Exception:
        return None


def _parse_key_sig(key_el: Optional[ET.Element]) -> Optional[Tuple[int, str]]:
    if key_el is None:
        return None
    fifths = _text(key_el.find("fifths"))
    mode = _text(key_el.find("mode")) or "major"
    try:
        return int(fifths), mode
    except Exception:
        return None


def _duration_to_ticks(duration: int, divisions: int, tpq: int) -> int:
    if divisions <= 0:
        return 0
    return int(round(duration * (tpq / divisions)))


def _measure_duration_ticks(num: int, den: int, tpq: int) -> int:
    return int(round(num * (tpq * 4 / den)))


def _matches_tempo_word(text: str) -> bool:
    lower = text.lower()
    return any(word in lower for word in TEMPO_WORDS)


def _collect_divisions(root: ET.Element) -> List[int]:
    divisions = []
    for part_el in root.findall("part"):
        for measure_el in part_el.findall("measure"):
            attr = measure_el.find("attributes")
            if attr is None:
                continue
            div_el = attr.find("divisions")
            try:
                divisions.append(int(_text(div_el)))
            except Exception:
                continue
    return divisions


def parse_musicxml(path: str, options: Optional[ParseOptions] = None) -> Dict:
    opts = options or ParseOptions()
    tree = ET.parse(path)
    root = tree.getroot()

    divisions_list = _collect_divisions(root)
    tpq = _compute_tpq(divisions_list, opts.max_tpq)

    score = {
        "tpq": tpq,
        "title": "",
        "composer": "",
        "time_sigs": [],
        "key_sigs": [],
        "tempos": [],
        "tempo_spans": [],
        "wedge_spans": [],
        "parts": [],
    }

    work_title = _text(root.find("work/work-title"))
    if work_title:
        score["title"] = work_title
    composer = _text(root.find("identification/creator[@type='composer']"))
    if composer:
        score["composer"] = composer

    part_meta: Dict[str, Dict] = {}
    for score_part in root.findall("part-list/score-part"):
        pid = score_part.attrib.get("id", "")
        name = _text(score_part.find("part-name")) or pid
        part_meta[pid] = {"id": pid, "name": name, "staves": 1}

    tempo_words: List[Tuple[int, str]] = []
    max_tick = 0

    for part_el in root.findall("part"):
        part_id = part_el.attrib.get("id", "")
        part_info = part_meta.get(part_id, {"id": part_id, "name": part_id, "staves": 1})
        measures = []

        current_divisions = divisions_list[0] if divisions_list else 1
        current_time = (4, 4)
        measure_start_tick = 0

        for measure_el in part_el.findall("measure"):
            attr = measure_el.find("attributes")
            if attr is not None:
                div_el = attr.find("divisions")
                if div_el is not None:
                    try:
                        current_divisions = int(_text(div_el))
                    except Exception:
                        pass
                time_sig = _parse_time_sig(attr.find("time"))
                if time_sig:
                    current_time = time_sig
                    score["time_sigs"].append(
                        {"tick": measure_start_tick, "num": time_sig[0], "den": time_sig[1]}
                    )
                key_sig = _parse_key_sig(attr.find("key"))
                if key_sig:
                    score["key_sigs"].append(
                        {"tick": measure_start_tick, "fifths": key_sig[0], "mode": key_sig[1]}
                    )
                staves_el = attr.find("staves")
                if staves_el is not None:
                    try:
                        part_info["staves"] = int(_text(staves_el))
                    except Exception:
                        pass

            num, den = current_time
            measure_duration = _measure_duration_ticks(num, den, tpq)
            events = []

            voice_cursor: Dict[str, int] = {}
            voice_last_tick: Dict[str, int] = {}

            for direction in measure_el.findall("direction"):
                offset_el = direction.find("offset")
                offset = 0
                if offset_el is not None:
                    try:
                        offset = int(_text(offset_el))
                    except Exception:
                        offset = 0
                dir_tick = measure_start_tick + _duration_to_ticks(offset, current_divisions, tpq)

                sound = direction.find("sound")
                if sound is not None and "tempo" in sound.attrib:
                    try:
                        bpm = float(sound.attrib["tempo"])
                        score["tempos"].append({"tick": dir_tick, "bpm": bpm})
                        events.append({"kind": "tempo", "tick": dir_tick, "data": {"bpm": bpm}})
                    except Exception:
                        pass

                words_el = direction.find("direction-type/words")
                words_text = _text(words_el)
                if words_text:
                    events.append({"kind": "words", "tick": dir_tick, "data": {"text": words_text}})
                    if _matches_tempo_word(words_text):
                        tempo_words.append((dir_tick, words_text))

                wedge_el = direction.find("direction-type/wedge")
                if wedge_el is not None:
                    wedge_type = wedge_el.attrib.get("type", "")
                    wedge_num = wedge_el.attrib.get("number")
                    staff_el = direction.find("staff")
                    staff = int(_text(staff_el) or "1")
                    events.append({
                        "kind": "wedge",
                        "tick": dir_tick,
                        "data": {
                            "type": wedge_type,
                            "number": wedge_num,
                            "staff": staff,
                            "part_id": part_id,
                        },
                    })

            for note_el in measure_el.findall("note"):
                is_rest = note_el.find("rest") is not None
                voice = _text(note_el.find("voice")) or "1"
                staff = _text(note_el.find("staff")) or "1"
                duration_el = note_el.find("duration")
                try:
                    duration_val = int(_text(duration_el))
                except Exception:
                    duration_val = 0
                duration_ticks = _duration_to_ticks(duration_val, current_divisions, tpq)
                is_chord = note_el.find("chord") is not None

                cursor = voice_cursor.get(voice, measure_start_tick)
                tick = voice_last_tick.get(voice, cursor) if is_chord else cursor

                if not is_chord:
                    voice_cursor[voice] = cursor + duration_ticks
                voice_last_tick[voice] = tick

                if is_rest:
                    continue

                pitch = _parse_pitch(note_el.find("pitch"))
                if pitch is None:
                    continue

                tie_start = note_el.find("tie[@type='start']") is not None
                tie_stop = note_el.find("tie[@type='stop']") is not None

                articulations = []
                articulations_el = note_el.find("notations/articulations")
                if articulations_el is not None:
                    for art in list(articulations_el):
                        if art.tag:
                            articulations.append(art.tag)

                events.append({
                    "tick": tick,
                    "duration": duration_ticks,
                    "pitch": pitch,
                    "voice": int(voice),
                    "staff": int(staff),
                    "tie": {"start": tie_start, "stop": tie_stop},
                    "articulations": articulations,
                })

            measure_number = int(measure_el.attrib.get("number", "0"))
            measures.append({
                "number": measure_number,
                "start_tick": measure_start_tick,
                "duration_ticks": measure_duration,
                "divisions": current_divisions,
                "events": events,
            })

            measure_start_tick += measure_duration
            max_tick = max(max_tick, measure_start_tick)

        score["parts"].append({
            "id": part_info["id"],
            "name": part_info["name"],
            "staves": part_info["staves"],
            "measures": measures,
        })

    score["tempos"].sort(key=lambda x: x["tick"])
    tempo_words.sort(key=lambda x: x[0])

    last_bpm = score["tempos"][0]["bpm"] if score["tempos"] else 120.0
    for tick, label in tempo_words:
        next_tempo = next((t for t in score["tempos"] if t["tick"] > tick), None)
        if next_tempo:
            end_tick = next_tempo["tick"]
            end_bpm = next_tempo["bpm"]
        else:
            end_tick = max_tick
            end_bpm = last_bpm
        prev_tempo = None
        for t in score["tempos"]:
            if t["tick"] <= tick:
                prev_tempo = t
            else:
                break
        start_bpm = prev_tempo["bpm"] if prev_tempo else last_bpm
        score["tempo_spans"].append({
            "start_tick": tick,
            "end_tick": end_tick,
            "start_bpm": start_bpm,
            "end_bpm": end_bpm,
            "label": label,
        })

    wedge_open: Dict[Tuple[str, Optional[str], int], Dict] = {}
    for part in score["parts"]:
        for measure in part["measures"]:
            for ev in measure["events"]:
                if ev.get("kind") != "wedge":
                    continue
                data = ev["data"]
                key = (data.get("part_id", ""), data.get("number"), int(data.get("staff", 1)))
                wtype = data.get("type")
                if wtype in ("crescendo", "diminuendo"):
                    wedge_open[key] = {
                        "start_tick": ev["tick"],
                        "type": wtype,
                        "staff": data.get("staff", 1),
                        "part_id": data.get("part_id", ""),
                    }
                elif wtype == "stop":
                    start = wedge_open.pop(key, None)
                    if start:
                        score["wedge_spans"].append({
                            "start_tick": start["start_tick"],
                            "end_tick": ev["tick"],
                            "type": start["type"],
                            "staff": start["staff"],
                            "part_id": start["part_id"],
                        })

    return score


def convert_to_engine_score(
    score: Dict,
    cue_name: str,
    part_map: Optional[Dict[str, int]] = None,
    division: int = 16,
) -> Dict:
    part_map = part_map or {}
    tpq = int(score.get("tpq", 24))
    time_sigs = score.get("time_sigs", [])
    if time_sigs:
        beats_per_bar = int(time_sigs[0].get("num", 4))
        den = int(time_sigs[0].get("den", 4))
    else:
        beats_per_bar = 4
        den = 4

    ticks_per_bar = int(round(beats_per_bar * (tpq * 4 / den)))
    total_ticks = 0
    for part in score.get("parts", []):
        if part.get("measures"):
            end_tick = part["measures"][-1]["start_tick"] + part["measures"][-1]["duration_ticks"]
            total_ticks = max(total_ticks, end_tick)
    bars = max(1, int(math.ceil(total_ticks / ticks_per_bar)))
    ticks_per_step = ticks_per_bar / division

    channels: Dict[str, List[List[int]]] = {}
    gates: Dict[str, List[int]] = {}
    next_ch = 4

    def part_channel(part_id: str, part_name: str) -> int:
        nonlocal next_ch
        if part_id in part_map:
            return part_map[part_id]
        if part_name in part_map:
            return part_map[part_name]
        ch = next_ch
        next_ch = min(16, next_ch + 1)
        part_map[part_id] = ch
        return ch

    total_steps = bars * division
    for part in score.get("parts", []):
        ch = part_channel(part.get("id", ""), part.get("name", ""))
        steps = channels.setdefault(str(ch), [[] for _ in range(total_steps)])
        gate_steps = gates.setdefault(str(ch), [0 for _ in range(total_steps)])
        active_ties: Dict[Tuple[int, int, int], Tuple[int, int]] = {}
        for measure in part.get("measures", []):
            for ev in measure.get("events", []):
                if "pitch" not in ev:
                    continue
                tick = ev["tick"]
                step = int(round(tick / ticks_per_step))
                if 0 <= step < total_steps:
                    pitch = int(ev["pitch"])
                    duration_ticks = int(ev.get("duration", 0))
                    duration_steps = max(1, int(round(duration_ticks / ticks_per_step)))
                    duration_steps = min(duration_steps, total_steps - step)
                    tie = ev.get("tie", {}) or {}
                    key = (pitch, int(ev.get("voice", 1)), int(ev.get("staff", 1)))

                    is_tie_start = bool(tie.get("start"))
                    is_tie_stop = bool(tie.get("stop"))

                    if is_tie_start and not is_tie_stop:
                        steps[step].append(pitch)
                        gate_steps[step] = max(gate_steps[step], duration_steps)
                        active_ties[key] = (step, duration_steps)
                        continue

                    if is_tie_stop and key in active_ties:
                        start_step, prev_steps = active_ties.pop(key)
                        total_gate = min(total_steps - start_step, prev_steps + duration_steps)
                        gate_steps[start_step] = max(gate_steps[start_step], total_gate)
                        continue

                    steps[step].append(pitch)
                    gate_steps[step] = max(gate_steps[step], duration_steps)

    for ch, steps in channels.items():
        for idx in range(len(steps)):
            if steps[idx]:
                steps[idx] = sorted(set(steps[idx]))
            else:
                steps[idx] = []

    cc_points: Dict[str, Dict[str, List[Dict[str, int]]]] = {}
    for wedge in score.get("wedge_spans", []):
        part_id = wedge.get("part_id", "")
        ch = part_map.get(part_id)
        if not ch:
            continue
        start_tick = wedge["start_tick"]
        end_tick = max(start_tick + 1, wedge["end_tick"])
        start_step = max(0, int(round(start_tick / ticks_per_step)))
        end_step = min(total_steps - 1, int(round(end_tick / ticks_per_step)))
        if end_step <= start_step:
            continue
        values = []
        for step in range(start_step, end_step + 1):
            t = (step - start_step) / max(1, end_step - start_step)
            if wedge["type"] == "diminuendo":
                val = int(round(110 - 70 * t))
            else:
                val = int(round(40 + 70 * t))
            values.append({"step": step + 1, "value": max(0, min(127, val))})
        cc_points.setdefault(str(ch), {})["11"] = values

    return {
        "bars": bars,
        "division": division,
        "beats_per_bar": beats_per_bar,
        "meta": {
            "tempos": score.get("tempos", []),
            "tempo_spans": score.get("tempo_spans", []),
            "key_sigs": score.get("key_sigs", []),
            "time_sigs": score.get("time_sigs", []),
        },
        "cues": {
            cue_name: {
                "channels": channels,
                "gates": gates,
                "cc": cc_points,
            }
        },
    }


def write_json(path: str, payload: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def load_part_map(path: Optional[str]) -> Dict[str, int]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)
    return {str(k): int(v) for k, v in raw.items()}
