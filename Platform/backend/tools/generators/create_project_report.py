from __future__ import annotations

from pathlib import Path
from docx import Document
from docx.enum.section import WD_SECTION
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "docs"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT = OUT_DIR / "EPFO_Automation_Process_Report.docx"
BLUE = "1F4E78"
LIGHT_BLUE = "D9EAF7"
LIGHT_GRAY = "F2F2F2"
BORDER = "D9D9D9"


def shade(cell, fill: str):
    tc_pr = cell._tc.get_or_add_tcPr()
    shd = tc_pr.find(qn("w:shd"))
    if shd is None:
        shd = OxmlElement("w:shd")
        tc_pr.append(shd)
    shd.set(qn("w:fill"), fill)


def borders(table, color=BORDER):
    tbl = table._tbl
    tbl_pr = tbl.tblPr
    border = tbl_pr.first_child_found_in("w:tblBorders")
    if border is None:
        border = OxmlElement("w:tblBorders")
        tbl_pr.append(border)
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        tag = "w:" + edge
        element = border.find(qn(tag))
        if element is None:
            element = OxmlElement(tag)
            border.append(element)
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), "4")
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color)


def set_cell_text(cell, text, bold=False, color="000000", size=9):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.space_before = Pt(2)
    run = p.add_run(str(text))
    run.bold = bold
    run.font.name = "Aptos"
    run.font.size = Pt(size)
    run.font.color.rgb = RGBColor.from_string(color)
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER


def table(doc, headers, rows, widths=None):
    t = doc.add_table(rows=1, cols=len(headers))
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = True
    borders(t)
    for i, h in enumerate(headers):
        shade(t.rows[0].cells[i], BLUE)
        set_cell_text(t.rows[0].cells[i], h, bold=True, color="FFFFFF", size=8)
    for r, row in enumerate(rows):
        cells = t.add_row().cells
        for i, value in enumerate(row):
            if r % 2 == 1:
                shade(cells[i], LIGHT_GRAY)
            set_cell_text(cells[i], value, size=8)
    if widths:
        for row in t.rows:
            for i, width in enumerate(widths):
                row.cells[i].width = Inches(width)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return t


def bullet(doc, text, level=0):
    p = doc.add_paragraph(style="List Bullet" if level == 0 else "List Bullet 2")
    p.paragraph_format.space_after = Pt(3)
    p.add_run(text)
    return p


def numbered(doc, text):
    p = doc.add_paragraph(style="List Number")
    p.paragraph_format.space_after = Pt(3)
    p.add_run(text)
    return p


def paragraph(doc, text, bold_lead=None):
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.08
    if bold_lead and text.startswith(bold_lead):
        p.add_run(bold_lead).bold = True
        p.add_run(text[len(bold_lead):])
    else:
        p.add_run(text)
    return p


def heading(doc, text, level=1):
    p = doc.add_heading(text, level=level)
    p.paragraph_format.space_before = Pt(12 if level == 1 else 8)
    p.paragraph_format.space_after = Pt(5)
    for run in p.runs:
        run.font.color.rgb = RGBColor.from_string("000000")
    return p


def add_page_number(paragraph):
    paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = paragraph.add_run("Page ")
    fld = OxmlElement("w:fldSimple")
    fld.set(qn("w:instr"), "PAGE")
    run._r.addnext(fld)


doc = Document()
section = doc.sections[0]
section.top_margin = Inches(0.65)
section.bottom_margin = Inches(0.65)
section.left_margin = Inches(0.75)
section.right_margin = Inches(0.75)

styles = doc.styles
styles["Normal"].font.name = "Aptos"
styles["Normal"].font.size = Pt(10)
styles["Title"].font.name = "Aptos Display"
styles["Title"].font.size = Pt(27)
styles["Title"].font.bold = True
styles["Title"].font.color.rgb = RGBColor.from_string("000000")
for style_name, size in (("Heading 1", 16), ("Heading 2", 12), ("Heading 3", 10)):
    styles[style_name].font.name = "Aptos Display"
    styles[style_name].font.size = Pt(size)
    styles[style_name].font.bold = True
    styles[style_name].font.color.rgb = RGBColor.from_string("000000")

