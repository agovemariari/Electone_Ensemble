import argparse
import json
from typing import Dict


def load_json(path: str) -> Dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_json(path: str, payload: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def merge_score(song: Dict, engine_score: Dict, cue_override: str = "") -> Dict:
    score = song.get("score")
    if not isinstance(score, dict):
        score = {}

    score["bars"] = int(engine_score.get("bars", score.get("bars", 1)))
    score["division"] = int(engine_score.get("division", score.get("division", 16)))
    score["beats_per_bar"] = int(engine_score.get("beats_per_bar", score.get("beats_per_bar", 4)))

    if engine_score.get("meta"):
        score["meta"] = engine_score["meta"]

    cues = score.setdefault("cues", {})
    for cue_name, cue_data in engine_score.get("cues", {}).items():
        name = cue_override or cue_name
        cues[name] = cue_data

    song["score"] = score
    return song


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge engine_score JSON into song.json.")
    parser.add_argument("engine_score", help="Path to engine_score JSON")
    parser.add_argument("--song", default="song.json", help="Path to song.json")
    parser.add_argument("--cue", default="", help="Override cue name")
    parser.add_argument("--out", default="", help="Output song path (default: overwrite --song)")
    args = parser.parse_args()

    song = load_json(args.song)
    engine_score = load_json(args.engine_score)
    merged = merge_score(song, engine_score, cue_override=args.cue)

    out_path = args.out or args.song
    save_json(out_path, merged)
    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
