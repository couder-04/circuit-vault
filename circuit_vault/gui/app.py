"""Unified 5-tab Circuit Vault GUI. Calls only core.* — no domain logic."""

from __future__ import annotations

import webbrowser
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from circuit_vault.core import CircuitVaultApp, first_run_needed, last_circ_path, load_session
from circuit_vault.formats import (
    CircFormat,
    credential_store_name,
    detect_format,
    format_label,
    quit_shortcut_hint,
)
from circuit_vault.promptgen import components_catalog
from circuit_vault.validator import HealthState

_DOT = {
    HealthState.HEALTHY: QColor("#2e7d32"),
    HealthState.CHANGED: QColor("#f9a825"),
    HealthState.BROKEN: QColor("#c62828"),
    HealthState.NO_FINAL: QColor("#9e9e9e"),
}


def _with_restart_help(message: str) -> str:
    text = (message or "Something went wrong.").strip()
    if "How to restart:" in text:
        return text
    return (
        f"{text}\n\n"
        "How to restart:\n"
        f"Quit Circuit Vault ({quit_shortcut_hint()}), then run:\n"
        "  circuit-vault gui"
    )


def _show_error(parent: QWidget | None, title: str, message: str) -> None:
    QMessageBox.warning(parent, title, _with_restart_help(message))


class HealthDot(QWidget):
    def __init__(self, color: QColor, parent=None) -> None:
        super().__init__(parent)
        self._color = color
        self.setFixedSize(16, 16)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setBrush(self._color)
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(2, 2, 12, 12)


class SetupWizard(QDialog):
    def __init__(self, app_core: CircuitVaultApp, parent=None) -> None:
        super().__init__(parent)
        self.app_core = app_core
        self.setWindowTitle("Link GitHub (one-time setup)")
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "Circuit Vault keeps a copy of your work on GitHub so nothing stays "
                "only on this laptop. You only do this once."
            )
        )
        form = QFormLayout()
        self.repo = QLineEdit()
        self.repo.setPlaceholderText("https://github.com/you/your-lab-repo.git")
        self.name = QLineEdit()
        self.name.setPlaceholderText("Your name")
        self.email = QLineEdit()
        self.email.setPlaceholderText("you@school.edu")
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText(
            f"GitHub personal access token (stored in {credential_store_name()})"
        )
        form.addRow("GitHub repo URL", self.repo)
        form.addRow("Name (optional)", self.name)
        form.addRow("Email (optional)", self.email)
        form.addRow("Access token", self.token)
        layout.addLayout(form)
        self.circ_btn = QPushButton("Choose .circ to protect first…")
        self.circ_btn.clicked.connect(self._pick_circ)
        layout.addWidget(self.circ_btn)
        self.circ_label = QLabel("No file chosen yet")
        layout.addWidget(self.circ_label)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._finish)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        last = last_circ_path()
        if last:
            self.circ_label.setText(str(last))

    def _pick_circ(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Logisim file", str(Path.home()), "Logisim (*.circ)"
        )
        if path:
            r = self.app_core.open_project(path)
            if not r.ok:
                QMessageBox.warning(self, "Could not open", r.message)
                return
            self.circ_label.setText(str(path))

    def _finish(self) -> None:
        if self.app_core.circ_path is None and last_circ_path():
            self.app_core.open_project(last_circ_path())  # type: ignore[arg-type]
        if self.app_core.circ_path is None:
            _show_error(self, "Need a file", "Choose a .circ file first.")
            return
        if not self.repo.text().strip():
            _show_error(self, "Need a repo", "Paste your GitHub repo URL.")
            return
        try:
            result = self.app_core.setup_repo(
                self.repo.text().strip(),
                self.name.text().strip(),
                self.email.text().strip(),
                self.token.text().strip(),
            )
        except Exception as exc:  # noqa: BLE001
            _show_error(self, "Setup failed", str(exc))
            return
        if not result.ok:
            _show_error(self, "Setup failed", result.message)
            return
        QMessageBox.information(self, "Ready", result.message)
        self.accept()


