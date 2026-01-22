import json
import os
import re
import tkinter as tk
from typing import Any, Dict, List, Optional
from tkinter import ttk, filedialog, messagebox

from engine import DuetEngine, list_midi_ports, load_song, save_song, note_to_int
from musicxml_tools import convert_to_engine_score, load_part_map, parse_musicxml


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("エレクトーン 合奏アシスタント（最小UI）")
        self.geometry("1100x680")

        self.engine = DuetEngine()
        self.engine.on_log = self._log_from_engine
        self.engine.on_bar = self._on_bar_advanced

        self.song_path = "song.json"
        self.song = load_song(self.song_path)
        self.engine.set_song(self.song)

        self.score_cells = {}
        self.patch_cells = {}
        self.cc_cells = {}
        self.var_score_bars = tk.StringVar()
        self.var_score_cue = tk.StringVar()
        self.score_division = 16

        self._build_ui()
        self._refresh_ports()
        self._load_song_to_table()
        self._load_patch_channels_from_song()
        self._init_score_settings()

        self._log("起動しました。まず「MIDI更新」→IN/OUT選択→「接続」→「GM初期化」→「開始」がおすすめです。")

    # ---------- UI ----------
    def _build_ui(self):
        # Top: connection row
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="MIDI IN").grid(row=0, column=0, sticky="w")
        self.cmb_in = ttk.Combobox(top, width=55, state="readonly")
        self.cmb_in.grid(row=0, column=1, padx=6)

        ttk.Label(top, text="MIDI OUT").grid(row=0, column=2, sticky="w")
        self.cmb_out = ttk.Combobox(top, width=55, state="readonly")
        self.cmb_out.grid(row=0, column=3, padx=6)

        ttk.Button(top, text="MIDI更新", command=self._refresh_ports).grid(row=0, column=4, padx=6)
        ttk.Button(top, text="接続", command=self._connect).grid(row=0, column=5, padx=6)

        self.lbl_status = ttk.Label(top, text="状態: 未接続")
        self.lbl_status.grid(row=0, column=6, padx=12)

        self.bar_var = tk.StringVar(value="小節: 1")
        self.lbl_bar = ttk.Label(top, textvariable=self.bar_var)
        self.lbl_bar.grid(row=0, column=7, padx=12)

        # Middle: left patch frame + right mapping/score frames
        mid = ttk.Frame(self, padding=8)
        mid.pack(fill="both", expand=True)

        patch = ttk.Labelframe(mid, text="GMパッチ設定（デフォルト値）", padding=8)
        patch.pack(side="left", fill="y", padx=(0, 8))

        right = ttk.Frame(mid)
        right.pack(side="left", fill="both", expand=True)

        self.var_out_ch = tk.StringVar(value=str(self.song["patch"].get("out_channel", 4)))
        self.var_msb = tk.StringVar(value=str(self.song["patch"].get("bank_msb", 0)))
        self.var_lsb = tk.StringVar(value=str(self.song["patch"].get("bank_lsb", 0)))
        self.var_prog = tk.StringVar(value=str(self.song["patch"].get("program", 48)))
        self.var_vol = tk.StringVar(value=str(self.song["patch"].get("volume", 100)))
        self.var_expr = tk.StringVar(value=str(self.song["patch"].get("expression", 127)))
        self.var_pan = tk.StringVar(value=str(self.song["patch"].get("pan", 64)))
        self.var_rev = tk.StringVar(value=str(self.song["patch"].get("reverb", 40)))
        self.var_cho = tk.StringVar(value=str(self.song["patch"].get("chorus", 0)))

        row = 0
        row = self._add_labeled_entry(patch, row, "出力CH", self.var_out_ch)
        row = self._add_labeled_entry(patch, row, "Bank MSB", self.var_msb)
        row = self._add_labeled_entry(patch, row, "Bank LSB", self.var_lsb)
        row = self._add_labeled_entry(patch, row, "Program", self.var_prog)
        row = self._add_labeled_entry(patch, row, "Volume", self.var_vol)
        row = self._add_labeled_entry(patch, row, "Expression", self.var_expr)
        row = self._add_labeled_entry(patch, row, "Pan", self.var_pan)
        row = self._add_labeled_entry(patch, row, "Reverb", self.var_rev)
        row = self._add_labeled_entry(patch, row, "Chorus", self.var_cho)

        btns = ttk.Frame(patch)
        btns.grid(row=row, column=0, columnspan=2, pady=(8, 0), sticky="ew")

        ttk.Button(btns, text="GM初期化（Domino相当）", command=self._gm_init).pack(fill="x", pady=(0, 6))
        ttk.Button(btns, text="開始", command=self._start).pack(fill="x")
        ttk.Button(btns, text="停止", command=self._stop).pack(fill="x", pady=(6, 0))

        ch_patch = ttk.Labelframe(patch, text="CH別パッチ（CH4-16）", padding=6)
        ch_patch.grid(row=row + 1, column=0, columnspan=2, pady=(10, 0), sticky="ew")

        headers = ["CH", "MSB", "LSB", "Prog", "Vol", "Expr", "Pan", "Rev", "Cho"]
        for c, h in enumerate(headers):
            ttk.Label(ch_patch, text=h).grid(row=0, column=c, padx=2, pady=2)

        for r, ch in enumerate(range(4, 17), start=1):
            ttk.Label(ch_patch, text=str(ch)).grid(row=r, column=0, padx=2, pady=1)
            for c, key in enumerate(["bank_msb", "bank_lsb", "program", "volume", "expression", "pan", "reverb", "chorus"], start=1):
                ent = ttk.Entry(ch_patch, width=5)
                ent.grid(row=r, column=c, padx=1, pady=1)
                self.patch_cells[(ch, key)] = ent

        # Mapping table
        mapf = ttk.Labelframe(right, text="マッピング（トリガ → 和音）", padding=8)
        mapf.pack(fill="both", expand=True)

        cols = ("name", "trig_ch", "trig_note", "act_notes", "vel", "phrase")
        self.tree = ttk.Treeview(mapf, columns=cols, show="headings", height=16)
        self.tree.heading("name", text="名前")
        self.tree.heading("trig_ch", text="Trigger CH")
        self.tree.heading("trig_note", text="Trigger Note")
        self.tree.heading("act_notes", text="Action Notes (A3,C4,E4...)")
        self.tree.heading("vel", text="Vel")
        self.tree.heading("phrase", text="Phrase (JSON)")
        self.tree.column("name", width=160)
        self.tree.column("trig_ch", width=90, anchor="center")
        self.tree.column("trig_note", width=120, anchor="center")
        self.tree.column("act_notes", width=360)
        self.tree.column("vel", width=80, anchor="center")
        self.tree.column("phrase", width=320)
        self.tree.pack(fill="both", expand=True)

        bar = ttk.Frame(mapf)
        bar.pack(fill="x", pady=(8, 0))

        ttk.Button(bar, text="+ 追加", command=self._add_row).pack(side="left")
        ttk.Button(bar, text="- 削除", command=self._del_row).pack(side="left", padx=6)
        ttk.Button(bar, text="選択行を編集", command=self._edit_row).pack(side="left", padx=6)
        ttk.Button(bar, text="Action試聴", command=self._test_action).pack(side="left", padx=6)

        self.btn_learn = ttk.Button(bar, text="Trigger学習: OFF", command=self._toggle_learn)
        self.btn_learn.pack(side="left", padx=6)

        ttk.Button(bar, text="保存", command=self._save_song).pack(side="right")
        ttk.Button(bar, text="読み込み", command=self._load_song).pack(side="right", padx=6)

        cuef = ttk.Labelframe(right, text="Cueトラック（CH1-3）", padding=8)
        cuef.pack(fill="both", expand=False, pady=(8, 0))

        cue_cols = ("name", "trig_ch", "trig_note", "score")
        self.cue_tree = ttk.Treeview(cuef, columns=cue_cols, show="headings", height=4)
        self.cue_tree.heading("name", text="Cue名")
        self.cue_tree.heading("trig_ch", text="CH")
        self.cue_tree.heading("trig_note", text="Note")
        self.cue_tree.heading("score", text="Score")
        self.cue_tree.column("name", width=220)
        self.cue_tree.column("trig_ch", width=60, anchor="center")
        self.cue_tree.column("trig_note", width=100, anchor="center")
        self.cue_tree.column("score", width=60, anchor="center")
        self.cue_tree.pack(fill="x")
        self.cue_tree.bind("<<TreeviewSelect>>", self._on_cue_select)

        # Score grid
        scoref = ttk.Labelframe(right, text="スコア入力（CH4-16 / 16分グリッド）", padding=8)
        scoref.pack(fill="both", expand=False, pady=(8, 0))

        score_top = ttk.Frame(scoref)
        score_top.pack(fill="x", pady=(0, 6))

        ttk.Label(score_top, text="Cue").pack(side="left")
        self.cmb_score_cue = ttk.Combobox(score_top, width=18, state="readonly", textvariable=self.var_score_cue)
        self.cmb_score_cue.pack(side="left", padx=6)
        self.cmb_score_cue.bind("<<ComboboxSelected>>", lambda _e: self._load_score_to_grid())

        ttk.Label(score_top, text="小節数").pack(side="left", padx=(8, 0))
        self.spn_score_bars = ttk.Spinbox(score_top, from_=1, to=8, width=5, textvariable=self.var_score_bars)
        self.spn_score_bars.pack(side="left", padx=6)

        ttk.Label(score_top, text="分割: 16分").pack(side="left", padx=(8, 0))

        ttk.Button(score_top, text="グリッド更新", command=self._rebuild_score_grid).pack(side="left", padx=6)
        ttk.Button(score_top, text="読み込み", command=self._load_score_to_grid).pack(side="left", padx=6)
        ttk.Button(score_top, text="反映", command=self._apply_score_from_grid).pack(side="left", padx=6)

        ttk.Button(score_top, text="MusicXML取り込み", command=self._import_musicxml).pack(side="left", padx=6)
        self.score_canvas = tk.Canvas(scoref, height=220)
        self.score_canvas.pack(fill="both", expand=True)
        self.score_scroll_x = ttk.Scrollbar(scoref, orient="horizontal", command=self.score_canvas.xview)
        self.score_scroll_x.pack(fill="x")
        self.score_canvas.configure(xscrollcommand=self.score_scroll_x.set)

        self.score_grid_frame = ttk.Frame(self.score_canvas)
        self.score_canvas.create_window((0, 0), window=self.score_grid_frame, anchor="nw")
        self.score_grid_frame.bind(
            "<Configure>",
            lambda e: self.score_canvas.configure(scrollregion=self.score_canvas.bbox("all"))
        )

        ccf = ttk.Labelframe(scoref, text="CCトラック（ポイント列）", padding=6)
        ccf.pack(fill="x", pady=(6, 0))
        ttk.Label(ccf, text="書式: step:value (例 1:64 5:80 9:100) / stepは1から").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 4))
        ttk.Label(ccf, text="CH").grid(row=1, column=0, padx=2)
        ttk.Label(ccf, text="CC11").grid(row=1, column=1, padx=2)
        ttk.Label(ccf, text="CC74").grid(row=1, column=2, padx=2)
        for r, ch in enumerate(range(4, 17), start=2):
            ttk.Label(ccf, text=str(ch)).grid(row=r, column=0, padx=2, pady=1)
            ent11 = ttk.Entry(ccf, width=28)
            ent11.grid(row=r, column=1, padx=2, pady=1, sticky="w")
            ent74 = ttk.Entry(ccf, width=28)
            ent74.grid(row=r, column=2, padx=2, pady=1, sticky="w")
            self.cc_cells[(ch, 11)] = ent11
            self.cc_cells[(ch, 74)] = ent74

        # Bottom: log
        logf = ttk.Labelframe(self, text="ログ", padding=8)
        logf.pack(fill="both", expand=False, padx=8, pady=(0, 8))

        self.txt = tk.Text(logf, height=10, wrap="word")
        self.txt.pack(fill="both", expand=True)
        self.txt.configure(state="disabled")

    def _add_labeled_entry(self, parent, row, label, var):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=var, width=18).grid(row=row, column=1, sticky="w", pady=2)
        return row + 1

    # ---------- Ports / Connect ----------
    def _refresh_ports(self):
        ins, outs = list_midi_ports()
        self.cmb_in["values"] = ins
        self.cmb_out["values"] = outs
        if ins and not self.cmb_in.get():
            self.cmb_in.current(0)
        if outs and not self.cmb_out.get():
            self.cmb_out.current(0)
        self._log(f"MIDI更新: IN={len(ins)}件 OUT={len(outs)}件")

    def _connect(self):
        in_name = self.cmb_in.get()
        out_name = self.cmb_out.get()
        if not in_name or not out_name:
            messagebox.showerror("エラー", "MIDI IN / OUT を選択してください。")
            return
        try:
            self.engine.open_ports(in_name, out_name)
            self.lbl_status.config(text="状態: 接続済み")
        except Exception as e:
            messagebox.showerror("接続エラー", str(e))

    # ---------- GM init / start/stop ----------
    def _apply_patch_to_song(self):
        p = self.song.get("patch", {})
        p["out_channel"] = int(self.var_out_ch.get())
        p["bank_msb"] = int(self.var_msb.get())
        p["bank_lsb"] = int(self.var_lsb.get())
        p["program"] = int(self.var_prog.get())
        p["volume"] = int(self.var_vol.get())
        p["expression"] = int(self.var_expr.get())
        p["pan"] = int(self.var_pan.get())
        p["reverb"] = int(self.var_rev.get())
        p["chorus"] = int(self.var_cho.get())
        self.song["patch"] = p
        self._apply_patch_channels_to_song()
        self.engine.set_song(self.song)

    def _apply_patch_channels_to_song(self):
        p = self.song.get("patch", {})
        channels = {}
        for ch in range(4, 17):
            ch_vals = {}
            for key in ["bank_msb", "bank_lsb", "program", "volume", "expression", "pan", "reverb", "chorus"]:
                ent = self.patch_cells.get((ch, key))
                if not ent:
                    continue
                text = ent.get().strip()
                if text == "":
                    continue
                try:
                    ch_vals[key] = int(text)
                except Exception as e:
                    messagebox.showerror("入力エラー", f"CH{ch} {key}: {e}")
                    return
            if ch_vals:
                channels[str(ch)] = ch_vals
        if channels:
            p["channels"] = channels
        else:
            p.pop("channels", None)
        self.song["patch"] = p

    def _load_patch_channels_from_song(self):
        p = self.song.get("patch", {})
        channels = p.get("channels", {})
        for ch in range(4, 17):
            conf = channels.get(str(ch), {}) if isinstance(channels, dict) else {}
            for key in ["bank_msb", "bank_lsb", "program", "volume", "expression", "pan", "reverb", "chorus"]:
                ent = self.patch_cells.get((ch, key))
                if not ent:
                    continue
                ent.delete(0, "end")
                if isinstance(conf, dict) and key in conf:
                    ent.insert(0, str(conf.get(key)))
    def _gm_init(self):
        try:
            self._apply_patch_to_song()
            self.engine.gm_reset_and_init()
        except Exception as e:
            messagebox.showerror("GM初期化エラー", str(e))

    def _start(self):
        try:
            self.engine.start()
            self.lbl_status.config(text="状態: 実行中")
        except Exception as e:
            messagebox.showerror("開始エラー", str(e))

    def _stop(self):
        try:
            self.engine.stop()
            self.lbl_status.config(text="状態: 停止")
        except Exception as e:
            messagebox.showerror("停止エラー", str(e))

    # ---------- Mapping table ----------
    def _load_song_to_table(self):
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        for m in self.song.get("mappings", []):
            name = m.get("name", "")
            tc = m["trigger"]["ch"]
            tn = m["trigger"]["note"]
            act = m["action"]
            an = ",".join(str(n) for n in act.get("notes", []))
            vel = act.get("velocity", 80)
            phrase = act.get("phrase")
            phrase_str = json.dumps(phrase, ensure_ascii=True) if phrase else ""
            self.tree.insert("", "end", values=(name, tc, tn, an, vel, phrase_str))
        self._refresh_cue_list()
        self._refresh_cue_tracks()

    def _table_to_song(self):
        mappings = []
        for iid in self.tree.get_children():
            name, tc, tn, an, vel, phrase = self.tree.item(iid, "values")
            notes = [x.strip() for x in str(an).split(",") if x.strip()]
            phrase_obj = None
            if str(phrase).strip():
                phrase_obj = json.loads(str(phrase))
            mappings.append({
                "name": str(name),
                "trigger": {"ch": int(tc), "note": str(tn)},
                "action": {"notes": notes, "velocity": int(vel)},
            })
            if phrase_obj is not None:
                mappings[-1]["action"]["phrase"] = phrase_obj
        self.song["mappings"] = mappings
        self.engine.set_song(self.song)
        self._refresh_cue_list()
        self._refresh_cue_tracks()

    def _add_row(self):
        self.tree.insert("", "end", values=("New", 1, "C4", "C4,E4,G4", 80, ""))
        self._refresh_cue_list()
        self._refresh_cue_tracks()

    def _del_row(self):
        sel = self.tree.selection()
        for iid in sel:
            self.tree.delete(iid)
        self._refresh_cue_list()
        self._refresh_cue_tracks()

    def _edit_row(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("編集", "編集する行を選択してください。")
            return
        iid = sel[0]
        vals = self.tree.item(iid, "values")
        EditDialog(self, "行の編集", vals, on_ok=lambda newvals: self._set_row(iid, newvals))

    def _set_row(self, iid, newvals):
        self.tree.item(iid, values=newvals)
        self._refresh_cue_list()
        self._refresh_cue_tracks()

    def _test_action(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("試聴", "試聴する行を選択してください。")
            return
        iid = sel[0]
        name, tc, tn, an, vel, phrase = self.tree.item(iid, "values")
        notes = [x.strip() for x in str(an).split(",") if x.strip()]
        try:
            midi_notes = [note_to_int(n) for n in notes]
            self._apply_patch_to_song()
            # 短い試聴
            self.engine.test_play_notes(midi_notes, velocity=int(vel), duration_ms=450)
        except Exception as e:
            messagebox.showerror("試聴エラー", str(e))

    # ---------- Score grid ----------
    def _ensure_score_defaults(self):
        score = self.song.get("score")
        if not isinstance(score, dict):
            score = {}
        score.setdefault("bars", 1)
        score.setdefault("division", 16)
        score.setdefault("beats_per_bar", 4)
        if not isinstance(score.get("cues"), dict):
            score["cues"] = {}
        self.song["score"] = score
        return score

    def _init_score_settings(self):
        score = self._ensure_score_defaults()
        self.score_division = int(score.get("division", 16))
        self.var_score_bars.set(str(score.get("bars", 1)))
        self._refresh_cue_list()
        self._refresh_cue_tracks()
        if not self.var_score_cue.get() and self.cmb_score_cue["values"]:
            self.cmb_score_cue.current(0)
        self._rebuild_score_grid()
        self._load_score_to_grid()

    def _refresh_cue_list(self):
        cues = []
        for m in self.song.get("mappings", []):
            name = str(m.get("name", "")).strip()
            if name:
                cues.append(name)
        self.cmb_score_cue["values"] = cues
        if cues and self.var_score_cue.get() not in cues:
            self.cmb_score_cue.current(0)

    def _refresh_cue_tracks(self):
        if not hasattr(self, "cue_tree"):
            return
        for iid in self.cue_tree.get_children():
            self.cue_tree.delete(iid)
        score_cues = set(self.song.get("score", {}).get("cues", {}).keys())
        for m in self.song.get("mappings", []):
            trig = m.get("trigger", {})
            try:
                ch = int(trig.get("ch"))
            except Exception:
                continue
            if ch not in (1, 2, 3):
                continue
            name = str(m.get("name", "")).strip()
            note = trig.get("note", "")
            has_score = "Y" if name in score_cues else ""
            self.cue_tree.insert("", "end", values=(name, ch, note, has_score))

    def _on_cue_select(self, _event):
        sel = self.cue_tree.selection()
        if not sel:
            return
        name, _ch, _note, _score = self.cue_tree.item(sel[0], "values")
        if not name:
            return
        self.var_score_cue.set(name)
        self._load_score_to_grid()

    def _rebuild_score_grid(self):
        for child in self.score_grid_frame.winfo_children():
            child.destroy()
        self.score_cells.clear()

        try:
            bars = int(self.var_score_bars.get())
        except Exception:
            bars = 1
        bars = max(1, bars)
        total_steps = bars * self.score_division

        ttk.Label(self.score_grid_frame, text="CH").grid(row=0, column=0, padx=2, pady=2)
        for idx in range(total_steps):
            bar = (idx // self.score_division) + 1
            step = (idx % self.score_division) + 1
            ttk.Label(self.score_grid_frame, text=f"{bar}-{step}").grid(row=0, column=idx + 1, padx=1, pady=2)

        for r, ch in enumerate(range(4, 17), start=1):
            ttk.Label(self.score_grid_frame, text=f"{ch}").grid(row=r, column=0, padx=2, pady=2)
            for idx in range(total_steps):
                ent = ttk.Entry(self.score_grid_frame, width=4)
                ent.grid(row=r, column=idx + 1, padx=1, pady=1)
                self.score_cells[(ch, idx)] = ent

    def _load_score_to_grid(self):
        score = self._ensure_score_defaults()
        try:
            bars = int(score.get("bars", 1))
        except Exception:
            bars = 1
        if self.var_score_bars.get() != str(bars):
            self.var_score_bars.set(str(bars))
            self._rebuild_score_grid()

        cue_name = self.var_score_cue.get().strip()
        if not cue_name:
            return
        cue = score.get("cues", {}).get(cue_name, {})
        channels = cue.get("channels", {})
        cc_tracks = cue.get("cc", {})
        total_steps = bars * self.score_division

        for ch in range(4, 17):
            steps = channels.get(str(ch), [])
            for idx in range(total_steps):
                ent = self.score_cells.get((ch, idx))
                if not ent:
                    continue
                step_val = steps[idx] if idx < len(steps) else []
                if isinstance(step_val, list):
                    text = ",".join(str(n) for n in step_val)
                else:
                    text = str(step_val) if step_val else ""
                ent.delete(0, "end")
                ent.insert(0, text)

            for cc_num in (11, 74):
                ent_cc = self.cc_cells.get((ch, cc_num))
                if not ent_cc:
                    continue
                ent_cc.delete(0, "end")
                ch_cc = cc_tracks.get(str(ch), {})
                points = ch_cc.get(str(cc_num), []) if isinstance(ch_cc, dict) else []
                ent_cc.insert(0, self._format_cc_points(points))

    def _apply_score_from_grid(self, warn_on_empty: bool = True):
        score = self._ensure_score_defaults()
        try:
            bars = int(self.var_score_bars.get())
        except Exception:
            bars = 1
        bars = max(1, bars)
        score["bars"] = bars
        score["division"] = self.score_division

        cue_name = self.var_score_cue.get().strip()
        if not cue_name:
            if warn_on_empty:
                messagebox.showerror("スコア", "Cue を選択してください。")
            return False

        total_steps = bars * self.score_division
        cue = score["cues"].setdefault(cue_name, {})
        cue_channels = cue.setdefault("channels", {})

        for ch in range(4, 17):
            steps = []
            for idx in range(total_steps):
                ent = self.score_cells.get((ch, idx))
                if not ent:
                    steps.append([])
                    continue
                text = ent.get().strip()
                if not text:
                    steps.append([])
                    continue
                parts = [p for p in re.split(r"[,\s]+", text) if p]
                try:
                    for p in parts:
                        note_to_int(p)
                except Exception as e:
                    messagebox.showerror("入力エラー", f"CH{ch} step{idx+1}: {e}")
                    return False
                steps.append(parts)
            cue_channels[str(ch)] = steps

        cue_cc = {}
        for ch in range(4, 17):
            ch_cc = {}
            for cc_num in (11, 74):
                ent_cc = self.cc_cells.get((ch, cc_num))
                if not ent_cc:
                    continue
                text = ent_cc.get().strip()
                if not text:
                    continue
                points = self._parse_cc_text(text, total_steps, ch, cc_num)
                if points is None:
                    return False
                if points:
                    ch_cc[str(cc_num)] = points
            if ch_cc:
                cue_cc[str(ch)] = ch_cc

        if cue_cc:
            cue["cc"] = cue_cc
        else:
            cue.pop("cc", None)

        self.song["score"] = score
        self.engine.set_song(self.song)
        self._log(f"[SCORE] 反映: cue={cue_name} bars={bars} div={self.score_division}")
        self._refresh_cue_tracks()
        return True

    def _import_musicxml(self):
        xml_path = filedialog.askopenfilename(
            title="MusicXMLファイルを選択",
            filetypes=[("MusicXML", "*.musicxml *.xml"), ("All Files", "*.*")],
        )
        if not xml_path:
            return

        cue_name = self.var_score_cue.get().strip()
        if not cue_name:
            messagebox.showerror("MusicXML", "Cue を選択してください。")
            return

        part_map = {}
        map_path = os.path.join(os.path.dirname(xml_path), "part_map.json")
        if os.path.isfile(map_path):
            try:
                part_map = load_part_map(map_path)
                self._log(f"[MusicXML] part_map={map_path}")
            except Exception as e:
                messagebox.showwarning("MusicXML", f"part_map読み込み失敗: {e}")

        try:
            internal_score = parse_musicxml(xml_path)
            engine_score = convert_to_engine_score(
                internal_score,
                cue_name,
                part_map=part_map,
                division=self.score_division,
            )
        except Exception as e:
            messagebox.showerror("MusicXML", str(e))
            return

        score = self._ensure_score_defaults()
        score["bars"] = int(engine_score.get("bars", score.get("bars", 1)))
        score["division"] = int(engine_score.get("division", score.get("division", 16)))
        score["beats_per_bar"] = int(engine_score.get("beats_per_bar", score.get("beats_per_bar", 4)))
        if engine_score.get("meta"):
            score["meta"] = engine_score["meta"]

        cues = score.setdefault("cues", {})
        for name, cue_data in engine_score.get("cues", {}).items():
            cues[name] = cue_data

        self.song["score"] = score
        self.engine.set_song(self.song)
        self.var_score_bars.set(str(score.get("bars", 1)))
        self._rebuild_score_grid()
        self._load_score_to_grid()
        self._refresh_cue_tracks()
        self._log(f"[MusicXML] Imported {os.path.basename(xml_path)} -> cue={cue_name}")

    def _parse_cc_text(self, text: str, total_steps: int, ch: int, cc_num: int) -> Optional[List[Dict[str, int]]]:
        points = []
        tokens = [t for t in re.split(r"[,\s]+", text) if t]
        for token in tokens:
            if ":" in token:
                step_s, val_s = token.split(":", 1)
            elif "=" in token:
                step_s, val_s = token.split("=", 1)
            else:
                messagebox.showerror("入力エラー", f"CH{ch} CC{cc_num}: '{token}' は step:value 形式にしてください。")
                return None
            try:
                step = int(step_s)
                value = int(val_s)
            except Exception:
                messagebox.showerror("入力エラー", f"CH{ch} CC{cc_num}: {token} の数値が不正です。")
                return None
            if not (1 <= step <= total_steps):
                messagebox.showerror("入力エラー", f"CH{ch} CC{cc_num}: stepは1〜{total_steps}です。")
                return None
            if not (0 <= value <= 127):
                messagebox.showerror("入力エラー", f"CH{ch} CC{cc_num}: valueは0〜127です。")
                return None
            points.append({"step": step, "value": value})
        points.sort(key=lambda x: x["step"])
        return points

    def _format_cc_points(self, points: Any) -> str:
        if not isinstance(points, list):
            return ""
        pairs = []
        for item in points:
            if isinstance(item, dict) and "step" in item and "value" in item:
                try:
                    pairs.append((int(item["step"]), int(item["value"])))
                except Exception:
                    continue
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    pairs.append((int(item[0]), int(item[1])))
                except Exception:
                    continue
        pairs.sort(key=lambda x: x[0])
        return " ".join(f"{s}:{v}" for s, v in pairs)

    # ---------- Learn trigger ----------
    def _toggle_learn(self):
        # toggle
        enable = "OFF" in self.btn_learn.cget("text")

        if enable:
            self.engine.set_learn(True, self._on_learn_trigger)
            self.btn_learn.config(text="Trigger学習: ON（次のNoteOnを取得）")
        else:
            self.engine.set_learn(False, None)
            self.btn_learn.config(text="Trigger学習: OFF")

    def _on_learn_trigger(self, ch, note, vel):
        # called from engine thread -> marshal to UI thread
        self.after(0, lambda: self._apply_learn_to_selected_row(ch, note, vel))

    def _apply_learn_to_selected_row(self, ch, note, vel):
        sel = self.tree.selection()
        if not sel:
            self._log(f"[LEARN] 取得: ch{ch} note={note} vel={vel}（※行未選択）")
            return
        iid = sel[0]
        name, tc, tn, an, v, phrase = self.tree.item(iid, "values")
        # update trigger
        self.tree.item(iid, values=(name, ch, note, an, v, phrase))
        self._log(f"[LEARN] 行更新: {name} -> Trigger ch{ch} note={note}")

    # ---------- Save/Load ----------
    def _save_song(self):
        try:
            self._apply_patch_to_song()
            self._table_to_song()
            self._apply_score_from_grid(warn_on_empty=False)

            path = filedialog.asksaveasfilename(
                title="保存",
                defaultextension=".json",
                filetypes=[("JSON", "*.json")]
            )
            if not path:
                return
            save_song(path, self.song)
            self.song_path = path
            self._log(f"[FILE] 保存: {path}")
        except Exception as e:
            messagebox.showerror("保存エラー", str(e))

    def _load_song(self):
        try:
            path = filedialog.askopenfilename(
                title="読み込み",
                filetypes=[("JSON", "*.json")]
            )
            if not path:
                return
            self.song = load_song(path)
            self.song_path = path
            self.engine.set_song(self.song)

            # reflect patch vars
            p = self.song.get("patch", {})
            self.var_out_ch.set(str(p.get("out_channel", 4)))
            self.var_msb.set(str(p.get("bank_msb", 0)))
            self.var_lsb.set(str(p.get("bank_lsb", 0)))
            self.var_prog.set(str(p.get("program", 48)))
            self.var_vol.set(str(p.get("volume", 100)))
            self.var_expr.set(str(p.get("expression", 127)))
            self.var_pan.set(str(p.get("pan", 64)))
            self.var_rev.set(str(p.get("reverb", 40)))
            self.var_cho.set(str(p.get("chorus", 0)))
            self._load_patch_channels_from_song()

            self._load_song_to_table()
            self._init_score_settings()
            self._log(f"[FILE] 読み込み: {path}")
        except Exception as e:
            messagebox.showerror("読み込みエラー", str(e))

    # ---------- Bar (measure) ----------
    def _on_bar_advanced(self, bar: int):
        # engineスレッド → UIスレッド
        self.after(0, lambda: self.bar_var.set(f"小節: {bar}"))

    # ---------- Logging ----------
    def _log(self, s: str):
        self.txt.configure(state="normal")
        self.txt.insert("end", s + "\n")
        self.txt.see("end")
        self.txt.configure(state="disabled")

    def _log_from_engine(self, s: str):
        # engine thread -> UI thread
        self.after(0, lambda: self._log(s))


class EditDialog(tk.Toplevel):
    def __init__(self, master, title, values, on_ok):
        super().__init__(master)
        self.title(title)
        self.resizable(False, False)
        self.on_ok = on_ok

        name, tc, tn, an, vel, phrase = values
        self.var_name = tk.StringVar(value=str(name))
        self.var_tc = tk.StringVar(value=str(tc))
        self.var_tn = tk.StringVar(value=str(tn))
        self.var_an = tk.StringVar(value=str(an))
        self.var_vel = tk.StringVar(value=str(vel))
        self.var_phrase = tk.StringVar(value=str(phrase))

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        self._row(frm, 0, "名前", self.var_name)
        self._row(frm, 1, "Trigger CH", self.var_tc)
        self._row(frm, 2, "Trigger Note", self.var_tn)
        self._row(frm, 3, "Action Notes", self.var_an)
        self._row(frm, 4, "Velocity", self.var_vel)
        self._row(frm, 5, "Phrase (JSON)", self.var_phrase)

        btns = ttk.Frame(frm)
        btns.grid(row=6, column=0, columnspan=2, pady=(10, 0), sticky="e")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="left", padx=6)
        ttk.Button(btns, text="キャンセル", command=self.destroy).pack(side="left")

        self.grab_set()
        self.transient(master)

    def _row(self, parent, r, label, var):
        ttk.Label(parent, text=label).grid(row=r, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(parent, textvariable=var, width=40).grid(row=r, column=1, sticky="w", pady=4)

    def _ok(self):
        try:
            newvals = (
                self.var_name.get().strip(),
                int(self.var_tc.get()),
                self.var_tn.get().strip(),
                self.var_an.get().strip(),
                int(self.var_vel.get()),
                self.var_phrase.get().strip(),
            )
            self.on_ok(newvals)
            self.destroy()
        except Exception as e:
            messagebox.showerror("入力エラー", str(e))


if __name__ == "__main__":
    app = App()
    app.mainloop()
