# MusicXML -> Internal Structure -> Engine Score Draft

This draft defines a minimal MusicXML parser scope and a conversion path into the
existing engine `score` model (grid + CC points).

## 1) Target Internal Structures

Use a normalized, time-based structure in ticks.

```
Score
  tpq: int
  time_sigs: [{ tick, num, den }]
  key_sigs: [{ tick, fifths, mode }]
  tempos: [{ tick, bpm, text? }]
  tempo_spans: [{ start_tick, end_tick, start_bpm, end_bpm, label }]
  wedge_spans: [{ start_tick, end_tick, type, staff, number? }]
  parts: [Part]

Part
  id: str
  name: str
  staves: int
  measures: [Measure]

Measure
  number: int
  start_tick: int
  duration_ticks: int
  divisions: int
  events: [NoteEvent | DirectionEvent]

NoteEvent
  tick: int
  duration: int
  pitch: int
  voice: int
  staff: int
  tie: { start: bool, stop: bool }
  articulations: [str]

DirectionEvent
  kind: "tempo" | "wedge" | "words"
  tick: int
  data: dict
```

## 2) Parser Scope (MusicXML Partwise)

Process only the elements needed by the requirement:

- `<part-list>` -> Part id/name/staves
- `<part><measure>`
  - `<attributes>`
    - `<divisions>` -> measure divisions
    - `<time>` -> time signature (beats/beat-type)
    - `<key>` -> key signature (fifths/mode)
  - `<direction>`
    - `<sound tempo="...">` -> explicit tempo change
    - `<direction-type><words>` -> tempo text (rit., accel.)
    - `<direction-type><wedge type="crescendo|diminuendo|stop">`
  - `<note>`
    - `<pitch>` step/alter/octave -> MIDI
    - `<duration>` -> in divisions
    - `<voice>` -> voice number
    - `<staff>` -> staff number
    - `<tie type="start|stop">`
    - `<notations><articulations>` (staccato/tenuto/accent/etc.)
    - ignore grace/chord/tied handling for v1 except `chord`:
      - if `<chord/>` then same tick as previous note in measure/voice.

Minimal assumptions:
- tick per measure is computed from time signature.
- when time sig is missing, keep previous.
- when tempo text exists without explicit tempo, create a tempo_span
  from this tick to the next explicit tempo.

## 3) Timing Normalization

Use a common `tpq` (ticks per quarter) to normalize durations.

Draft rule:
- Collect all `divisions` values in the file.
- tpq = LCM(divisions)
- Convert note duration: `duration_ticks = duration * (tpq / divisions)`
- Measure duration: `beats * (tpq * 4 / beat_type)`

If LCM grows too large, clamp to a safe upper bound and round.

## 4) Tempo / Wedge Spans

### Tempos
- For each `<sound tempo="X">` at tick T: add `tempos += {tick: T, bpm: X}`.
- For each `<words>` with text in {"rit.", "ritard.", "accel.", ...}
  add a `tempo_span` with:
  - start_tick = T
  - end_tick = tick of the next explicit tempo (or end of score)
  - start_bpm = last explicit tempo before T
  - end_bpm = next explicit tempo (or same as start_bpm if unknown)

### Wedges (Cresc./Dim.)
- Track wedge start by `number` attribute if present.
- On `type="crescendo|diminuendo"`: store start_tick.
- On `type="stop"`: create `wedge_span`.

## 5) Mapping to Engine Score Grid

Existing engine expects:
```
score: {
  bars: int,
  division: int,           # steps per bar (currently 16)
  beats_per_bar: int,
  cues: {
    cueName: {
      channels: { "4": [ [notes], ... per step ] },
      cc: { "4": { "11": [ {step, value}, ... ] } }
    }
  }
}
```

Draft conversion:
- Pick fixed grid `division = 16` (or match time sig denom for future).
- For each measure:
  - `steps_per_bar = division`
  - `ticks_per_step = (beats_per_bar * tpq) / division`
- For each NoteEvent in a part mapped to output channel:
  - `step = round(note.tick / ticks_per_step)`
  - Insert MIDI pitch at `channels[ch][step]`
- For wedge spans:
  - Convert to CC11/CC74 points.
  - For each step in span, interpolate 0..127 (or use default curve).

Note:
- channel routing (part->CH4-16) is still defined by cue mapping.
- conversion can be done per cue or per part.

## 6) Suggested Defaults for v1

- tpq = LCM(divisions), but clamp to <= 480.
- tempo text handling: only "rit", "ritard", "accel", "rall".
- articulations: store names but not yet applied.
- chord handling: if `<chord/>`, same tick as previous note in voice.
- tie handling: ignore sustain for v1; keep as flags.

## 7) Minimal Parser Pseudocode

```
for part in score.parts:
  for measure in part.measures:
    divisions = measure.divisions or last_divisions
    for note in measure.notes:
      if note.chord: tick = last_tick_for_voice
      else: tick = cursor_for_voice; cursor += duration
      emit NoteEvent(tick, duration_ticks, ...)
    for direction in measure.directions:
      if sound tempo: emit tempo event
      if words and matches: mark tempo span
      if wedge: track start/stop
```

## 8) Open Choices

- How to map parts/staves to output CH4-16 by default.
- Whether to quantize to fixed 16th grid or allow per-measure division.
- Where to persist parsed structure (JSON file vs in-memory only).