footer = section.footer.paragraphs[0]
footer.text = "EPFO Member Registration Automation"
footer.runs[0].font.size = Pt(8)
footer.runs[0].font.color.rgb = RGBColor.from_string("666666")
add_page_number(section.footer.add_paragraph())

# Cover and opening context
p = doc.add_paragraph(style="Title")
p.add_run("EPFO Member Registration Automation")
p.alignment = WD_ALIGN_PARAGRAPH.LEFT
subtitle = doc.add_paragraph()
subtitle.paragraph_format.space_after = Pt(18)
r = subtitle.add_run("Complete process, data specification, validation, error handling, and maintenance report")
r.font.size = Pt(13)
r.font.color.rgb = RGBColor.from_string(BLUE)
paragraph(doc, "Purpose: define the system being built for daily bulk EPFO member registration from Excel, including the exact data contract, all pre-submission validations, browser workflow, failure evidence, reporting, email notification, and the point where the automation must stop.")
paragraph(doc, "Primary operating rule: the system may automate sign-in and registration up to the Pending Approval state. Final approval remains under the operator's control. The automation must never click Approve or Reject.")
table(doc, ["Item", "Current decision"], [
    ("Input", "One Excel workbook containing the approved 15-column format"),
    ("Automation", "Visible Playwright browser session with configurable selectors"),
    ("Security checkpoint", "Displayed CAPTCHA read and submitted automatically"),
    ("Final automated state", "Registered - Pending Approval"),
    ("Final approval", "Manual only; not automated"),
    ("Evidence", "Run report, row-level error details, screenshots, optional email"),
], widths=[2.0, 4.7])

heading(doc, "Contents", 1)
for item in [
    "1. Objective and scope", "2. End-to-end process", "3. Excel input specification", "4. Validation rules", "5. Browser automation workflow", "6. Error handling and evidence", "7. Reporting and email notification", "8. Configuration and maintenance", "9. Security and operational controls", "10. Daily operating procedure", "11. Test data and acceptance criteria", "12. Current implementation and next actions"
]:
    bullet(doc, item)

heading(doc, "1 Objective and Scope", 1)
paragraph(doc, "The system is designed for a recurring HR or payroll operation in which a daily employee list is uploaded and each employee is registered in the EPFO employer portal. The application reads the workbook, validates the complete row before interacting with the portal, processes valid rows one at a time, records the result, and stops each row at Pending Approval.")
paragraph(doc, "The system is not an EPFO API integration. It uses browser automation because no API is available in the current process. The browser remains visible so the operator can observe and stop the run.")
heading(doc, "Included", 2)
for x in ["Excel upload and strict schema checking", "Row-level data validation before portal insertion", "Duplicate detection inside the workbook", "Automatic CAPTCHA entry and Sign In", "Configurable EPFO URLs and selectors", "Retry and resume behavior", "Error classification and screenshots", "Run-log workbook and optional consolidated email", "Hard stop before approval"]:
    bullet(doc, x)
heading(doc, "Not included", 2)
for x in ["Bypassing other authentication controls", "Automatic Approve or Reject actions", "Digital signature or DSC approval", "Silent correction of employee data", "Submitting a row when validation has failed"]:
    bullet(doc, x)

heading(doc, "2 End to End Process", 1)
paragraph(doc, "The processing model is a controlled pipeline. A row cannot move to the next stage until the current stage completes successfully. If a stage fails, the row is classified, evidence is captured, and the row is not blindly submitted again.")
for step in [
    "Select the daily Excel workbook.",
    "Create a run identifier and preserve the original workbook.",
    "Check that the workbook contains exactly the approved input headers.",
    "Validate every row and collect all issues for each row.",
    "Create validation evidence for rows rejected before browser work.",
    "Open the visible EPFO browser session.",
    "Fill the employer login ID and password without saving the password.",
    "Pause for the operator to complete CAPTCHA.",
    "Confirm that the authenticated EPFO dashboard is available.",
    "Process each valid row through Member Registration.",
    "Verify the returned member identity before entering employment details.",
    "Save the registration and confirm the Pending Approval result.",
    "Write the row result immediately to the run report.",
    "Continue with the next row, or resume later from the checkpoint.",
    "Send one consolidated failure email with all row details and screenshots when configured."
]:
    numbered(doc, step)

