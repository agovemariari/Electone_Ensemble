import argparse
import os

from musicxml_tools import convert_to_engine_score, load_part_map, parse_musicxml, write_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert MusicXML into internal JSON and engine score JSON.")
    parser.add_argument("musicxml", help="Path to MusicXML file")
    parser.add_argument("--cue", default="Cue_MusicXML", help="Cue name to store in engine score")
    parser.add_argument("--part-map", default="", help="JSON file mapping part_id or part_name to channel")
    parser.add_argument("--out-dir", default=".", help="Output directory")
    args = parser.parse_args()

    score = parse_musicxml(args.musicxml)
    part_map = load_part_map(args.part_map) if args.part_map else {}
    engine_score = convert_to_engine_score(score, args.cue, part_map=part_map)

    base = os.path.splitext(os.path.basename(args.musicxml))[0]
    internal_path = os.path.join(args.out_dir, f"{base}.internal.json")
    score_path = os.path.join(args.out_dir, f"{base}.engine_score.json")

    write_json(internal_path, score)
    write_json(score_path, engine_score)

    print(f"Wrote: {internal_path}")
    print(f"Wrote: {score_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
