import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from engine import DuetEngine, list_midi_ports, load_song, save_song, note_to_int


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("エレクトーン 合奏アシスタント（最小UI）")
        self.geometry("1100x680")

        self.engine = DuetEngine()
        self.engine.on_log = self._log_from_engine

        self.song_path = "song.json"
        self.song = load_song(self.song_path)
        self.engine.set_song(self.song)

        self._build_ui()
        self._refresh_ports()
        self._load_song_to_table()

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

        # Middle: left patch frame + right mapping frame
        mid = ttk.Frame(self, padding=8)
        mid.pack(fill="both", expand=True)

        patch = ttk.Labelframe(mid, text="GMパッチ設定（出力CHの音色など）", padding=8)
        patch.pack(side="left", fill="y", padx=(0, 8))

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

        # Mapping table
        mapf = ttk.Labelframe(mid, text="マッピング（トリガ → 和音）", padding=8)
        mapf.pack(side="left", fill="both", expand=True)

        cols = ("name", "trig_ch", "trig_note", "act_notes", "vel")
        self.tree = ttk.Treeview(mapf, columns=cols, show="headings", height=16)
        self.tree.heading("name", text="名前")
        self.tree.heading("trig_ch", text="Trigger CH")
        self.tree.heading("trig_note", text="Trigger Note")
        self.tree.heading("act_notes", text="Action Notes (A3,C4,E4...)")
        self.tree.heading("vel", text="Vel")
        self.tree.column("name", width=160)
        self.tree.column("trig_ch", width=90, anchor="center")
        self.tree.column("trig_note", width=120, anchor="center")
        self.tree.column("act_notes", width=360)
        self.tree.column("vel", width=80, anchor="center")
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
        self.engine.set_song(self.song)

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
            an = ",".join(m["action"]["notes"])
            vel = m["action"].get("velocity", 80)
            self.tree.insert("", "end", values=(name, tc, tn, an, vel))

    def _table_to_song(self):
        mappings = []
        for iid in self.tree.get_children():
            name, tc, tn, an, vel = self.tree.item(iid, "values")
            notes = [x.strip() for x in str(an).split(",") if x.strip()]
            mappings.append({
                "name": str(name),
                "trigger": {"ch": int(tc), "note": str(tn)},
                "action": {"notes": notes, "velocity": int(vel)},
            })
        self.song["mappings"] = mappings
        self.engine.set_song(self.song)

    def _add_row(self):
        self.tree.insert("", "end", values=("New", 1, "C4", "C4,E4,G4", 80))

    def _del_row(self):
        sel = self.tree.selection()
        for iid in sel:
            self.tree.delete(iid)

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

    def _test_action(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("試聴", "試聴する行を選択してください。")
            return
        iid = sel[0]
        name, tc, tn, an, vel = self.tree.item(iid, "values")
        notes = [x.strip() for x in str(an).split(",") if x.strip()]
        try:
            midi_notes = [note_to_int(n) for n in notes]
            self._apply_patch_to_song()
            # 短い試聴
            self.engine.test_play_notes(midi_notes, velocity=int(vel), duration_ms=450)
        except Exception as e:
            messagebox.showerror("試聴エラー", str(e))

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
        name, tc, tn, an, v = self.tree.item(iid, "values")
        # update trigger
        self.tree.item(iid, values=(name, ch, note, an, v))
        self._log(f"[LEARN] 行更新: {name} -> Trigger ch{ch} note={note}")

    # ---------- Save/Load ----------
    def _save_song(self):
        try:
            self._apply_patch_to_song()
            self._table_to_song()

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

            self._load_song_to_table()
            self._log(f"[FILE] 読み込み: {path}")
        except Exception as e:
            messagebox.showerror("読み込みエラー", str(e))

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

        name, tc, tn, an, vel = values
        self.var_name = tk.StringVar(value=str(name))
        self.var_tc = tk.StringVar(value=str(tc))
        self.var_tn = tk.StringVar(value=str(tn))
        self.var_an = tk.StringVar(value=str(an))
        self.var_vel = tk.StringVar(value=str(vel))

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill="both", expand=True)

        self._row(frm, 0, "名前", self.var_name)
        self._row(frm, 1, "Trigger CH", self.var_tc)
        self._row(frm, 2, "Trigger Note", self.var_tn)
        self._row(frm, 3, "Action Notes", self.var_an)
        self._row(frm, 4, "Velocity", self.var_vel)

        btns = ttk.Frame(frm)
        btns.grid(row=5, column=0, columnspan=2, pady=(10, 0), sticky="e")
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
            )
            self.on_ok(newvals)
            self.destroy()
        except Exception as e:
            messagebox.showerror("入力エラー", str(e))


if __name__ == "__main__":
    app = App()
    app.mainloop()