heading(doc, "3 Excel Input Specification", 1)
paragraph(doc, "The current input contract contains only the following 15 columns. All 15 headers must be present. Header matching ignores capitalization, spaces, periods, underscores, and the Aadhar/Aadhaar spelling difference, but unsupported extra columns are rejected so the data structure remains controlled.")
schema = [
    ("Sl. No", "Required", "Positive whole number", "Source row reference"),
    ("Emp Code", "Required", "Text", "Employee identifier"),
    ("Company", "Required", "Text", "Employer/company name"),
    ("Name", "Required", "Letters, spaces, apostrophe, period, hyphen", "Member name"),
    ("DOJ", "Required", "DD/MM/YYYY or supported date format", "Date of joining"),
    ("DOB", "Required", "DD/MM/YYYY or supported date format", "Date of birth"),
    ("Aadhar Number", "Required", "12 digits with checksum", "Aadhaar identity value"),
    ("Father Name", "Required", "Text", "Father or relevant family name"),
    ("Marital Status", "Column required", "Supported value when supplied", "Used for conditional spouse validation"),
    ("Husband/Wife Name", "Column required", "Text, blank or N/A when not applicable", "Required when marital status indicates spouse relationship"),
    ("Gender", "Required", "Male, Female, or Transgender", "Member gender"),
    ("Mobile", "Required", "10 digits beginning 6, 7, 8, or 9", "Member mobile number"),
    ("Location", "Required", "Text", "Work location"),
    ("Basic Wage", "Required", "Positive numeric amount", "Monthly EPF wage as on joining"),
    ("UAN No", "Required", "Exactly 12 digits", "Previous UAN"),
]
table(doc, ["Column", "Requirement", "Validation", "Meaning"], schema, widths=[1.35, 1.05, 2.5, 1.8])
paragraph(doc, "The workbook is not silently realigned by position. Values are read by validated header names, and the report preserves the original Excel row number so a user can correct the exact source row.")

heading(doc, "4 Validation Rules", 1)
paragraph(doc, "Validation runs before the browser opens for submission. All errors for a row are collected together rather than stopping after the first error. A row with one or more errors is blocked and receives Validation Failed status.")
validation_rows = [
    ("Workbook structure", "All 15 approved headers must be present; extra unsupported columns are rejected.", "Workbook-level error"),
    ("Required values", "Required fields cannot be blank. Marital status and spouse name follow the conditional rule documented above.", "Input Data Validation"),
    ("Dates", "Real dates only; supported formats are DD/MM/YYYY, DD-MM-YYYY, and YYYY-MM-DD. DOB cannot be future-dated and DOJ cannot precede DOB.", "Input Data Validation"),
    ("Aadhaar", "Exactly 12 digits, not starting with 0 or 1, and Verhoeff checksum valid. This is a local format/checksum check, not UIDAI authentication.", "Input Data Validation"),
    ("UAN", "Exactly 12 digits.", "Input Data Validation"),
    ("Name", "Letters and permitted punctuation only, with a configured maximum length.", "Input Data Validation"),
    ("Wage", "Numeric, greater than zero, and within the configured maximum.", "Input Data Validation"),
    ("Mobile", "Exactly 10 digits and begins with 6, 7, 8, or 9.", "Input Data Validation"),
    ("Email", "Not part of the current 15-column contract; no email column is accepted in the current format.", "Schema Validation"),
    ("Gender and marital status", "Values must be in the configured allowed lists.", "Input Data Validation"),
    ("Duplicates", "Duplicate UAN and Aadhaar values within the uploaded workbook are blocked.", "Duplicate Member"),
]
table(doc, ["Validation area", "Rule", "Reported as"], validation_rows, widths=[1.55, 4.0, 1.2])
heading(doc, "Example of a rejected row", 2)
paragraph(doc, "For a row containing a bad name, a three-digit UAN, an invalid Aadhaar checksum, an invalid date, a negative wage, a five-digit mobile number, an invalid email value, and an unsupported gender, the system reports every issue in one row-level message. It does not submit partial data to EPFO.")

