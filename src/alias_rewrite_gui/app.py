from __future__ import annotations

import random
from dataclasses import replace
from pathlib import Path
from tkinter import BooleanVar, StringVar, Tk, Toplevel, filedialog, messagebox
from tkinter import ttk

try:
    import winsound
except ImportError:  # pragma: no cover - non-Windows fallback
    winsound = None

from alias_rewrite import (
    ApplyOptions,
    AliasRewriteConfig,
    ExcludeConfig,
    KeyWarningConfig,
    NoteMappingConfig,
    PreviewOptions,
    ReplacementRule,
    apply_changes_direct,
    build_voice_information,
    invert_changes,
    merge_changes,
    preview_changes,
    read_changes_csv,
    write_changes_csv,
)
from alias_rewrite.ust_sync import preview_ust_sync_for_folder


MODE_LABELS = {
    "pitch_append": "音階追記",
    "replace": "置換",
    "numbering": "ナンバリング",
    "csv": "CSVに従って変更",
    "none": "オプション",
}

MODE_VALUES = {label: value for value, label in MODE_LABELS.items()}


# GUIアプリ本体を管理する
class AliaScaleApp:
    # 初期状態を設定する
    def __init__(self, root: Tk) -> None:
        self.root = root
        self.root.title("AliaScale")
        self.root.geometry("1040x720")
        self.root.minsize(900, 600)

        self.voice_dir = StringVar()
        self.oto_path = StringVar()
        self.mrq_path = StringVar()
        self.csv_path = StringVar()
        self.ust_root = StringVar()
        self.output_dir = StringVar(value="outputs")

        self.mode_label = StringVar(value=MODE_LABELS["pitch_append"])
        self.separator = StringVar(value="_")
        self.note_mode = StringVar(value="semitone")
        self.allowed_notes = StringVar()
        self.missing_pitch = StringVar(value="keep")
        self.alias_target = StringVar(value="call_key")
        self.exclude_mode = StringVar(value="none")
        self.exclude_patterns = StringVar()
        self.sort_direction = StringVar(value="asc")

        self.strip_suffix = BooleanVar(value=True)
        self.keep_prefix = BooleanVar(value=True)
        self.add_alias_for_unused_wav = BooleanVar(value=True)
        self.edit_mismatched_wav_mora = BooleanVar(value=False)
        self.prefix_underscore = BooleanVar(value=False)
        self.hiragana_wav = BooleanVar(value=False)
        self.number_first_alias = BooleanVar(value=False)
        self.sort_filename = BooleanVar(value=False)
        self.sort_alias = BooleanVar(value=False)
        self.sort_pitch = BooleanVar(value=False)
        self.update_ust = BooleanVar(value=False)
        self.show_full_ust_path = BooleanVar(value=False)
        self.rename_files = BooleanVar(value=True)
        self.disallow_wav_edit = BooleanVar(value=False)
        self.backup_enabled = BooleanVar(value=True)
        self.backup_mode = StringVar(value="voice_dir")
        self.write_csv_on_apply = BooleanVar(value=True)
        self.merge_csv_on_apply = BooleanVar(value=False)
        self.csv_output_mode = StringVar(value="default")
        self.csv_output_path = StringVar()

        self.replacement_rows: list[tuple[StringVar, StringVar, StringVar, BooleanVar]] = []
        self.ust_vars: dict[Path, BooleanVar] = {}
        self.current_changes = []
        self.current_preview_rows = []
        self.change_by_iid: dict[str, object] = {}
        self.playing_path: Path | None = None
        self.edit_entry: ttk.Entry | None = None

        self._configure_style()
        self._build_layout()
        self._add_replacement_row()
        self._sync_mode_fields()

    # 表示スタイルを設定する
    def _configure_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        self.colors = {
            "bg": "#26232c",
            "panel": "#302b38",
            "panel_2": "#393340",
            "line": "#584f62",
            "text": "#f4edf8",
            "muted": "#b9adbf",
            "main": "#b97ee0",
            "accent": "#a2dce3",
            "warn": "#a1466a",
            "soft": "#f7dec8",
            "entry": "#221f28",
        }
        self.root.configure(bg=self.colors["bg"])
        style.configure(".", background=self.colors["bg"], foreground=self.colors["text"], fieldbackground=self.colors["entry"])
        style.configure("TFrame", background=self.colors["bg"])
        style.configure("Panel.TFrame", background=self.colors["panel"])
        style.configure("TLabel", background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("Muted.TLabel", foreground=self.colors["muted"])
        style.configure("Title.TLabel", font=("Segoe UI", 12, "bold"), foreground=self.colors["text"])
        style.configure("Small.TLabel", font=("Segoe UI", 8), foreground=self.colors["muted"])
        style.configure("TButton", background=self.colors["panel_2"], foreground=self.colors["text"], borderwidth=0, padding=(10, 5))
        style.map("TButton", background=[("active", self.colors["main"])], foreground=[("active", self.colors["bg"])])
        style.configure("Accent.TButton", background=self.colors["main"], foreground=self.colors["bg"])
        style.configure("Ghost.TButton", background=self.colors["bg"], foreground=self.colors["muted"])
        style.configure("TCheckbutton", background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("TRadiobutton", background=self.colors["bg"], foreground=self.colors["text"])
        style.configure("TCombobox", fieldbackground=self.colors["entry"], background=self.colors["entry"], foreground=self.colors["text"])
        style.configure("Treeview", background=self.colors["entry"], fieldbackground=self.colors["entry"], foreground=self.colors["text"], rowheight=22, borderwidth=0)
        style.configure("Treeview.Heading", background=self.colors["panel_2"], foreground=self.colors["muted"], borderwidth=0)
        style.map("Treeview", background=[("selected", self.colors["main"])], foreground=[("selected", self.colors["bg"])])

    # 画面構成を作る
    def _build_layout(self) -> None:
        root = ttk.Frame(self.root, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="AliaScale", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(header, text="UTAU alias rewrite utility", style="Muted.TLabel").grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Button(header, text="⚙", width=3, command=self.open_settings_dialog).grid(row=0, column=2, sticky="e")

        left = ttk.Frame(root, width=360, padding=(0, 0, 14, 0))
        left.grid(row=1, column=0, sticky="nsew")
        left.grid_propagate(False)
        left.columnconfigure(0, weight=1)
        left.rowconfigure(3, weight=1)

        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self._build_setup(left)
        self._build_rewrite(left)
        self._build_ust(left)
        self._build_actions(left)
        self._build_preview(right)
        self._build_information(right)
        self._build_log(right)

    # 区画を処理する
    def _section_title(self, parent: ttk.Frame, text: str, row: int) -> ttk.Label:
        label = ttk.Label(parent, text=text, style="Small.TLabel")
        label.grid(row=row, column=0, sticky="w", pady=(12, 4))
        return label

    # 項目行情報を処理する
    def _field_row(self, parent: ttk.Frame, row: int, label: str, var: StringVar, command=None, state: str = "normal") -> None:
        ttk.Label(parent, text=label, style="Muted.TLabel").grid(row=row, column=0, sticky="w")
        entry = ttk.Entry(parent, textvariable=var, state=state)
        entry.grid(row=row + 1, column=0, sticky="ew", pady=(2, 4))
        ttk.Button(parent, text="参照", command=command).grid(row=row + 1, column=1, sticky="ew", padx=(6, 0), pady=(2, 4))

    # 値を作る
    def _build_setup(self, parent: ttk.Frame) -> None:
        self._section_title(parent, "SETUP", 0)
        frame = ttk.Frame(parent)
        frame.grid(row=1, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

        self._field_row(frame, 0, "音源フォルダ", self.voice_dir, self.choose_voice_dir)
        self._field_row(frame, 2, "oto.ini", self.oto_path, lambda: self.choose_file(self.oto_path, "oto.ini", [("oto.ini", "oto.ini"), ("INI", "*.ini"), ("All", "*.*")]))

        ttk.Label(frame, text="処理モード", style="Muted.TLabel").grid(row=4, column=0, sticky="w")
        mode = ttk.Combobox(frame, textvariable=self.mode_label, values=list(MODE_VALUES), state="readonly")
        mode.grid(row=5, column=0, columnspan=2, sticky="ew", pady=(2, 4))
        mode.bind("<<ComboboxSelected>>", lambda _event: self._sync_mode_fields())

        self.mode_area = ttk.Frame(frame)
        self.mode_area.grid(row=6, column=0, columnspan=2, sticky="ew")
        self.mode_area.columnconfigure(0, weight=1)

    # 値を作る
    def _build_rewrite(self, parent: ttk.Frame) -> None:
        self._section_title(parent, "REWRITE", 2)
        frame = ttk.Frame(parent)
        frame.grid(row=3, column=0, sticky="ew")
        frame.columnconfigure(0, weight=1)

        line = ttk.Frame(frame)
        line.grid(row=0, column=0, sticky="ew", pady=(0, 2))
        ttk.Checkbutton(line, text="suffix削除", variable=self.strip_suffix).pack(side="left")
        ttk.Checkbutton(line, text="prefix保持", variable=self.keep_prefix).pack(side="left", padx=(10, 0))

        line = ttk.Frame(frame)
        line.grid(row=1, column=0, sticky="ew", pady=(0, 2))
        ttk.Checkbutton(line, text="未使用原音にalias付与", variable=self.add_alias_for_unused_wav).pack(side="left")

        line = ttk.Frame(frame)
        line.grid(row=2, column=0, sticky="ew", pady=(0, 2))
        ttk.Checkbutton(line, text='先頭に"_"', variable=self.prefix_underscore).pack(side="left")
        ttk.Checkbutton(line, text="wav名をひらがな", variable=self.hiragana_wav).pack(side="left", padx=(10, 0))

        line = ttk.Frame(frame)
        line.grid(row=3, column=0, sticky="ew", pady=(0, 2))
        ttk.Checkbutton(line, text="1番目に1を付ける", variable=self.number_first_alias).pack(side="left")

        line = ttk.Frame(frame)
        line.grid(row=4, column=0, sticky="ew", pady=(0, 2))
        ttk.Checkbutton(line, text="wavの編集を許可しない", variable=self.disallow_wav_edit).pack(side="left")

        ttk.Label(frame, text="alias生成対象", style="Muted.TLabel").grid(row=5, column=0, sticky="w", pady=(6, 0))
        ttk.Combobox(
            frame,
            textvariable=self.alias_target,
            values=["call_key", "alias_wav"],
            state="readonly",
        ).grid(row=6, column=0, sticky="ew", pady=(2, 4))

        ttk.Label(frame, text="除外設定", style="Muted.TLabel").grid(row=7, column=0, sticky="w")
        ex = ttk.Frame(frame)
        ex.grid(row=8, column=0, sticky="ew", pady=(2, 4))
        ex.columnconfigure(1, weight=1)
        ttk.Combobox(ex, textvariable=self.exclude_mode, values=["none", "string_list", "regex", "mora"], state="readonly", width=11).grid(row=0, column=0, sticky="w")
        ttk.Entry(ex, textvariable=self.exclude_patterns).grid(row=0, column=1, sticky="ew", padx=(6, 0))

        ttk.Label(frame, text="並べ替え", style="Muted.TLabel").grid(row=9, column=0, sticky="w", pady=(6, 0))
        sort_line = ttk.Frame(frame)
        sort_line.grid(row=10, column=0, sticky="ew", pady=(2, 0))
        ttk.Checkbutton(sort_line, text="ファイル名", variable=self.sort_filename).pack(side="left")
        ttk.Checkbutton(sort_line, text="エイリアス", variable=self.sort_alias).pack(side="left", padx=(8, 0))
        self.pitch_sort_button = ttk.Checkbutton(sort_line, text="音階", variable=self.sort_pitch)
        self.pitch_sort_button.pack(side="left", padx=(8, 0))
        direction = ttk.Frame(frame)
        direction.grid(row=11, column=0, sticky="ew")
        ttk.Radiobutton(direction, text="昇順", variable=self.sort_direction, value="asc").pack(side="left")
        ttk.Radiobutton(direction, text="降順", variable=self.sort_direction, value="desc").pack(side="left", padx=(10, 0))

    # USTを作る
    def _build_ust(self, parent: ttk.Frame) -> None:
        self._section_title(parent, "UST", 4)
        frame = ttk.Frame(parent)
        frame.grid(row=5, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(4, weight=1)

        ttk.Checkbutton(frame, text="USTを書き換える", variable=self.update_ust, command=self.refresh_ust_list).grid(row=0, column=0, sticky="w")
        path = ttk.Frame(frame)
        path.grid(row=1, column=0, sticky="ew", pady=(2, 4))
        path.columnconfigure(0, weight=1)
        ttk.Entry(path, textvariable=self.ust_root).grid(row=0, column=0, sticky="ew")
        ttk.Button(path, text="参照", command=self.choose_ust_root).grid(row=0, column=1, padx=(6, 0))
        ttk.Checkbutton(frame, text="フルパス表示", variable=self.show_full_ust_path, command=self._redraw_ust_checks).grid(row=2, column=0, sticky="w")
        ttk.Button(frame, text="UST再検索", command=self.refresh_ust_list).grid(row=3, column=0, sticky="ew", pady=(2, 4))

        self.ust_canvas = None
        self.ust_list = ttk.Frame(frame)
        self.ust_list.grid(row=4, column=0, sticky="nsew")

    # 値を作る
    def _build_actions(self, parent: ttk.Frame) -> None:
        actions = ttk.Frame(parent)
        actions.grid(row=6, column=0, sticky="ew", pady=(14, 0))
        actions.columnconfigure(1, weight=1)
        ttk.Button(actions, text="Preview", command=self.preview, style="Accent.TButton").grid(row=0, column=0, sticky="ew")
        ttk.Button(actions, text="Apply to oto.ini", command=self.apply_direct).grid(row=0, column=1, sticky="ew", padx=(8, 0))

    # Previewを作る
    def _build_preview(self, parent: ttk.Frame) -> None:
        top = ttk.Frame(parent)
        top.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="PREVIEW", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.preview_summary = ttk.Label(top, text="0 rows / 0 edits / 0 warnings", style="Muted.TLabel")
        self.preview_summary.grid(row=0, column=1, sticky="w", padx=(10, 0))
        ttk.Button(top, text="更新", command=self.preview).grid(row=0, column=2, padx=(8, 0))

        table_frame = ttk.Frame(parent)
        table_frame.grid(row=1, column=0, sticky="nsew")
        table_frame.columnconfigure(0, weight=1)
        table_frame.rowconfigure(0, weight=1)
        columns = ("op", "old_wav", "new_wav", "old_alias", "new_alias", "status", "warning")
        self.table = ttk.Treeview(table_frame, columns=columns, show="headings", height=22)
        headings = {
            "op": "",
            "old_wav": "旧ファイル名",
            "new_wav": "新ファイル名",
            "old_alias": "元エイリアス",
            "new_alias": "新エイリアス",
            "status": "状態",
            "warning": "警告",
        }
        widths = {"op": 34, "old_wav": 150, "new_wav": 150, "old_alias": 140, "new_alias": 140, "status": 90, "warning": 130}
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(column, width=widths[column], minwidth=28, stretch=column != "op")
        self.table.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(table_frame, orient="vertical", command=self.table.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.tag_configure("changed", background="#33273b")
        self.table.tag_configure("warning", background="#4a2f40")
        self.table.tag_configure("danger", background="#5a2635")
        self.table.bind("<<TreeviewSelect>>", lambda _event: self.update_information())
        self.table.bind("<Double-1>", self._begin_table_edit)

    # 情報を作る
    def _build_information(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="INFORMATION", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        self.play_button = ttk.Button(frame, text="▶", width=3, command=self.toggle_information_playback)
        self.play_button.grid(row=0, column=1, sticky="e")
        self.info_text = ttk.Label(frame, text="音源フォルダを選択すると概要を表示します。", style="Muted.TLabel", justify="left")
        self.info_text.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))

    # 値を作る
    def _build_log(self, parent: ttk.Frame) -> None:
        frame = ttk.Frame(parent)
        frame.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        frame.columnconfigure(0, weight=1)
        ttk.Label(frame, text="LOG", style="Small.TLabel").grid(row=0, column=0, sticky="w")
        self.log_box = ttk.Treeview(frame, columns=("message",), show="headings", height=5)
        self.log_box.heading("message", text="")
        self.log_box.column("message", stretch=True)
        self.log_box.grid(row=1, column=0, sticky="ew")

    # モード項目一覧を同期する
    def _sync_mode_fields(self) -> None:
        for child in self.mode_area.winfo_children():
            child.destroy()
        mode = self.mode
        self.mrq_path.trace_add("write", lambda *_args: self._sync_pitch_sort_state())
        if mode == "pitch_append":
            self._field_row(self.mode_area, 0, "周波数表 / moresampler MRQ", self.mrq_path, lambda: self.choose_file(self.mrq_path, "MRQ", [("MRQ", "*.mrq"), ("All", "*.*")]))
            ttk.Label(self.mode_area, text="丸め", style="Muted.TLabel").grid(row=2, column=0, sticky="w")
            row = ttk.Frame(self.mode_area)
            row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(2, 4))
            row.columnconfigure(1, weight=1)
            ttk.Combobox(row, textvariable=self.note_mode, values=["semitone", "whole", "classes", "explicit"], state="readonly", width=10).grid(row=0, column=0)
            ttk.Entry(row, textvariable=self.allowed_notes).grid(row=0, column=1, sticky="ew", padx=(6, 0))
            ttk.Entry(row, textvariable=self.separator, width=5).grid(row=0, column=2, padx=(6, 0))
        elif mode == "replace":
            ttk.Button(self.mode_area, text="+ 置換ルール", command=self._add_replacement_row).grid(row=0, column=0, sticky="w", pady=(0, 4))
            self.replacement_frame = ttk.Frame(self.mode_area)
            self.replacement_frame.grid(row=1, column=0, columnspan=2, sticky="ew")
            self.replacement_frame.columnconfigure(0, weight=1)
            self._redraw_replacement_rows()
        elif mode == "csv":
            self._field_row(self.mode_area, 0, "CSV変換規則", self.csv_path, lambda: self.choose_file(self.csv_path, "CSV", [("CSV", "*.csv"), ("All", "*.*")]))
            ttk.Button(self.mode_area, text="CSV反転", command=self.invert_csv).grid(row=2, column=0, sticky="w", pady=(2, 4))
        else:
            ttk.Label(self.mode_area, text="モード固有設定はありません。", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self._sync_pitch_sort_state()

    @property
    # モードを処理する
    def mode(self) -> str:
        return MODE_VALUES.get(self.mode_label.get(), "pitch_append")

    # 音高並べ替え状態を同期する
    def _sync_pitch_sort_state(self) -> None:
        has_pitch = bool(self.mrq_path.get().strip())
        state = "normal" if has_pitch else "disabled"
        self.pitch_sort_button.configure(state=state)
        if not has_pitch:
            self.sort_pitch.set(False)

    # 行情報を追加する
    def _add_replacement_row(self) -> None:
        self.replacement_rows.append((StringVar(), StringVar(), StringVar(value="alias"), BooleanVar(value=False)))
        if hasattr(self, "replacement_frame"):
            self._redraw_replacement_rows()

    # 行情報一覧を描画し直す
    def _redraw_replacement_rows(self) -> None:
        for child in self.replacement_frame.winfo_children():
            child.destroy()
        for index, (old, new, target, regex) in enumerate(self.replacement_rows):
            row = ttk.Frame(self.replacement_frame)
            row.grid(row=index, column=0, sticky="ew", pady=(0, 3))
            row.columnconfigure(1, weight=1)
            row.columnconfigure(2, weight=1)
            ttk.Combobox(row, textvariable=target, values=["alias", "wav", "both"], state="readonly", width=7).grid(row=0, column=0, padx=(0, 4))
            ttk.Entry(row, textvariable=old).grid(row=0, column=1, sticky="ew", padx=(0, 4))
            ttk.Entry(row, textvariable=new).grid(row=0, column=2, sticky="ew", padx=(0, 4))
            ttk.Checkbutton(row, text="re", variable=regex).grid(row=0, column=3)

    # 音源を選択する
    def choose_voice_dir(self) -> None:
        path = filedialog.askdirectory(title="音源フォルダを選択")
        if not path:
            return
        voice = Path(path)
        self.voice_dir.set(str(voice))
        oto = voice / "oto.ini"
        if oto.exists():
            self.oto_path.set(str(oto))
        mrqs = sorted(voice.glob("*.mrq"))
        if mrqs and not self.mrq_path.get():
            self.mrq_path.set(str(mrqs[0]))
        self.output_dir.set(str(Path.cwd() / "outputs"))
        self.update_information()

    # ファイルを選択する
    def choose_file(self, variable: StringVar, title: str, filetypes) -> None:
        path = filedialog.askopenfilename(title=title, filetypes=filetypes)
        if path:
            variable.set(path)
            self._sync_pitch_sort_state()

    # USTルートを選択する
    def choose_ust_root(self) -> None:
        path = filedialog.askdirectory(title="UST検索フォルダを選択")
        if path:
            self.ust_root.set(path)
            self.refresh_ust_list()

    # 値を生成する
    def preview(self) -> None:
        try:
            oto_path = self._require_path(self.oto_path, "oto.ini")
            mrq_path = Path(self.mrq_path.get()) if self.mrq_path.get().strip() else None
            csv_path = Path(self.csv_path.get()) if self.csv_path.get().strip() else None
            if self.mode == "pitch_append" and mrq_path is None:
                raise ValueError("音階追記モードでは周波数表が必要です。")
            if self.mode == "csv" and csv_path is None:
                raise ValueError("CSVに従って変更するモードではCSVが必要です。")
            rows, _encoding = preview_changes(
                oto_path,
                options=self._preview_options(),
                mrq_path=mrq_path,
                csv_path=csv_path,
            )
            self.current_preview_rows = rows
            self.current_changes = rows
            self.refresh_table()
            self.refresh_ust_list()
            self.update_information()
            self.log(f"Preview: {len(rows)} rows, {sum(1 for row in rows if row.changed)} edits")
        except Exception as exc:  # noqa: BLE001 - GUI boundary
            self.log(f"Preview failed: {exc}")
            messagebox.showerror("Preview failed", str(exc))

    # 設定を生成する
    def _preview_options(self) -> PreviewOptions:
        return PreviewOptions(
            mode=self.mode,
            alias_config=AliasRewriteConfig(
                separator=self.separator.get(),
                strip_suffix=self.strip_suffix.get(),
                keep_prefix=self.keep_prefix.get(),
                missing_pitch=self.missing_pitch.get(),
            ),
            note_config=self._note_config(),
            replacement_rules=self._replacement_rules(),
            edit_scope=self.alias_target.get(),
            alias_target=self.alias_target.get(),
            add_alias_for_unused_wav=self.add_alias_for_unused_wav.get(),
            edit_mismatched_wav_mora=False,
            exclude_config=ExcludeConfig(mode=self.exclude_mode.get(), patterns=self._pattern_tuple()),
            key_warning_config=KeyWarningConfig(warn_on_resolution_change=True, excluded_moras=("_",)),
            number_first_alias=self.number_first_alias.get(),
            sort_keys=self._sort_keys(),
            sort_descending=self.sort_direction.get() == "desc",
            allow_wav_edit=not self.disallow_wav_edit.get(),
        )

    # 音符設定を処理する
    def _note_config(self) -> NoteMappingConfig:
        mode = self.note_mode.get()
        values = tuple(item.strip() for item in self.allowed_notes.get().split(",") if item.strip())
        if mode == "whole":
            return NoteMappingConfig(mode="whole_tone")
        if mode == "classes":
            return NoteMappingConfig(mode="pitch_classes", allowed_pitch_classes=values or None)
        if mode == "explicit":
            return NoteMappingConfig(mode="explicit_notes", explicit_notes=values or None)
        return NoteMappingConfig(mode="semitone")

    # 値を処理する
    def _pattern_tuple(self) -> tuple[str, ...]:
        return tuple(item.strip() for item in self.exclude_patterns.get().split(",") if item.strip())

    # ルール一覧を処理する
    def _replacement_rules(self) -> tuple[ReplacementRule, ...]:
        rules = []
        for old, new, target, regex in self.replacement_rows:
            if old.get():
                rules.append(
                    ReplacementRule(
                        old=old.get(),
                        new=new.get(),
                        target=target.get(),
                        use_regex=regex.get(),
                    )
                )
        return tuple(rules)

    # キー一覧を並べ替える
    def _sort_keys(self) -> tuple[str, ...]:
        keys = []
        if self.sort_filename.get():
            keys.append("filename")
        if self.sort_alias.get():
            keys.append("alias")
        if self.sort_pitch.get():
            keys.append("pitch")
        return tuple(keys)

    # 表を更新する
    def refresh_table(self) -> None:
        self.table.delete(*self.table.get_children())
        self.change_by_iid.clear()
        edits = 0
        warnings = 0
        for change in self.current_changes:
            edits += int(change.changed)
            warning_text = "; ".join(change.warnings)
            warnings += int(change.severity in {"warning", "danger"})
            tag = "danger" if change.severity == "danger" else "warning" if change.severity == "warning" else "changed" if change.changed else ""
            iid = self.table.insert(
                "",
                "end",
                values=(
                    "▶",
                    change.old_wav,
                    change.new_wav,
                    change.old_alias,
                    change.new_alias,
                    self._status_label(change),
                    warning_text,
                ),
                tags=(tag,) if tag else (),
            )
            self.change_by_iid[iid] = change
        self.preview_summary.configure(text=f"{len(self.current_changes)} rows / {edits} edits / {warnings} warnings")

    # 状態を処理する
    def _status_label(self, change) -> str:
        if change.severity == "danger":
            return "危険"
        if change.severity == "warning":
            return "警告"
        labels = {
            "system": "自動",
            "manual": "手動",
            "exclude": "除外",
            "empty": "原音設定なし",
            "no_freq": "無声音",
            "no_freq_src": "周波数表なし",
            "invalid_freq": "周波数表不正",
            "no_f0": "無声音",
            "duplication": "重複",
            "changed_mora": "発音変化",
            "external_file_conflict": "退避",
            "cannotcall": "呼び出し不可",
            "old_wav_split": "wav分裂",
            "wav_conflict": "wav衝突",
            "invalid_wav_name": "wav名不正",
        }
        return labels.get(change.status, change.status)

    # 表を開始する
    def _begin_table_edit(self, event) -> None:
        region = self.table.identify_region(event.x, event.y)
        if region != "cell":
            return
        row_id = self.table.identify_row(event.y)
        column_id = self.table.identify_column(event.x)
        columns = ("op", "old_wav", "new_wav", "old_alias", "new_alias", "status", "warning")
        column = columns[int(column_id[1:]) - 1]
        if column not in {"new_wav", "new_alias"} or row_id not in self.change_by_iid:
            return
        if column == "new_wav" and self.disallow_wav_edit.get():
            return
        bbox = self.table.bbox(row_id, column)
        if not bbox:
            return
        x, y, width, height = bbox
        value = self.table.set(row_id, column)
        if self.edit_entry is not None:
            self.edit_entry.destroy()
        self.edit_entry = ttk.Entry(self.table)
        self.edit_entry.insert(0, value)
        self.edit_entry.place(x=x, y=y, width=width, height=height)
        self.edit_entry.focus_set()

        # 値を確定する
        def commit(_event=None) -> None:
            if self.edit_entry is None:
                return
            new_value = self.edit_entry.get()
            change = self.change_by_iid[row_id]
            if column == "new_wav":
                updated = replace(change, new_wav=new_value, changed=(new_value != change.old_wav or change.new_alias != change.old_alias), reason="manual")
            else:
                updated = replace(change, new_alias=new_value, changed=(change.new_wav != change.old_wav or new_value != change.old_alias), reason="manual")
            self.change_by_iid[row_id] = updated
            self.current_changes = [self.change_by_iid[iid] for iid in self.table.get_children()]
            self.edit_entry.destroy()
            self.edit_entry = None
            self.refresh_table()

        self.edit_entry.bind("<Return>", commit)
        self.edit_entry.bind("<FocusOut>", commit)

    # USTを更新する
    def refresh_ust_list(self) -> None:
        if not self.update_ust.get() or not self.ust_root.get() or not self.oto_path.get():
            self.ust_vars.clear()
            self._redraw_ust_checks()
            return
        try:
            previews = preview_ust_sync_for_folder(
                Path(self.ust_root.get()),
                self.current_changes,
                voice_dir=Path(self.voice_dir.get()) if self.voice_dir.get() else None,
                oto_path=Path(self.oto_path.get()),
            )
            existing = self.ust_vars
            self.ust_vars = {
                preview.path: existing.get(preview.path, BooleanVar(value=True))
                for preview in previews
                if preview.changed_count > 0
            }
            self._redraw_ust_checks()
        except Exception as exc:  # noqa: BLE001 - GUI boundary
            self.log(f"UST scan failed: {exc}")

    # USTを描画し直す
    def _redraw_ust_checks(self) -> None:
        for child in self.ust_list.winfo_children():
            child.destroy()
        if not self.ust_vars:
            ttk.Label(self.ust_list, text="影響するUSTは未検出です。", style="Muted.TLabel").pack(anchor="w")
            return
        for path, variable in sorted(self.ust_vars.items(), key=lambda item: str(item[0]).casefold()):
            text = str(path) if self.show_full_ust_path.get() else path.name
            ttk.Checkbutton(self.ust_list, text=text, variable=variable).pack(anchor="w")

    # 情報を更新する
    def update_information(self) -> None:
        selected = self.table.selection()
        if selected:
            change = self.change_by_iid.get(selected[0])
            if change is not None:
                info = [
                    f"{change.old_wav}  →  {change.new_wav}",
                    f"{change.old_alias or '(aliasなし)'}  →  {change.new_alias or '(aliasなし)'}",
                    f"status: {change.status or '-'} / reason: {change.reason or '-'}",
                    f"frequency: {change.frequency or '-'} / note: {change.note or '-'} / frames: {change.valid_frame_count}",
                ]
                if change.warnings:
                    info.append("warning: " + "; ".join(change.warnings))
                self.info_text.configure(text="\n".join(info))
                return
        try:
            voice_dir = Path(self.voice_dir.get()) if self.voice_dir.get() else None
            oto_path = Path(self.oto_path.get()) if self.oto_path.get() else None
            mrq_path = Path(self.mrq_path.get()) if self.mrq_path.get() else None
            if voice_dir is None:
                self.info_text.configure(text="音源フォルダを選択すると概要を表示します。")
                return
            info = build_voice_information(voice_dir, oto_path=oto_path, mrq_path=mrq_path)
            lines = [
                f"音源: {info.voice_name}",
                f"wav: {info.wav_file_count} / oto entries: {info.oto_entry_count} / aliases: {info.alias_count}",
                f"empty aliases: {info.empty_alias_count}",
            ]
            if info.frequency_min and info.frequency_max:
                lines.append(f"frequency range: {info.frequency_min:.2f} - {info.frequency_max:.2f} Hz")
                lines.extend(f"{bin.label}: {bin.alias_count}" for bin in info.pitch_bins[:6])
            self.info_text.configure(text="\n".join(lines))
        except Exception as exc:  # noqa: BLE001 - GUI boundary
            self.info_text.configure(text=f"INFORMATIONを更新できませんでした: {exc}")

    # 情報再生を処理する
    def toggle_information_playback(self) -> None:
        if winsound is None:
            messagebox.showinfo("Playback", "この環境ではwinsound再生を利用できません。")
            return
        if self.playing_path is not None:
            winsound.PlaySound(None, winsound.SND_PURGE)
            self.playing_path = None
            self.play_button.configure(text="▶")
            return
        wav = self._selected_or_random_wav()
        if wav is None:
            messagebox.showinfo("Playback", "再生できるwavが見つかりません。")
            return
        winsound.PlaySound(str(wav), winsound.SND_FILENAME | winsound.SND_ASYNC)
        self.playing_path = wav
        self.play_button.configure(text="■")

    # ランダムwavを取得す
    def _selected_or_random_wav(self) -> Path | None:
        voice = Path(self.voice_dir.get()) if self.voice_dir.get() else None
        if voice is None:
            return None
        selected = self.table.selection()
        if selected and selected[0] in self.change_by_iid:
            change = self.change_by_iid[selected[0]]
            path = voice / change.old_wav
            if path.exists():
                return path
        wavs = sorted(voice.glob("*.wav"))
        return random.choice(wavs) if wavs else None

    # 値を反映する
    def apply_direct(self) -> None:
        try:
            if not self.current_changes:
                self.preview()
            voice_dir = self._require_path(self.voice_dir, "音源フォルダ")
            oto_path = self._require_path(self.oto_path, "oto.ini")
            csv_output_path = self._selected_csv_output_path()
            if (
                self.write_csv_on_apply.get()
                and csv_output_path is not None
                and not self.merge_csv_on_apply.get()
                and csv_output_path.exists()
            ):
                if not messagebox.askyesno("CSV overwrite", f"{csv_output_path} を上書きします。実行しますか？"):
                    self.log("Apply cancelled: CSV overwrite was not confirmed")
                    return
            result = apply_changes_direct(
                voice_dir,
                oto_path,
                self.current_changes,
                ApplyOptions(
                    rename_files=self.rename_files.get() and not self.disallow_wav_edit.get(),
                    allow_wav_edit=not self.disallow_wav_edit.get(),
                    update_ust=self.update_ust.get(),
                    ust_root=Path(self.ust_root.get()) if self.ust_root.get() else None,
                    selected_ust_paths=tuple(path for path, var in self.ust_vars.items() if var.get()) if self.update_ust.get() else None,
                    backup=self.backup_enabled.get(),
                    backup_mode=self.backup_mode.get(),
                    write_csv=self.write_csv_on_apply.get(),
                    csv_path=csv_output_path,
                    merge_csv=self.merge_csv_on_apply.get(),
                ),
            )
            self.log(f"Apply completed: {len(result.written_files)} files")
            self._show_apply_dialog(result)
        except Exception as exc:  # noqa: BLE001 - GUI boundary
            self.log(f"Apply failed: {exc}")
            messagebox.showerror("Apply failed", str(exc))

    # CSVパスを取得す
    def _selected_csv_output_path(self) -> Path | None:
        if not self.write_csv_on_apply.get() or self.csv_output_mode.get() != "custom":
            return None
        value = self.csv_output_path.get().strip()
        return Path(value) if value else None

    # Applyダイアログを表示する
    def _show_apply_dialog(self, result) -> None:
        dialog = Toplevel(self.root)
        dialog.title("Apply completed")
        dialog.geometry("520x360")
        dialog.configure(bg=self.colors["bg"])
        dialog.transient(self.root)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="以下のファイルを編集しました！", style="Title.TLabel").pack(anchor="w")
        tree = ttk.Treeview(frame, columns=("path",), show="headings", height=10)
        tree.heading("path", text="")
        tree.column("path", stretch=True)
        tree.pack(fill="both", expand=True, pady=(10, 8))
        for path in result.written_files:
            tree.insert("", "end", values=(str(path),))
        for backup in result.backups:
            tree.insert("", "end", values=(f"backup: {backup.backup_path}",))
        for skipped in result.skipped:
            tree.insert("", "end", values=(f"skip: {skipped}",))
        for warning in result.warnings:
            tree.insert("", "end", values=(f"warning: {warning}",))
        for error in result.errors:
            tree.insert("", "end", values=(f"error: {error}",))
        ttk.Button(frame, text="OK", command=dialog.destroy, style="Accent.TButton").pack(anchor="e")

    # CSVを書き出す
    def export_csv(self) -> None:
        if not self.current_changes:
            self.preview()
        path = filedialog.asksaveasfilename(
            title="CSVを書き出し",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
        )
        if not path:
            return
        write_changes_csv(self.current_changes, path)
        self.log(f"CSV exported: {path}")

    # CSVを反転する
    def invert_csv(self) -> None:
        input_path = self._ask_csv_open("反転するCSV")
        if input_path is None:
            return
        output_path = filedialog.asksaveasfilename(title="反転CSVを書き出し", defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not output_path:
            return
        write_changes_csv(invert_changes(read_changes_csv(input_path)), output_path)
        self.log(f"CSV inverted: {output_path}")

    # CSVを統合する
    def merge_csv(self) -> None:
        first = self._ask_csv_open("統合元CSV 1")
        if first is None:
            return
        second = self._ask_csv_open("統合元CSV 2")
        if second is None:
            return
        output = filedialog.asksaveasfilename(title="統合CSVを書き出し", defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not output:
            return
        write_changes_csv(merge_changes(read_changes_csv(first), read_changes_csv(second)), output)
        self.log(f"CSV merged: {output}")

    # CSVを確認する
    def _ask_csv_open(self, title: str) -> Path | None:
        path = filedialog.askopenfilename(title=title, filetypes=[("CSV", "*.csv"), ("All", "*.*")])
        return Path(path) if path else None

    # CSVパスを選択する
    def choose_csv_output_path(self) -> None:
        path = filedialog.asksaveasfilename(
            title="CSV出力先",
            defaultextension=".csv",
            filetypes=[("CSV", "*.csv"), ("All", "*.*")],
        )
        if path:
            self.csv_output_path.set(path)
            self.csv_output_mode.set("custom")

    # 設定ダイアログを処理する
    def open_settings_dialog(self) -> None:
        dialog = Toplevel(self.root)
        dialog.title("Settings")
        dialog.geometry("520x520")
        dialog.configure(bg=self.colors["bg"])
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="SETTINGS", style="Title.TLabel").pack(anchor="w")

        file_frame = ttk.LabelFrame(frame, text="Files", padding=10)
        file_frame.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(file_frame, text="実ファイル名も変更する", variable=self.rename_files).pack(anchor="w")
        ttk.Checkbutton(file_frame, text="wavの編集を許可しない", variable=self.disallow_wav_edit).pack(anchor="w", pady=(4, 0))
        backup_frame = ttk.LabelFrame(frame, text="Backup", padding=10)
        backup_frame.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(backup_frame, text="Apply前にバックアップを作成する", variable=self.backup_enabled).pack(anchor="w")
        backup_modes = ttk.Frame(backup_frame)
        backup_modes.pack(fill="x", pady=(6, 0))
        ttk.Radiobutton(backup_modes, text="voiceフォルダ全体 + 変更UST", variable=self.backup_mode, value="voice_dir").pack(side="left")
        ttk.Radiobutton(backup_modes, text="oto.ini + 変更USTのみ", variable=self.backup_mode, value="oto_only").pack(side="left", padx=(12, 0))

        csv_frame = ttk.LabelFrame(frame, text="CSV", padding=10)
        csv_frame.pack(fill="x", pady=(10, 0))
        ttk.Checkbutton(csv_frame, text="Apply時にCSV記録を出力する", variable=self.write_csv_on_apply).pack(anchor="w")
        ttk.Checkbutton(csv_frame, text="既存CSVへ統合する", variable=self.merge_csv_on_apply).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            csv_frame,
            text="規定: 統合ONは(音源名).csv、統合OFFは(音源名)_(日時).csv",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(6, 0))
        csv_modes = ttk.Frame(csv_frame)
        csv_modes.pack(fill="x", pady=(6, 0))
        ttk.Radiobutton(csv_modes, text="規定", variable=self.csv_output_mode, value="default").pack(side="left")
        ttk.Radiobutton(csv_modes, text="ファイル名を指定", variable=self.csv_output_mode, value="custom").pack(side="left", padx=(12, 0))
        csv_path = ttk.Frame(csv_frame)
        csv_path.pack(fill="x", pady=(4, 0))
        csv_path.columnconfigure(0, weight=1)
        ttk.Entry(csv_path, textvariable=self.csv_output_path).grid(row=0, column=0, sticky="ew")
        ttk.Button(csv_path, text="参照", command=self.choose_csv_output_path).grid(row=0, column=1, padx=(6, 0))

        ttk.Button(frame, text="OK", command=dialog.destroy, style="Accent.TButton").pack(anchor="e", pady=(16, 0))

    # 値を処理する
    def log(self, message: str) -> None:
        self.log_box.insert("", "end", values=(message,))
        items = self.log_box.get_children()
        if items:
            self.log_box.see(items[-1])

    # パスを確認する
    def _require_path(self, variable: StringVar, label: str) -> Path:
        value = variable.get().strip()
        if not value:
            raise ValueError(f"{label} を指定してください。")
        return Path(value)


# アプリを起動する
def main() -> None:
    root = Tk()
    AliaScaleApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