class CircuitRow(QWidget):
    def __init__(self, name: str, health: HealthState, on_action) -> None:
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.addWidget(HealthDot(_DOT[health]))
        lab = QLabel(name)
        lab.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(lab)
        if health == HealthState.BROKEN:
            b = QPushButton("Restore")
            b.setStyleSheet("QPushButton { background:#c62828; color:white; padding:6px 12px; }")
            b.clicked.connect(lambda: on_action(name, "restore"))
            layout.addWidget(b)
        elif health == HealthState.CHANGED:
            r = QPushButton("Restore")
            r.clicked.connect(lambda: on_action(name, "restore"))
            layout.addWidget(r)
            m = QPushButton("Mark Final")
            m.clicked.connect(lambda: on_action(name, "mark"))
            layout.addWidget(m)
        else:
            m = QPushButton("Mark Final")
            m.clicked.connect(lambda: on_action(name, "mark"))
            layout.addWidget(m)


class MainWindow(QMainWindow):
    REFRESH_MS = 5000

    def __init__(self, app_core: CircuitVaultApp | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Circuit Vault")
        self.resize(900, 640)
        self.app_core = app_core or CircuitVaultApp()
        self._fingerprint = None
        self._custom_components: list[str] = []
        self._incoming_path: Path | None = None
        self._incoming_scan = []
        self._build_preview = {}

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)

        body = QHBoxLayout()
        outer.addLayout(body, stretch=1)

        # Sidebar
        side = QVBoxLayout()
        self.nav = QListWidget()
        for label in ("My File", "Import", "Build", "History", "Settings"):
            self.nav.addItem(QListWidgetItem(label))
        self.nav.setFixedWidth(140)
        self.nav.currentRowChanged.connect(self._switch_tab)
        side.addWidget(self.nav)
        body.addLayout(side)

        self.stack = QStackedWidget()
        body.addWidget(self.stack, stretch=1)

        self.tab_file = self._build_tab_file()
        self.tab_import = self._build_tab_import()
        self.tab_build = self._build_tab_build()
        self.tab_history = self._build_tab_history()
        self.tab_settings = self._build_tab_settings()
        for w in (
            self.tab_file,
            self.tab_import,
            self.tab_build,
            self.tab_history,
            self.tab_settings,
        ):
            self.stack.addWidget(w)

        # Status bar
        bar = QHBoxLayout()
        self.sync_label = QLabel("☁ Not linked yet")
        self.retry_btn = QPushButton("Retry sync")
        self.retry_btn.clicked.connect(self._retry_sync)
        self.retry_btn.hide()
        self.active_label = QLabel("")
        bar.addWidget(self.sync_label)
        bar.addWidget(self.retry_btn)
        bar.addStretch()
        bar.addWidget(self.active_label)
        outer.addLayout(bar)

        self.poll = QTimer(self)
        self.poll.setInterval(self.REFRESH_MS)
        self.poll.timeout.connect(self._poll)
        self.nav.setCurrentRow(0)

        last = last_circ_path()
        if last:
            self.app_core.open_project(last)
            self._refresh_file_tab(force=True)
            self.poll.start()
        self._refresh_sync_bar()
        self._load_settings_fields()

    def _switch_tab(self, index: int) -> None:
        self.stack.setCurrentIndex(index)
        if index == 0:
            self._refresh_file_tab(force=True)
        elif index == 3:
            self._refresh_history()
        elif index == 4:
            self._load_settings_fields()

    # ----- Tab 1 My File -----
    def _build_tab_file(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        self.file_title = QLabel("My File")
        self.file_title.setStyleSheet("font-size:20px; font-weight:600;")
        layout.addWidget(self.file_title)
        self.open_btn = QPushButton("Open .circ")
        self.open_btn.clicked.connect(self._choose_file)
        layout.addWidget(self.open_btn)
        self.list_host = QWidget()
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.list_host)
        self.file_scroll = scroll
        layout.addWidget(scroll, stretch=1)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.toast = QLabel("")
        self.toast.hide()
        layout.addWidget(self.toast)
        return w

    def _choose_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Logisim file", str(Path.home()), "Logisim (*.circ)"
        )
        if path:
            r = self.app_core.open_project(path)
            if not r.ok:
                QMessageBox.critical(self, "Could not open", r.message)
                return
            self.open_btn.setText("Open another .circ")
            self._refresh_file_tab(force=True)
            self._refresh_target_dropdowns()
            if not self.poll.isActive():
                self.poll.start()
            self._refresh_sync_bar()

    def _clear_list(self) -> None:
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _refresh_file_tab(self, *, force: bool = False) -> None:
        if self.app_core.circ_path is None:
            return
        try:
            self.app_core.reload()
        except Exception as exc:  # noqa: BLE001
            self.hint.setText(f"Waiting for a readable file… ({exc})")
            return
        self.active_label.setText(self.app_core.circ_path.name)
        fmt = detect_format(self.app_core.project) if self.app_core.project else None
        if fmt is not None:
            self.file_title.setText(f"My File — {format_label(fmt)}")
        self._clear_list()
        for row in self.app_core.status():
            self.list_layout.addWidget(CircuitRow(row.name, row.health, self._file_action))
        self.hint.setText(self.app_core.plain_status_summary())
        self._refresh_sync_bar()
        if hasattr(self, "build_format") and self.build_format.currentData() == "auto":
            self._rebuild_component_checks()

    def _file_action(self, name: str, action: str) -> None:
        if action == "restore":
            if (
                QMessageBox.question(
                    self,
                    "Restore?",
                    f"Restore “{name}”? Other circuits stay untouched.",
                )
                != QMessageBox.StandardButton.Yes
            ):
                return
            r = self.app_core.restore(name)
            self._toast(r.message if r.ok else r.message)
            if not r.ok:
                QMessageBox.warning(self, "Restore failed", r.message)
        else:
            vault = self.app_core.vault
            if vault and vault.has_final(name):
                if (
                    QMessageBox.question(
                        self, "Update saved final?", f"Replace saved final for “{name}”?"
                    )
                    != QMessageBox.StandardButton.Yes
                ):
                    return
            r = self.app_core.mark_final(name)
            if not r.ok:
                QMessageBox.warning(self, "Could not mark", r.message)
            else:
                self._toast(r.message)
        self._refresh_file_tab(force=True)

    def _toast(self, text: str) -> None:
        self.toast.setText(text)
        self.toast.show()
        QTimer.singleShot(6000, self.toast.hide)

    def _poll(self) -> None:
        if self.app_core.circ_path is None or QApplication.activeModalWidget():
            return
        if self.stack.currentIndex() == 0:
            self._refresh_file_tab(force=True)

    # ----- Tab 2 Import -----
    def _build_tab_import(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("Import circuits from a shared .circ"))
        row = QHBoxLayout()
        browse = QPushButton("Browse shared .circ")
        browse.clicked.connect(self._browse_incoming)
        row.addWidget(browse)
        layout.addLayout(row)
        self.import_path_label = QLabel("No shared file loaded")
        layout.addWidget(self.import_path_label)
        self.import_list = QWidget()
        self.import_list_layout = QVBoxLayout(self.import_list)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.import_list)
        layout.addWidget(scroll, stretch=1)
        into_row = QHBoxLayout()
        into_row.addWidget(QLabel("Merge into"))
        self.import_target = QComboBox()
        into_row.addWidget(self.import_target, stretch=1)
        layout.addLayout(into_row)
        clash_row = QHBoxLayout()
        clash_row.addWidget(QLabel("If name already exists"))
        self.clash = QComboBox()
        self.clash.addItems(["replace", "keep_both", "skip"])
        clash_row.addWidget(self.clash)
        layout.addLayout(clash_row)
        merge_btn = QPushButton("Fix & Merge Selected")
        merge_btn.clicked.connect(self._do_merge)
        layout.addWidget(merge_btn)
        return w

    def _browse_incoming(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Shared Logisim file", str(Path.home()), "Logisim (*.circ)"
        )
        if not path:
            return
        self._incoming_path = Path(path)
        self.import_path_label.setText(path)
        self._incoming_scan = self.app_core.import_scan(path)
        while self.import_list_layout.count():
            item = self.import_list_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._import_checks: dict[str, QCheckBox] = {}
        for circ in self._incoming_scan:
            row = QHBoxLayout()
            host = QWidget()
            host.setLayout(row)
            row.addWidget(HealthDot(_DOT.get(circ.health, _DOT[HealthState.BROKEN])))
            cb = QCheckBox(circ.name)
            can = circ.xml_bytes is not None and not circ.unfixable_reason
            cb.setEnabled(can)
            cb.setChecked(can)
            note = ""
            if circ.repaired and can:
                note = "  🔴→🟢 auto-fixed"
            elif circ.unfixable_reason:
                note = "  ⚠ couldn't fix"
                cb.setToolTip(circ.unfixable_reason)
            row.addWidget(cb)
            row.addWidget(QLabel(note))
            row.addStretch()
            self.import_list_layout.addWidget(host)
            self._import_checks[circ.name] = cb
        self._refresh_target_dropdowns()

    def _refresh_target_dropdowns(self) -> None:
        paths = [str(p) for p in self.app_core.list_target_circ_files()]
        for combo in (self.import_target, self.build_target):
            cur = combo.currentText()
            combo.clear()
            combo.addItems(paths)
            if cur in paths:
                combo.setCurrentText(cur)

    def _do_merge(self) -> None:
        if not self._incoming_path:
            QMessageBox.warning(self, "No file", "Browse a shared .circ first.")
            return
        selected = [n for n, cb in self._import_checks.items() if cb.isChecked()]
        target = self.import_target.currentText()
        if not target:
            QMessageBox.warning(self, "No target", "Open a .circ on My File first.")
            return
        # Offer unresolved deps
        for circ in self._incoming_scan:
            if circ.name in selected:
                for dep in circ.resolvable_deps:
                    if dep not in selected:
                        if (
                            QMessageBox.question(
                                self,
                                "Also import dependency?",
                                f"Also import “{dep}”?",
                            )
                            == QMessageBox.StandardButton.Yes
                        ):
                            selected.append(dep)
        result = self.app_core.import_merge(
            selected,
            target,
            self.clash.currentText(),
            incoming_path=self._incoming_path,
        )
        if not result.ok:
            QMessageBox.warning(self, "Merge failed", result.message)
        else:
            QMessageBox.information(self, "Imported", result.message)
            self._refresh_file_tab(force=True)
        self._refresh_sync_bar()

    # ----- Tab 3 Build -----
    def _build_tab_build(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("Step 1 — Describe the circuit"))
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("e.g. 4-bit ripple carry adder")
        self.desc.setMaximumHeight(80)
        layout.addWidget(self.desc)

        name_row = QHBoxLayout()
        name_row.addWidget(QLabel("Circuit name"))
        self.circuit_name_edit = QLineEdit()
        self.circuit_name_edit.setPlaceholderText(
            "e.g. RippleAdder — if taken, a number is added (RippleAdder1)"
        )
        self.circuit_name_edit.textChanged.connect(self._validate_paste)
        name_row.addWidget(self.circuit_name_edit, stretch=1)
        layout.addLayout(name_row)

        fmt_row = QHBoxLayout()
        fmt_row.addWidget(QLabel("Target Logisim"))
        self.build_format = QComboBox()
        self.build_format.addItem("Auto (from open file)", "auto")
        self.build_format.addItem("Logisim Evolution", CircFormat.EVOLUTION.value)
        self.build_format.addItem("Logisim (classic)", CircFormat.CLASSIC.value)
        self.build_format.currentIndexChanged.connect(self._rebuild_component_checks)
        fmt_row.addWidget(self.build_format, stretch=1)
        layout.addLayout(fmt_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self._comp_host = QWidget()
        self.comp_layout = QVBoxLayout(self._comp_host)
        self.comp_checks: dict[str, QCheckBox] = {}
        self._rebuild_component_checks()
        scroll.setWidget(self._comp_host)
        scroll.setMinimumHeight(160)
        layout.addWidget(scroll)

        add_row = QHBoxLayout()
        self.custom_edit = QLineEdit()
        self.custom_edit.setPlaceholderText("Add your own component name")
        add_btn = QPushButton("Add")
        add_btn.clicked.connect(self._add_custom_comp)
        add_row.addWidget(self.custom_edit)
        add_row.addWidget(add_btn)
        layout.addLayout(add_row)
        self.custom_chips = QLabel("")
        layout.addWidget(self.custom_chips)

        io = QHBoxLayout()
        self.inputs = QLineEdit()
        self.inputs.setPlaceholderText("Inputs (e.g. A[3:0], B[3:0], Cin)")
        self.outputs = QLineEdit()
        self.outputs.setPlaceholderText("Outputs (e.g. Sum[3:0], Cout)")
        io.addWidget(self.inputs)
        io.addWidget(self.outputs)
        layout.addLayout(io)

        gen = QPushButton("Generate Prompt")
        gen.clicked.connect(self._gen_prompt)
        layout.addWidget(gen)

        layout.addWidget(QLabel("Step 2 — Copy into Claude"))
        self.prompt_box = QPlainTextEdit()
        self.prompt_box.setReadOnly(True)
        self.prompt_box.setMaximumHeight(120)
        layout.addWidget(self.prompt_box)
        pbuttons = QHBoxLayout()
        copy_btn = QPushButton("📋 Copy")
        copy_btn.clicked.connect(
            lambda: QApplication.clipboard().setText(self.prompt_box.toPlainText())
        )
        open_btn = QPushButton("Open Claude ↗")
        open_btn.clicked.connect(lambda: webbrowser.open("https://claude.ai"))
        pbuttons.addWidget(copy_btn)
        pbuttons.addWidget(open_btn)
        layout.addLayout(pbuttons)

        layout.addWidget(QLabel("Step 3 — Paste or attach the <circuit> XML"))
        self.xml_box = QPlainTextEdit()
        self.xml_box.setPlaceholderText("Paste <circuit>...</circuit> here")
        self.xml_box.setMaximumHeight(100)
        layout.addWidget(self.xml_box)
        attach = QPushButton("Attach .xml")
        attach.clicked.connect(self._attach_xml)
        layout.addWidget(attach)
        self.preview_label = QLabel("")
        self.preview_label.setWordWrap(True)
        layout.addWidget(self.preview_label)

        brow = QHBoxLayout()
        brow.addWidget(QLabel("Merge into"))
        self.build_target = QComboBox()
        brow.addWidget(self.build_target, stretch=1)
        layout.addLayout(brow)
        build_btn = QPushButton("Build & Merge")
        build_btn.clicked.connect(self._do_build_merge)
        layout.addWidget(build_btn)
        return w

    def _rebuild_component_checks(self) -> None:
        while self.comp_layout.count():
            item = self.comp_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.comp_checks.clear()
        fmt = self._selected_build_format()
        for cat, items in components_catalog(fmt).items():
            box = QGroupBox(cat)
            gl = QVBoxLayout(box)
            for name in items:
                cb = QCheckBox(name)
                self.comp_checks[name] = cb
                gl.addWidget(cb)
            self.comp_layout.addWidget(box)

    def _selected_build_format(self) -> CircFormat:
        data = self.build_format.currentData()
        if data == "auto" or data is None:
            if self.app_core.project is not None:
                return detect_format(self.app_core.project)
            return CircFormat.EVOLUTION
        return CircFormat(str(data))

    def _add_custom_comp(self) -> None:
        name = self.custom_edit.text().strip()
        if name and name not in self._custom_components:
            self._custom_components.append(name)
            self.custom_edit.clear()
            self.custom_chips.setText("Custom: " + ", ".join(self._custom_components))

    def _selected_components(self) -> list[str]:
        picked = [n for n, cb in self.comp_checks.items() if cb.isChecked()]
        return picked + list(self._custom_components)

    def _gen_prompt(self) -> None:
        fmt = self._selected_build_format()
        prompt = self.app_core.build_prompt(
            self.desc.toPlainText().strip(),
            self._selected_components(),
            self.inputs.text().strip(),
            self.outputs.text().strip(),
            fmt,
        )
        self.prompt_box.setPlainText(prompt)

    def _attach_xml(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Circuit XML", str(Path.home()), "XML (*.xml);;All (*)"
        )
        if path:
            self.xml_box.setPlainText(Path(path).read_text(encoding="utf-8"))
            self._validate_paste()

    def _validate_paste(self) -> None:
        from circuit_vault.promptgen import validate_generated

        text = self.xml_box.toPlainText().strip()
        if not text:
            self.preview_label.setText("")
            return

        fmt = self._selected_build_format()
        existing: set[str] = set()
        target = self.build_target.currentText()
        if target:
            try:
                from circuit_vault.parser import list_circuits, load

                existing = set(list_circuits(load(target)))
            except Exception:  # noqa: BLE001
                existing = set()
        preferred = self.circuit_name_edit.text().strip()
        try:
            ok, preview = validate_generated(
                text.encode("utf-8"),
                target_format=fmt,
                existing_names=existing,
                preferred_name=preferred or None,
                prepare=True,
            )
        except Exception as exc:  # noqa: BLE001
            self.preview_label.setText(f"Could not process XML: {exc}")
            self._build_preview = {"error": str(exc)}
            return
        self._build_preview = preview
        if not ok:
            self.preview_label.setText(f"Not valid yet: {preview.get('error')}")
            return
        tip = preview.get("tip") or ""
        self.preview_label.setText(
            f"{preview.get('name')}: {preview.get('input_count')} in, "
            f"{preview.get('output_count')} out, {preview.get('component_count')} parts. {tip}"
        )

    def _do_build_merge(self) -> None:
        self._validate_paste()
        target = self.build_target.currentText()
        if not target:
            QMessageBox.warning(self, "No target", "Open a .circ first.")
            return
        preferred = self.circuit_name_edit.text().strip()
        result = self.app_core.build_merge(
            self.xml_box.toPlainText().encode("utf-8"),
            target,
            preferred_name=preferred,
        )
        if not result.ok:
            QMessageBox.warning(self, "Could not merge", result.message)
        else:
            tip = (result.preview or {}).get("tip") or ""
            QMessageBox.information(
                self,
                "Ready",
                result.message + (("\n" + tip) if tip else ""),
            )
            self._refresh_file_tab(force=True)
        self._refresh_sync_bar()

    # ----- Tab 4 History -----
    def _build_tab_history(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        layout.addWidget(QLabel("Recent changes (also on GitHub when linked)"))
        self.history_list = QListWidget()
        layout.addWidget(self.history_list, stretch=1)
        undo = QPushButton("Undo last action")
        undo.clicked.connect(self._undo)
        layout.addWidget(undo)
        return w

    def _refresh_history(self) -> None:
        self.history_list.clear()
        if self.app_core.circ_path is None:
            return
        for line in self.app_core.history(30):
            self.history_list.addItem(line)

    def _undo(self) -> None:
        r = self.app_core.undo()
        if not r.ok:
            QMessageBox.warning(self, "Could not undo", r.message)
        else:
            QMessageBox.information(self, "Undone", r.message)
        self._refresh_file_tab(force=True)
        self._refresh_history()
        self._refresh_sync_bar()

    # ----- Tab 5 Settings -----
    def _build_tab_settings(self) -> QWidget:
        w = QWidget()
        layout = QVBoxLayout(w)
        form = QFormLayout()
        self.set_repo = QLineEdit()
        self.set_name = QLineEdit()
        self.set_email = QLineEdit()
        self.set_token = QLineEdit()
        self.set_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.set_token.setPlaceholderText("Leave blank to keep existing token")
        form.addRow("GitHub repo", self.set_repo)
        form.addRow("Commit name", self.set_name)
        form.addRow("Commit email", self.set_email)
        form.addRow("New token", self.set_token)
        layout.addLayout(form)
        change = QPushButton("Save & test push")
        change.clicked.connect(self._save_repo_settings)
        layout.addWidget(change)

        self.auto_sync_cb = QCheckBox("Auto-sync to GitHub after every change")
        self.auto_sync_cb.setChecked(True)
        self.bak_cb = QCheckBox("Push backups (.bak) to GitHub (uses more space over time)")
        self.bak_cb.setChecked(True)
        layout.addWidget(self.auto_sync_cb)
        layout.addWidget(self.bak_cb)
        apply = QPushButton("Apply toggles")
        apply.clicked.connect(self._apply_toggles)
        layout.addWidget(apply)

        browse = QPushButton("Change active .circ")
        browse.clicked.connect(self._choose_file)
        layout.addWidget(browse)
        layout.addStretch()
        return w

    def _load_settings_fields(self) -> None:
        data = load_session()
        self.set_repo.setText(str(data.get("repo_url", "")))
        self.set_name.setText(str(data.get("git_name", "")))
        self.set_email.setText(str(data.get("git_email", "")))
        self.auto_sync_cb.setChecked(bool(data.get("auto_sync", True)))
        self.bak_cb.setChecked(bool(data.get("push_backups", True)))

    def _save_repo_settings(self) -> None:
        token = self.set_token.text().strip()
        try:
            r = self.app_core.setup_repo(
                self.set_repo.text().strip(),
                self.set_name.text().strip(),
                self.set_email.text().strip(),
                token,
            )
        except Exception as exc:  # noqa: BLE001
            _show_error(self, "Failed", str(exc))
            self._refresh_sync_bar()
            return
        if not r.ok:
            _show_error(self, "Failed", r.message)
        else:
            QMessageBox.information(self, "Saved", r.message)
            self.set_token.clear()
        self._refresh_sync_bar()

    def _apply_toggles(self) -> None:
        self.app_core.update_settings(
            auto_sync=self.auto_sync_cb.isChecked(),
            push_backups=self.bak_cb.isChecked(),
        )
        QMessageBox.information(self, "Saved", "Settings updated.")

    def _retry_sync(self) -> None:
        self.app_core.retry_sync()
        self._refresh_sync_bar()

    def _refresh_sync_bar(self) -> None:
        st = self.app_core.sync_status()
        msg = self.app_core.sync_message()
        if st == "synced":
            self.sync_label.setText("☁ Synced")
            self.retry_btn.hide()
        elif st == "failed":
            self.sync_label.setText(f"⚠ Sync failed — {msg[:80]}")
            self.retry_btn.show()
        elif st == "offline":
            self.sync_label.setText(f"☁ Offline — {msg[:80]}")
            self.retry_btn.show()
        elif st == "skipped":
            self.sync_label.setText("☁ Auto-sync off")
            self.retry_btn.hide()
        else:
            self.sync_label.setText("☁ " + (msg or "Ready"))
            self.retry_btn.hide()
        if self.app_core.circ_path:
            self.active_label.setText(self.app_core.circ_path.name)


def run_gui() -> None:
    import sys
    import traceback

    from circuit_vault import core as core_mod

    qt = QApplication.instance() or QApplication(sys.argv)

    def _excepthook(exc_type, exc, tb) -> None:  # noqa: ANN001
        details = "".join(traceback.format_exception(exc_type, exc, tb))
        summary = str(exc).strip() or exc_type.__name__
        dialog = QMessageBox()
        dialog.setIcon(QMessageBox.Icon.Critical)
        dialog.setWindowTitle("Circuit Vault error")
        dialog.setText(_with_restart_help(summary))
        dialog.setDetailedText(details)
        dialog.exec()

    sys.excepthook = _excepthook

    core = core_mod.get_app()

    if first_run_needed():
        wiz = SetupWizard(core)
        wiz.exec()

    win = MainWindow(core)
    if core.circ_path is None and last_circ_path():
        core.open_project(last_circ_path())  # type: ignore[arg-type]
    if core.circ_path:
        win._refresh_file_tab(force=True)
        win.poll.start()
        win._refresh_target_dropdowns()
    win._refresh_sync_bar()
    win._load_settings_fields()
    win.show()
    qt.exec()