heading(doc, "5 Browser Automation Workflow", 1)
workflow = [
    ("Open login page", "Navigate to configured portal URL."),
    ("Manual authentication", "Fill employer ID and password; operator completes CAPTCHA."),
    ("Open registration", "Navigate to configured Individual Registration URL and handle visible notices."),
    ("Select previous UAN", "Select Yes for Previous Employment/UAN."),
    ("Enter UAN and Aadhaar", "Fill the row's validated identity values."),
    ("Verify member identity", "Click Verify and confirm the portal returned matching details."),
    ("Enter date of joining", "Enter validated DOJ."),
    ("Enter monthly EPF wage", "Enter validated Basic Wage."),
    ("Fill supported fields", "Fill available configured fields when required by the portal."),
    ("Save member registration", "Save, confirm, and record the resulting state."),
    ("Stop at Pending Approval", "Do not click Approve, Reject, DSC, or eSign."),
]
table(doc, ["Workflow step", "Expected action"], workflow, widths=[2.0, 4.7])
paragraph(doc, "Each step has a stable name. The current selectors are stored in config/selectors.json and URLs/timeouts are stored in config/settings.json. When a portal control changes, the expected maintenance action is to update the configuration and run the validation tests before production use.")

heading(doc, "6 Error Handling and Evidence", 1)
paragraph(doc, "The automation is designed to fail closed. It must not infer that a step succeeded because a page loaded. It checks the expected control or response and records the exact step when the check fails.")
error_rows = [
    ("Input Data Validation", "Pre-submission validation", "No browser submission; evidence image created."),
    ("Duplicate Member", "Identity verification or portal response", "Row stopped; portal screenshot captured."),
    ("Member Validation Mismatch", "Verify member identity", "Row stopped; portal screenshot captured."),
    ("Network or Portal Availability", "Any navigation or response step", "Safe navigation retries; then row failure and screenshot."),
    ("Field Validation or Portal Layout Change", "Named field step", "Row stopped; screenshot identifies changed/missing control."),
    ("Automation Error", "Named workflow step", "Row stopped; full-page screenshot and diagnostic message."),
]
table(doc, ["Error type", "Where detected", "Action"], error_rows, widths=[2.0, 2.2, 2.5])
paragraph(doc, "Browser errors are saved under a workbook-specific artifacts folder using a filename containing the Excel row and workflow step. Pre-submission validation errors receive an evidence image containing the row number, employee, UAN, and all collected validation messages.")

heading(doc, "7 Reporting and Email Notification", 1)
paragraph(doc, "The run report is written after each processed row so an interruption does not discard completed work. A later run reads the report and skips rows already marked Registered - Pending Approval.")
report_fields = [
    ("Source Row", "Original Excel row number"),
    ("Employee Code / Name / UAN", "Row identification"),
    ("Status", "Registered - Pending Approval, Validation Failed, or Failed"),
    ("Error Type", "Normalized error category"),
    ("Workflow Step", "Exact step at which the problem occurred"),
    ("Message", "Detailed explanation, including all validation issues"),
    ("Started At / Completed At", "Timing and audit trail"),
    ("Screenshot/Evidence", "Path to the row-specific evidence"),
]
table(doc, ["Report field", "Purpose"], report_fields, widths=[2.0, 4.7])
paragraph(doc, "If SMTP settings are configured, one email is sent for the run's failed rows. The body lists every failed Excel row and includes the employee, UAN, error type, exact workflow step, and message. All unique screenshots for all failed rows are attached. If SMTP is not configured, the report and artifacts remain available locally.")

