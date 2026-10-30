from __future__ import annotations

import queue
import threading
import os
import json
import uuid
import time
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from Platform.backend.automation.playwright_page import PlaywrightPage, create_playwright_browser

from PF.backend.epfo_bot import EpfoBot, read_records, validate_record
from Platform.backend.authentication import AuthState
from Platform.backend.authentication import auth_mode, ensure_auth_environment, mock_scenario
from PF.backend.mock_epfo_server import MockEpfoServer
from Platform.backend.data_processing.duplicate_checker import duplicate_errors
from Platform.backend.data_processing.validator import validate_record_all, validation_summary
from Platform.backend.reporting.reporting import completed_rows, uncertain_rows, existing_results, write_report
from Platform.backend.reporting.email_reporter import send_failure_email
from Platform.backend.data_processing.data_quality import summarize, write_quality_report
from preflight import run_preflight
from Platform.backend.reporting.audit_log import append_event


def validation_evidence(artifact_dir: Path, row: dict[str, str], error: str) -> str:
    """Create an attachable evidence image for errors found before browser submission."""
    try:
        from PIL import Image, ImageDraw
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"row_{row['row_number']}_precheck_error.png"
        image = Image.new("RGB", (1400, 360), "white")
        draw = ImageDraw.Draw(image)
        draw.text((30, 25), "EPFO automation — pre-submission validation error", fill="black")
        draw.text((30, 80), f"Excel row: {row['row_number']}", fill="black")
        draw.text((30, 120), f"Employee: {row.get('name', '')}", fill="black")
        draw.text((30, 160), f"UAN: {row.get('uan', '')}", fill="black")
        draw.text((30, 220), f"Error: {error}", fill="red")
        image.save(path)
        return str(path)
    except Exception:
        return ""


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("EPFO Registration Automation — stops before approval")
        self.root.geometry("900x650")
        self.events: queue.Queue[tuple[str, object]] = queue.Queue()
        self.file_var = tk.StringVar()
        self.user_var = tk.StringVar()
        self.password_var = tk.StringVar()
        self.running = False
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.manual_login_event = threading.Event()
        self._build()
        self.root.after(150, self._pump)

    def _build(self):
        frame = ttk.Frame(self.root, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="EPFO Member Registration Automation", font=("Segoe UI", 16, "bold")).pack(anchor="w")
        ttk.Label(frame, text="Processes Excel rows and stops after saving each registration. Approval is never automated.").pack(anchor="w", pady=(2, 14))

        form = ttk.LabelFrame(frame, text="Run setup", padding=10)
        form.pack(fill="x")
        ttk.Label(form, text="Excel file").grid(row=0, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(form, textvariable=self.file_var, width=75).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(form, text="Browse", command=self.browse).grid(row=0, column=2, padx=4)
        ttk.Label(form, text="Employer login ID").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(form, textvariable=self.user_var, width=35).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(form, text="Password (not saved)").grid(row=2, column=0, sticky="w", padx=4, pady=4)
        ttk.Entry(form, textvariable=self.password_var, show="*", width=35).grid(row=2, column=1, sticky="w", padx=4)
        form.columnconfigure(1, weight=1)

        controls = ttk.Frame(frame)
        controls.pack(fill="x", pady=10)
        self.start_btn = ttk.Button(controls, text="Start processing", command=self.start)
        self.start_btn.pack(side="left")
        self.pause_btn = ttk.Button(controls, text="Pause", command=self.toggle_pause, state="disabled")
        self.pause_btn.pack(side="left", padx=4)
        self.stop_btn = ttk.Button(controls, text="Stop", command=self.stop, state="disabled")
        self.stop_btn.pack(side="left", padx=4)
        self.continue_btn = ttk.Button(controls, text="Refresh CAPTCHA checkpoint", command=self.manual_login_event.set, state="disabled")
        self.continue_btn.pack(side="left", padx=8)
        self.progress = ttk.Progressbar(controls, mode="indeterminate")
        self.progress.pack(side="right", fill="x", expand=True, padx=8)

        self.log_box = tk.Text(frame, height=25, wrap="word", state="disabled")
        self.log_box.pack(fill="both", expand=True)
        ttk.Label(frame, text="CAPTCHA is always manual. Never share the workbook or credentials unnecessarily.").pack(anchor="w", pady=(8, 0))

    def browse(self):
        path = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx *.xlsm"), ("All files", "*.*")])
        if path:
            self.file_var.set(path)

    def log(self, message: str):
        self.events.put(("log", message))

    def start(self):
        if self.running:
            return
        path = Path(self.file_var.get().strip())
        if not path.exists():
            messagebox.showerror("Missing workbook", "Select an Excel workbook first.")
            return
        mock_run = os.getenv("EPFO_AUTH_MODE", "REAL").upper() == "MOCK"
        if not mock_run and (not self.user_var.get().strip() or not self.password_var.get()):
            messagebox.showerror("Missing login", "Enter the employer login ID and password.")
            return
        self.running = True
        self.stop_event.clear()
        self.pause_event.clear()
        self.start_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal", text="Pause")
        self.stop_btn.configure(state="normal")
        self.continue_btn.configure(state="normal")
        self.progress.start(12)
        threading.Thread(target=self.worker, args=(path, self.user_var.get().strip(), self.password_var.get()), daemon=True).start()

    def toggle_pause(self):
        if not self.running:
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_btn.configure(text="Pause")
            self.log("Resume requested; processing will continue at the next safe checkpoint.")
        else:
            self.pause_event.set()
            self.pause_btn.configure(text="Resume")
            self.log("Pause requested; the current browser operation will finish before waiting.")

    def stop(self):
        if self.running:
            self.stop_event.set()
            self.pause_event.clear()
            self.log("Stop requested; no new rows will be started after the current safe checkpoint.")

    def worker(self, workbook: Path, username: str, password: str):
        try:
            run_id = "RUN-" + datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4].upper()
            audit_path = workbook.parent / f"{workbook.stem}_artifacts" / run_id / "audit.jsonl"
            append_event(audit_path, run_id=run_id, event="RUN_CREATED", status="STARTED")
            run_settings = json.loads((Path(__file__).resolve().parent / "config" / "settings.json").read_text(encoding="utf-8"))
            run_mode = auth_mode(run_settings)
            ensure_auth_environment(run_settings, run_mode)
            if run_mode == "MOCK":
                username = os.getenv("MOCK_EPFO_EMPLOYER_ID", "TEST_EMPLOYER")
                password = os.getenv("MOCK_EPFO_PASSWORD", "TEST_PASSWORD")
            records = read_records(workbook)
            self.log(f"Loaded {len(records)} rows from {workbook.name}.")
            report_path = workbook.with_name(workbook.stem + "_run_log.xlsx")
            artifact_dir = workbook.parent / (workbook.stem + "_artifacts") / run_id
            quality = summarize(records)
            quality_path = workbook.with_name(workbook.stem + "_data_quality.xlsx")
            write_quality_report(quality_path, quality)
            self.log(f"Data quality: total={quality.total}, valid={quality.valid}, invalid={quality.invalid}, duplicates={quality.duplicates}.")
            append_event(audit_path, run_id=run_id, event="VALIDATION_COMPLETED", status="SUCCESS", total=quality.total, valid=quality.valid, invalid=quality.invalid, duplicates=quality.duplicates)
            done_rows = completed_rows(report_path)
            uncertain = uncertain_rows(report_path)
            if done_rows:
                self.log(f"Resume mode: skipping {len(done_rows)} rows already saved as Pending Approval.")
            if uncertain:
                self.log(f"Resume safety: {len(uncertain)} rows have unknown Save results and require manual verification; they will not be resubmitted.")
            duplicate_map = duplicate_errors(records)
            if duplicate_map:
                self.log(f"Found {len(duplicate_map)} duplicate rows; they will not be submitted.")
            result_rows = existing_results(report_path)
            for record in records:
                if record["row_number"] in done_rows or record["row_number"] in uncertain:
                    continue
                errors = validate_record_all(record)
                if record["row_number"] in duplicate_map:
                    errors.append({"column": "uan/aadhaar", "cell": f"uan/aadhaar (Excel row {record['row_number']})", "message": duplicate_map[record["row_number"]]})
                if errors:
                    error = validation_summary(errors)
                    evidence = validation_evidence(artifact_dir, record, error)
                    result_rows.append({"Source Row": record["row_number"], "Employee Code": record.get("emp_code", ""), "Employee Name": record.get("name", ""), "UAN": record.get("uan", ""), "Status": "Validation Failed", "Error Type": "Input Data Validation", "Workflow Step": "Pre-submission validation", "Message": error, "Started At": "", "Completed At": datetime.now().isoformat(timespec="seconds"), "Screenshot/Evidence": evidence})
                    self.log(f"Row {record['row_number']}: validation failed — {error}")
            valid = [r for r in records if not validate_record_all(r) and r["row_number"] not in duplicate_map and r["row_number"] not in done_rows and r["row_number"] not in uncertain]
            single_row = os.getenv("SINGLE_ROW", str(run_settings.get("single_row", ""))).strip()
            if single_row:
                valid = [record for record in valid if record["row_number"] == single_row or record.get("sl_no") == single_row]
                self.log(f"Single-row mode enabled for {single_row}; {len(valid)} row(s) selected.")
            batch_size = int(os.getenv("BATCH_SIZE", str(run_settings.get("batch_size", 0))) or 0)
            if batch_size > 0:
                valid = valid[:batch_size]
                self.log(f"Batch limit enabled: {batch_size} row(s).")
            self.log(f"Validated {len(valid)} rows; {len(records) - len(valid)} rows will be skipped.")
            if not valid:
                write_report(report_path, result_rows)
                try:
                    send_failure_email(result_rows, workbook.stem, self.log)
                except Exception as email_error:
                    self.log(f"Failure email could not be sent: {email_error}")
                self.events.put(("done", f"No valid rows to submit. Report saved as {report_path.name}"))
                return
            dry_run = os.getenv("DRY_RUN", str(run_settings.get("dry_run", False))).lower() in {"1", "true", "yes"}
            if dry_run:
                for record in valid:
                    result_rows.append({"Source Row": record["row_number"], "Employee Code": record.get("emp_code", ""), "Employee Name": record.get("name", ""), "UAN": record.get("uan", ""), "Status": "Dry Run - Ready", "Error Type": "", "Workflow Step": "Workflow simulation", "Message": "Validated and duplicate-free; no browser submission performed.", "Started At": "", "Completed At": datetime.now().isoformat(timespec="seconds"), "Screenshot/Evidence": ""})
                write_report(report_path, result_rows)
                self.log("DRY_RUN enabled; no browser was launched and no registration was submitted.")
                self.events.put(("done", f"Dry run complete. Report saved as {report_path.name}"))
                return
            preflight = run_preflight(workbook, artifact_dir, run_settings, check_portal=auth_mode(run_settings) == "REAL")
            for check in preflight.checks:
                self.log(f"Preflight {check['name']}: {check['status']} — {check['message']}")
            if not preflight.passed:
                message = "; ".join(f"{check['name']}: {check['message']}" for check in preflight.failures)
                result_rows.append({"Source Row": "", "Employee Code": "", "Employee Name": "", "UAN": "", "Status": "Preflight Failed", "Error Type": "Configuration or Portal Preflight", "Workflow Step": "Preflight", "Message": message, "Started At": "", "Completed At": datetime.now().isoformat(timespec="seconds"), "Screenshot/Evidence": ""})
                write_report(report_path, result_rows)
                append_event(audit_path, run_id=run_id, event="RUN_FAILED", status="FAILED", workflow_step="PREFLIGHT", message=message)
                self.events.put(("done", f"Preflight failed. Report saved as {report_path.name}"))
                return
            browser = create_playwright_browser()
            page = PlaywrightPage(browser.page)
            if True:
                bot = EpfoBot(page, self.log, artifact_dir)
                mock_server = None
                if auth_mode(bot.settings) == "MOCK":
                    mock_server = MockEpfoServer(mock_scenario(bot.settings))
                    bot.mock_base_url = mock_server.start()
                    self.log(f"Started isolated localhost mock EPFO at {bot.mock_base_url}.")
                    bot.login_page(username, password, f"{bot.mock_base_url}/login")
                else:
                    bot.login_page(username, password)
                self.manual_login_event.clear()
                auth = bot.authenticate(continue_event=self.manual_login_event)
                if auth.state != AuthState.REGISTRATION_READY:
                    result_rows.append({"Source Row": "", "Employee Code": "", "Employee Name": "", "UAN": "", "Status": "Authentication Failed", "Error Type": auth.error_type, "Workflow Step": auth.state.value, "Message": auth.message, "Started At": "", "Completed At": datetime.now().isoformat(timespec="seconds"), "Screenshot/Evidence": auth.screenshot})
                    write_report(report_path, result_rows)
                    self.log(f"Authentication stopped safely: {auth.message}")
                    try:
                        send_failure_email(result_rows, workbook.stem, self.log)
                    except Exception as email_error:
                        self.log(f"Failure email could not be sent: {email_error}")
                    self.events.put(("done", f"Authentication stopped safely. Report saved as {report_path.name}"))
                    if mock_server:
                        mock_server.stop()
                    return
                self.log("Manual authentication verified; continuing to member registration.")
                for record in valid:
                    while self.pause_event.is_set() and not self.stop_event.is_set():
                        time.sleep(0.25)
                    if self.stop_event.is_set():
                        append_event(audit_path, run_id=run_id, event="RUN_COMPLETED", status="STOPPED", workflow_step="STOP")
                        self.log("Stop completed at a safe row boundary.")
                        break
                    self.log(f"Starting row {record['row_number']}: {record['name']}")
                    append_event(audit_path, run_id=run_id, event="ROW_STARTED", status="STARTED", row=record["row_number"], workflow_step="REGISTRATION")
                    started = datetime.now().isoformat(timespec="seconds")
                    result = bot.register_one(record)
                    result_rows.append({"Source Row": result.row_number, "Employee Code": record.get("emp_code", ""), "Employee Name": result.name, "UAN": record.get("uan", ""), "Status": result.status, "Error Type": result.error_type, "Workflow Step": result.workflow_step, "Message": result.message, "Started At": started, "Completed At": datetime.now().isoformat(timespec="seconds"), "Screenshot/Evidence": result.screenshot})
                    self.log(f"Row {result.row_number}: {result.status} — {result.message}")
                    append_event(audit_path, run_id=run_id, event="ROW_COMPLETED" if result.status == "Registered - Pending Approval" else "ROW_FAILED", status=result.status, row=result.row_number, workflow_step=result.workflow_step, message=result.message)
                    write_report(report_path, result_rows)
                browser.close()
                if mock_server:
                    mock_server.stop()
            write_report(report_path, result_rows)
            try:
                send_failure_email(result_rows, workbook.stem, self.log)
            except Exception as email_error:
                self.log(f"Failure email could not be sent: {email_error}")
            self.events.put(("done", f"Completed. Report saved beside the workbook as {workbook.stem}_run_log.xlsx"))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _pump(self):
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self.log_box.configure(state="normal")
                    self.log_box.insert("end", str(value) + "\n")
                    self.log_box.see("end")
                    self.log_box.configure(state="disabled")
                elif kind in ("done", "error"):
                    self.running = False
                    self.start_btn.configure(state="normal")
                    self.continue_btn.configure(state="disabled")
                    self.pause_btn.configure(state="disabled", text="Pause")
                    self.stop_btn.configure(state="disabled")
                    self.pause_event.clear()
                    self.progress.stop()
                    (messagebox.showinfo if kind == "done" else messagebox.showerror)("EPFO automation", str(value))
        except queue.Empty:
            pass
        self.root.after(150, self._pump)


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
