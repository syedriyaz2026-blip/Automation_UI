import fs from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const rootDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");
const workbookDir = path.join(rootDir, "samples", "workbooks");
const reportDir = path.join(rootDir, "reports", "qa");
await fs.mkdir(workbookDir, { recursive: true });
await fs.mkdir(reportDir, { recursive: true });
const workbook = Workbook.create();
const sheet = workbook.worksheets.add("Employees");
sheet.showGridLines = false;

const headers = [
  "Sl. No", "Emp Code", "Company", "Name", "DOJ", "DOB", "Aadhar Number",
  "Father Name", "Marital Status", "Husband/Wife Name", "Gender", "Mobile",
  "Location", "Basic Wage", "UAN No",
];

const rows = [
  [1, "TEST001", "Mock Consulting Ltd", "Selva", "05/10/2026", "19/09/1997", "762330210052", "Selva Father", "Un-Married", "N/A", "Male", "9000000001", "Bengaluru", 18600, "100000000001"],
  [2, "TEST002", "Mock Consulting Ltd", "Manoj", "05/10/2026", "22/02/1995", "719680799020", "Manoj Father", "Married", "Manoj Spouse", "Male", "9000000002", "Bengaluru", 22000, "100000000002"],
  [3, "TEST003", "Mock Consulting Ltd", "Karthick", "05/10/2026", "10/11/1996", "613953767240", "Karthick Father", "Un-Married", "N/A", "Male", "9000000003", "Chennai", 19500, "100000000003"],
  [4, "TEST004", "Mock Consulting Ltd", "Priya", "05/10/2026", "15/06/1998", "438849696530", "Priya Father", "Un-Married", "N/A", "Female", "9000000004", "Bengaluru", 21000, "100000000004"],
  [5, "TEST005", "Mock Consulting Ltd", "Riyaz", "05/10/2026", "28/01/1994", "487101226913", "Riyaz Father", "Married", "Riyaz Spouse", "Male", "9000000005", "Hyderabad", 24000, "100000000005"],
];

sheet.getRange("A1:O6").values = [headers, ...rows];
sheet.getRange("A1:O1").format = {
  fill: "#1F4E78",
  font: { name: "Aptos", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
  wrapText: true,
};
sheet.getRange("A2:O6").format = {
  font: { name: "Aptos", size: 10, color: "#1F2937" },
  verticalAlignment: "center",
};
sheet.getRange("A2:A6").format.horizontalAlignment = "center";
sheet.getRange("G2:G6").format.numberFormat = "@";
sheet.getRange("O2:O6").format.numberFormat = "@";
sheet.getRange("L2:L6").format.numberFormat = "@";
sheet.getRange("N2:N6").format.numberFormat = "#,##0";
sheet.getRange("A1:O6").format.borders = { preset: "all", style: "thin", color: "#D9E2F3" };
sheet.getRange("A1:O6").format.autofitColumns();
sheet.getRange("A1:O6").format.autofitRows();
sheet.getRange("A1:O6").format.rowHeight = 22;
sheet.getRange("A1:O1").format.rowHeight = 34;
sheet.freezePanes.freezeRows(1);

workbook.recalculate();
const inspect = await workbook.inspect({ kind: "table", range: "Employees!A1:O6", include: "values", tableMaxRows: 6, tableMaxCols: 15 });
console.log(inspect.ndjson);
const preview = await workbook.render({ sheetName: "Employees", range: "A1:O6", scale: 1, format: "png" });
await fs.writeFile(path.join(reportDir, "sample_test_employees_preview.png"), new Uint8Array(await preview.arrayBuffer()));
const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(path.join(workbookDir, "sample_test_employees.xlsx"));
console.log(path.join(workbookDir, "sample_test_employees.xlsx"));