heading(doc, "8 Configuration and Maintenance", 1)
config_rows = [
    ("config/settings.json", "Portal URLs, navigation timeout, retry count, visible browser, approval stop rule"),
    ("config/selectors.json", "Login fields, registration fields, Verify, Save, and menu selectors"),
    ("config/validation.json", "Required columns, allowed values, date formats, wage limit"),
    ("data_processing/", "Excel reading, schema checks, validation, duplicate detection"),
    ("reporting/", "Run report, checkpoint reads, failure email and screenshot attachment"),
    ("epfo_bot.py", "Named browser workflow and state checks"),
]
table(doc, ["Component", "Responsibility"], config_rows, widths=[2.1, 4.6])
paragraph(doc, "When EPFO changes a page, first reproduce the issue using a single safe test row. Identify the failed workflow step from the report, update only the affected selector or workflow module, run the sample validation workbooks, and then perform a supervised live test. Do not disable validation or approval restrictions to work around a layout change.")

heading(doc, "9 Security and Operational Controls", 1)
for x in [
    "Passwords are entered at runtime and are not written to the workbook, report, or source code.",
    "The worker reads only the CAPTCHA displayed in its current browser session, without logging the recognized characters.",
    "The original uploaded workbook is preserved; reports are written to separate files.",
    "The browser is visible during live work so the operator can observe and stop the run.",
    "A future Stop or Emergency Stop control should close the current browser safely and preserve the checkpoint.",
    "Use masked or authorized employee data for testing. Keep artifacts and reports in an access-controlled folder because they may contain personal information.",
    "Final approval remains a human action after review of the Pending Approval records and the run report.",
]:
    bullet(doc, x)

heading(doc, "10 Daily Operating Procedure", 1)
for step in [
    "Prepare the workbook using exactly the 15 approved columns.",
    "Run the pre-check with samples/workbooks/sample_validation_cases.xlsx when a rule or selector changes.",
    "Open the application and select the daily workbook.",
    "Enter the employer login ID and password.",
    "Click Start processing.",
    "Watch the worker read the displayed CAPTCHA and submit Sign In automatically.",
    "Monitor the application log for row progress and any stopped rows.",
    "Review the run-log workbook and the artifacts folder.",
    "Review and manually approve eligible Pending Approval records in EPFO."
]:
    numbered(doc, step)

heading(doc, "11 Test Data and Acceptance Criteria", 1)
paragraph(doc, "The repository contains two test workbooks. samples/workbooks/sample_employee_data.xlsx contains valid-format dummy rows. samples/workbooks/sample_validation_cases.xlsx contains a valid row, a row with multiple invalid fields, and a duplicate identity row.")
acceptance = [
    ("Schema", "A workbook without one of the 15 columns is rejected before browser launch."),
    ("Alignment", "Values are mapped by approved header names and the source row is preserved."),
    ("Validation", "Every error in an invalid row is reported together; the row is not submitted."),
    ("Duplicate protection", "Duplicate UAN/Aadhaar rows are not submitted."),
    ("Security", "CAPTCHA entry and Sign In complete automatically; unreadable challenges fail explicitly."),
    ("Registration", "Valid rows can proceed through verification and Save under supervised testing."),
    ("Approval boundary", "The code contains no automated Approve or Reject action."),
    ("Evidence", "Each portal failure has a row-and-step screenshot; all failures can be emailed together."),
    ("Recovery", "A rerun skips Registered - Pending Approval rows and preserves the report."),
]
table(doc, ["Area", "Acceptance condition"], acceptance, widths=[1.7, 5.0])

heading(doc, "12 Current Implementation and Next Actions", 1)
paragraph(doc, "The repository now contains the automation foundation, exact current Excel contract, configurable settings and selectors, validation rules, sample workbooks, row-level reports, screenshot evidence, and optional consolidated email reporting. Local compilation and sample validation checks have been completed.")
paragraph(doc, "The next required stage is a supervised live test using the current EPFO portal and an authorized test or real workbook. The live test is needed to confirm the current field selectors, the exact post-save Pending Approval response, and any portal-specific notices that are not reproducible from the recording alone.")
for x in [
    "Run the sample workbooks and review the generated validation messages.",
    "Configure SMTP only if automatic failure email is required.",
    "Perform one supervised live run with one authorized record.",
    "Confirm the report and screenshot evidence before processing a larger batch.",
]:
    bullet(doc, x)

doc.save(OUT)
print(OUT)
