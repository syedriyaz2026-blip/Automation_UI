"""Read-only source adapters and the normalized employee-data aggregator.

The local application exposes one stable contract while the source system is
split across company APIs, PF master APIs, and payroll/employee tables.  This
module deliberately contains SELECT-only SQL.  It never calls a stored
procedure and never executes INSERT, UPDATE, DELETE, MERGE, or DDL.

Configuration is supplied through environment variables so source credentials
never reach the browser or the frontend:

    PF_READONLY_DB_CONNECTION
    EMANAGE_API_BASE_URL
    EMANAGE_API_TOKEN (optional)
    EMANAGE_API_KEY (optional)
    EMANAGE_API_USER_ID (optional)
    EMANAGE_API_TIMEOUT_SECONDS (optional, default 20)
    EMANAGE_ESI_EMPLOYEE_ROUTE (required for ESI source synchronization)
    EMANAGE_ESI_EMPLOYEE_METHOD (optional, default POST)
    EMANAGE_SALARY_API_ENABLED (optional, default true when the API is configured)
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from Platform.backend.data_processing.validator import validate_record_all


def _load_local_source_env() -> None:
    """Load the local source adapter settings when the service is started directly.

    The desktop launcher can provide environment variables itself, but the API is
    also intentionally runnable as a standalone local service.  Environment
    variables always win; this file is only a local fallback and is never sent to
    the browser.
    """
    env_path = Path(__file__).with_name("config") / "source_data.env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_local_source_env()


class SourceDataError(RuntimeError):
    """Raised when a configured source cannot be read safely."""


PF_REQUIRED_FIELD_LABELS = {
    "uan": "UAN No",
    "aadhaar": "Aadhar Number",
    "name": "Name",
    "dob": "DOB",
    "doj": "DOJ",
    "basic_wage": "Basic Wage",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _first(item: dict[str, Any], *names: str, default: Any = None) -> Any:
    lowered = {str(key).lower(): value for key, value in item.items()}
    for name in names:
        if name.lower() in lowered and lowered[name.lower()] not in (None, ""):
            return lowered[name.lower()]
    return default


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


RECENT_JOINING_WINDOW_HOURS = 48


def _as_naive_datetime(value: Any) -> datetime | None:
    """Convert source date shapes into a comparable local-naive datetime."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        parsed = None
        for pattern in (
            "%d-%b-%Y",
            "%d-%B-%Y",
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%Y/%m/%d",
            "%m/%d/%Y",
        ):
            try:
                parsed = datetime.strptime(raw[:10], pattern)
                break
            except ValueError:
                continue
    if parsed is None:
        return None
    return parsed.replace(tzinfo=None)


def _recent_joining_cutoff() -> datetime:
    return datetime.now().replace(tzinfo=None) - timedelta(hours=RECENT_JOINING_WINDOW_HOURS)


def _canonical_date(value: Any) -> str:
    """Return the single date format used by PF intake: DD-MM-YYYY."""
    parsed = _as_naive_datetime(value)
    return parsed.strftime("%d-%m-%Y") if parsed else _text(value)


def _canonical_digits(value: Any) -> str:
    """Preserve identifier digits while handling numeric API/SQL values safely."""
    if value in (None, ""):
        return ""
    if isinstance(value, int):
        return str(value)
    raw = _text(value)
    if re.fullmatch(r"\d+\.0+", raw):
        raw = raw.split(".", 1)[0]
    return re.sub(r"\D", "", raw)


def _canonical_wage(value: Any) -> Any:
    """Normalize Basic Wage to a plain numeric value without display commas."""
    if value in (None, ""):
        return ""
    raw = _text(value).replace(",", "")
    try:
        amount = Decimal(raw)
    except Exception:
        return raw
    normalized = format(amount, "f").rstrip("0").rstrip(".")
    return normalized or "0"


def _canonical_pf_field(field: str, value: Any) -> Any:
    if field in {"dob", "doj"}:
        return _canonical_date(value)
    if field in {"uan", "aadhaar"}:
        return _canonical_digits(value)
    if field == "basic_wage":
        return _canonical_wage(value)
    if field in {"name", "gender", "father_name", "marital_status", "mobile", "email"}:
        return _text(value)
    return value


def _envelope_items(payload: Any) -> list[dict[str, Any]]:
    """Unwrap the source API's list/data/status response shapes."""
    if isinstance(payload, list):
        result: list[dict[str, Any]] = []
        for item in payload:
            if isinstance(item, dict):
                data = _first(item, "data", "Data")
                if isinstance(data, list):
                    result.extend(x for x in data if isinstance(x, dict))
                elif isinstance(data, dict):
                    result.append(data)
                elif not any(str(k).lower() == "status" for k in item):
                    result.append(item)
        return result
    if isinstance(payload, dict):
        for key in ("data", "Data", "items", "Items", "results", "Results"):
            value = _first(payload, key)
            if isinstance(value, (list, dict)):
                return _envelope_items(value)
        return [payload]
    return []


def _safe_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", value or ""):
        raise SourceDataError("Unsafe database identifier returned by source metadata.")
    return value


class ReadOnlyDatabase:
    """Small pyodbc adapter whose public methods contain SELECT-only queries."""

    def __init__(self, connection_string: str | None = None):
        self.connection_string = (
            connection_string
            or os.getenv("PF_READONLY_DB_CONNECTION", "")
            or os.getenv("PF_SOURCE_DB_CONNECTION", "")
            or os.getenv("EMANAGE_READONLY_DB_CONNECTION", "")
        )

    @property
    def configured(self) -> bool:
        return bool(self.connection_string.strip())

    def _connect(self):
        if not self.configured:
            raise SourceDataError("PF_READONLY_DB_CONNECTION is not configured.")
        try:
            import pyodbc
        except ImportError as exc:  # pragma: no cover - depends on deployment image
            raise SourceDataError("pyodbc is required for the configured read-only database source.") from exc
        connection = self.connection_string
        if "ApplicationIntent=" not in connection:
            connection += ";ApplicationIntent=ReadOnly"
        return pyodbc.connect(connection, timeout=10, autocommit=True)

    @staticmethod
    def _rows(cursor) -> list[dict[str, Any]]:
        columns = [column[0] for column in cursor.description or []]
        return [{column: _jsonable(value) for column, value in zip(columns, row)} for row in cursor.fetchall()]

    def _query(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        # All callers pass literal SELECT text from this module. Parameters are
        # always bound through the driver; request data is never interpolated.
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(sql, tuple(params))
            return self._rows(cursor)
        except Exception as exc:
            raise SourceDataError(f"Read-only database query failed: {exc}") from exc
        finally:
            connection.close()

    def companies(self) -> list[dict[str, Any]]:
        rows = self._query(
            """
            SELECT int_cid AS company_id, varchar_companyname AS company_name,
                   varchar_companycode AS company_code, varchar_ShortName AS short_name
            FROM dbo.im_companydetails
            WHERE varchar_internalclient = 'Internal'
            ORDER BY varchar_companyname
            """
        )
        return [
            {
                "id": str(row.get("company_id")),
                "company_id": str(row.get("company_id")),
                "name": _text(row.get("company_name")),
                "code": _text(row.get("company_code")),
                "short_name": _text(row.get("short_name")),
                "scope_type": "company",
                "source": "db.im_companydetails",
            }
            for row in rows
        ]

    def deputed_companies(self, company_id: int) -> list[dict[str, Any]]:
        """Return the same parent/deputed relationship as Emanage's Api_DeputedCompanylist.

        The old implementation read vwDeputedClientList.companyid.  That view
        is a reporting view and does not represent the scope relationship used
        by Emanage.  The source procedure uses int_cid -> int_scid through
        im_employeetransitionvalue and im_subclient, so this query deliberately
        mirrors that contract.
        """
        rows = self._query(
            """
            SELECT a.int_transvalueid AS deputed_company_id,
                   a.varchar_transvalue AS deputed_company_name,
                   c.varchar_companyname AS company_name,
                   a.int_cid AS company_id,
                   a.int_transitionid AS transition_id,
                   b.int_hoclient AS head_office_company_id,
                   b.varchar_status AS deputed_status
            FROM dbo.im_employeetransitionvalue AS a
            INNER JOIN dbo.im_subclient AS b
                ON a.int_transvalueid = b.int_scid
            INNER JOIN dbo.im_companydetails AS c
                ON b.int_hoclient = c.int_cid
            WHERE a.int_cid = ?
              AND a.int_transitionid = 11
              AND b.varchar_status = 'Active'
            ORDER BY a.varchar_transvalue
            """,
            (company_id,),
        )
        result = [
            {
                "id": str(row.get("deputed_company_id")),
                "deputed_company_id": str(row.get("deputed_company_id")),
                "name": _text(row.get("deputed_company_name")),
                "company_id": str(row.get("company_id")),
                "parent_company_id": str(row.get("company_id")),
                "parent_company_name": _text(row.get("company_name")),
                "head_office_company_id": str(row.get("head_office_company_id")),
                "transition_id": _as_int(row.get("transition_id")),
                "status": _text(row.get("deputed_status")),
                "is_all": False,
                "scope_type": "deputed_company",
                "source": "db.im_employeetransitionvalue+im_subclient",
            }
            for row in rows
        ]
        if result:
            result.insert(
                0,
                {
                    "id": "0",
                    "deputed_company_id": "0",
                    "name": "ALL",
                    "company_id": str(company_id),
                    "parent_company_id": str(company_id),
                    "parent_company_name": result[0]["parent_company_name"],
                    "head_office_company_id": result[0]["head_office_company_id"],
                    "transition_id": 11,
                    "status": "Active",
                    "is_all": True,
                    "scope_type": "deputed_company",
                    "source": "db.Api_DeputedCompanylist",
                },
            )
        return result

    def company(self, company_id: int) -> dict[str, Any] | None:
        rows = self._query(
            """
            SELECT TOP (1) int_cid AS company_id, varchar_companyname AS company_name,
                   varchar_companycode AS company_code, varchar_ShortName AS short_name
            FROM dbo.im_companydetails WHERE int_cid = ?
            """,
            (company_id,),
        )
        return rows[0] if rows else None

    def employees(
        self,
        company_id: int,
        deputed_company_id: int | None,
        employee_code: str,
        limit: int,
        offset: int,
        updated_since: datetime | None = None,
        joined_since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        filters = ["e.int_cid = ?"]
        params: list[Any] = [company_id]
        # Emanage represents "ALL" with deputed company ID 0.  It means the
        # selected parent company is still the scope, but no child filter is
        # applied to the employee query.
        if deputed_company_id not in (None, 0):
            filters.append("e.int_dcid = ?")
            params.append(deputed_company_id)
        if employee_code:
            filters.append("e.varchar_empno = ?")
            params.append(employee_code)
        if updated_since is not None:
            filters.append("e.date_updated >= ?")
            params.append(updated_since)
        if joined_since is not None:
            filters.append("e.date_joining >= ?")
            params.append(joined_since)
        params.extend([offset, limit])
        return self._query(
            f"""
            SELECT e.int_employeeid AS employee_id, e.varchar_empno AS employee_code,
                   e.varchar_fullname AS name, e.varchar_fathername AS father_name,
                   e.varchar_gender AS gender, e.date_dob AS dob, e.date_joining AS doj,
                   e.varchar_aadhar AS aadhaar, e.varchar_pan AS pan,
                   e.varchar_mobilenumber AS mobile, e.varchar_personalemail AS email,
                   e.int_cid AS company_id, e.int_dcid AS deputed_company_id,
                   e.date_updated AS updated_at, e.int_iscurrent AS is_current,
                   e.int_status AS employee_status,
                   pf.varchar_pfuannumber AS uan, pf.varchar_pfnumber AS pf_number,
                   pf.bool_pfscheme AS pf_applicable
            FROM dbo.im_employeedetails e
            OUTER APPLY (
                SELECT TOP (1) p.varchar_pfuannumber, p.varchar_pfnumber,
                       p.bool_pfscheme
                FROM dbo.im_employeepfdetails p
                WHERE p.int_employeeid = e.int_employeeid
                ORDER BY p.int_pfid DESC
            ) pf
            WHERE {' AND '.join(filters)}
            ORDER BY e.int_employeeid
            OFFSET ? ROWS FETCH NEXT ? ROWS ONLY
            """,
            params,
        )

    def fixed_basic(self, company_id: int, employee_codes: list[str]) -> dict[str, dict[str, Any]]:
        if not employee_codes:
            return {}
        marks = ",".join("?" for _ in employee_codes)
        rows = self._query(
            f"""
            SELECT varchar_empno AS employee_code, double_fixedbasic AS basic_wage,
                   date_updateddate AS updated_at
            FROM dbo.im_clientaddemployee
            WHERE int_cid = ? AND varchar_empno IN ({marks})
            ORDER BY date_updateddate DESC, int_rowid DESC
            """,
            [company_id, *employee_codes],
        )
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            code = _text(row.get("employee_code"))
            if code and code not in result and row.get("basic_wage") is not None:
                result[code] = {"value": row.get("basic_wage"), "source": "db.im_clientaddemployee.double_fixedbasic"}
        return result

    def salary_card_basic(self, short_name: str, employee_ids: list[int]) -> dict[int, dict[str, Any]]:
        if not short_name or not employee_ids:
            return {}
        short_name = _safe_identifier(short_name)
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(
                """
                SELECT TABLE_SCHEMA, TABLE_NAME
                FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA IN ('sc', 'dbo')
                  AND TABLE_NAME LIKE ?
                  AND (TABLE_NAME LIKE '%_SCSalaryCard' OR TABLE_NAME LIKE '%_PFSalaryCard')
                ORDER BY TABLE_NAME DESC
                """,
                (short_name + "_%",),
            )
            candidates = [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
            result: dict[int, dict[str, Any]] = {}
            for schema, table in candidates:
                _safe_identifier(schema)
                _safe_identifier(table)
                cursor.execute(
                    """
                    SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?
                      AND COLUMN_NAME IN (
                          'BASIC_STATUTORY','FULL_BASIC','BASIC_A','BASIC','BASIC_DA',
                          'Cal_EPFWages','EPF_WAGES_STATURORY','EPF_WAGES_STATUTORY',
                          'PF_WAGE','PF_WAGES','EPF_WAGE'
                      )
                    ORDER BY CASE COLUMN_NAME
                        WHEN 'BASIC_STATUTORY' THEN 1 WHEN 'FULL_BASIC' THEN 2
                        WHEN 'BASIC_A' THEN 3 WHEN 'BASIC' THEN 4 WHEN 'BASIC_DA' THEN 5
                        WHEN 'Cal_EPFWages' THEN 6 WHEN 'EPF_WAGES_STATURORY' THEN 7
                        WHEN 'EPF_WAGES_STATUTORY' THEN 8 WHEN 'PF_WAGE' THEN 9
                        WHEN 'PF_WAGES' THEN 10 WHEN 'EPF_WAGE' THEN 11 ELSE 99 END
                    """,
                    (schema, table),
                )
                wage_columns = [str(row[0]) for row in cursor.fetchall()]
                if not wage_columns:
                    continue
                wage_sql = ", ".join(f"[{column}]" for column in wage_columns)
                for start in range(0, len(employee_ids), 900):
                    batch = employee_ids[start:start + 900]
                    marks = ",".join("?" for _ in batch)
                    cursor.execute(
                        f"SELECT int_employeeid, {wage_sql} FROM [{schema}].[{table}] WHERE int_employeeid IN ({marks})",
                        batch,
                    )
                    columns = [column[0] for column in cursor.description or []]
                    for raw in cursor.fetchall():
                        row = dict(zip(columns, raw))
                        employee_id = _as_int(row.get("int_employeeid"))
                        if employee_id is None or employee_id in result:
                            continue
                        for column in wage_columns:
                            if row.get(column) not in (None, ""):
                                result[employee_id] = {
                                    "value": _jsonable(row[column]),
                                    "source": f"db.{schema}.{table}.{column}",
                                }
                                break
                if len(result) == len(employee_ids):
                    break
            return result
        except Exception as exc:
            raise SourceDataError(f"Read-only salary-card lookup failed: {exc}") from exc
        finally:
            connection.close()

    def hrms_basic_wage(self, company_id: int, employee_ids: list[int]) -> dict[int, dict[str, Any]]:
        """Read the HRMS Basic component used by the salary screen.

        The supplied HRMS source uses ``im_hrsalarystructure`` to identify the
        visible Income component named ``Basic`` and ``im_hrsalarymaster`` for
        its employee-specific amount.  This is the same contract implemented
        by ``Api_Admin_GetEmployeeSalaryDataWithOrder`` in the supplied API
        source.  The two small form tables are read only fallbacks for legacy
        onboarding records where the salary master has not been materialized.
        """
        if not employee_ids:
            return {}
        marks = ",".join("?" for _ in employee_ids)
        result: dict[int, dict[str, Any]] = {}
        try:
            rows = self._query(
                f"""
                SELECT e.int_employeeid AS employee_id,
                       m.double_amount AS basic_wage,
                       m.date_from AS effective_date
                FROM dbo.im_employeedetails AS e
                INNER JOIN dbo.im_hrsalarymaster AS m
                    ON m.int_empid = e.int_employeeid
                INNER JOIN dbo.im_hrsalarystructure AS s
                    ON s.int_componentid = m.int_componentid
                   AND s.int_cid = e.int_cid
                WHERE e.int_cid = ?
                  AND e.int_employeeid IN ({marks})
                  AND s.int_parentid <> 0
                  AND s.int_visible = 1
                  AND s.varchar_type = 'Income'
                  AND LOWER(LTRIM(RTRIM(s.varchar_name))) IN ('basic', 'basic wage')
                ORDER BY e.int_employeeid, m.date_from DESC
                """,
                [company_id, *employee_ids],
            )
            for row in rows:
                employee_id = _as_int(row.get("employee_id"))
                value = row.get("basic_wage")
                if employee_id is not None and employee_id not in result and value not in (None, ""):
                    result[employee_id] = {
                        "value": value,
                        "source": "sql_server_read_only.im_hrsalarystructure.Basic+im_hrsalarymaster.double_amount",
                    }

            missing = [employee_id for employee_id in employee_ids if employee_id not in result]
            if missing:
                fallback_marks = ",".join("?" for _ in missing)
                rows = self._query(
                    f"""
                    SELECT int_empid AS employee_id, double_basic AS basic_wage
                    FROM dbo.im_formtwagesinput
                    WHERE int_empid IN ({fallback_marks})
                      AND double_basic IS NOT NULL
                    """,
                    missing,
                )
                for row in rows:
                    employee_id = _as_int(row.get("employee_id"))
                    value = row.get("basic_wage")
                    if employee_id is not None and employee_id not in result and value not in (None, ""):
                        result[employee_id] = {
                            "value": value,
                            "source": "sql_server_read_only.im_formtwagesinput.double_basic",
                        }

            missing = [employee_id for employee_id in employee_ids if employee_id not in result]
            if missing:
                fallback_marks = ",".join("?" for _ in missing)
                rows = self._query(
                    f"""
                    SELECT int_empid AS employee_id, double_wagesfixed AS basic_wage
                    FROM dbo.im_formtemployeeinput
                    WHERE int_empid IN ({fallback_marks})
                      AND double_wagesfixed IS NOT NULL
                    """,
                    missing,
                )
                for row in rows:
                    employee_id = _as_int(row.get("employee_id"))
                    value = row.get("basic_wage")
                    if employee_id is not None and employee_id not in result and value not in (None, ""):
                        result[employee_id] = {
                            "value": value,
                            "source": "sql_server_read_only.im_formtemployeeinput.double_wagesfixed",
                        }
            return result
        except SourceDataError:
            raise
        except Exception as exc:
            raise SourceDataError(f"Read-only HRMS Basic Wage lookup failed: {exc}") from exc


class EmanageApi:
    """Read-only client for the source API controllers found in the supplied ZIP."""

    def __init__(self):
        self.base_url = os.getenv("EMANAGE_API_BASE_URL", "").rstrip("/")
        self.timeout = max(3, int(os.getenv("EMANAGE_API_TIMEOUT_SECONDS", "20")))
        self.token = os.getenv("EMANAGE_API_TOKEN", "")
        self.api_key = os.getenv("EMANAGE_API_KEY", "")
        self.user_id = os.getenv("EMANAGE_API_USER_ID", "")
        self.context_company_id = os.getenv("EMANAGE_CONTEXT_COMPANY_ID", "")
        self.context_company_code = os.getenv("EMANAGE_CONTEXT_COMPANY_CODE", "")

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _request(self, method: str, route: str, *, query: dict[str, Any] | None = None, body: dict[str, Any] | None = None) -> Any:
        if not self.configured:
            raise SourceDataError("EMANAGE_API_BASE_URL is not configured.")
        url = f"{self.base_url}/{route.lstrip('/')}"
        if query:
            url += "?" + urlencode({key: value for key, value in query.items() if value not in (None, "")})
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        try:
            with urlopen(Request(url, data=data, headers=headers, method=method), timeout=self.timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise SourceDataError(f"Source API request failed for {route}: {exc}") from exc

    def companies(self) -> list[dict[str, Any]]:
        payload = self._request(
            "GET", "/Company/GetCompanyList",
            query={"UserID": self.user_id, "_companyID": self.context_company_id, "_companycode": self.context_company_code},
        )
        return _envelope_items(payload)

    def deputed_companies(self, company_id: int) -> list[dict[str, Any]]:
        payload = self._request(
            "GET", "/Company/GetDeputedCompanyList",
            query={"CompanyID": company_id, "UserID": self.user_id, "_companyID": self.context_company_id, "_companycode": self.context_company_code},
        )
        return _envelope_items(payload)

    def client_id(self, company_id: int, company_code: str = "") -> int | None:
        payload = self._request(
            "GET", "/Company/GetClientID",
            query={"CompanyID": company_id, "CompanyCode": company_code},
        )
        rows = _envelope_items(payload)
        values: list[Any] = []
        for row in rows:
            values.extend(_first(row, "ClientId", "client_id", "int_cid", "CompanyID", "company_id", default=None) for _ in [0])
        for value in values:
            parsed = _as_int(value)
            if parsed:
                return parsed
        return None

    def employee_pf_master(self, client_id: int, employee_code: str = "") -> list[dict[str, Any]]:
        body: dict[str, Any] = {"ClientId": client_id}
        if employee_code:
            body["EmployeeCode"] = employee_code
        if self.user_id:
            body["APIUserId"] = self.user_id
        payload = self._request("POST", "/PFAutomation/GetEmployeePFMaster", body=body)
        return _envelope_items(payload)

    def employee_esi_details(
        self,
        company_id: int,
        deputed_company_id: int | None = None,
        *,
        client_id: int | None = None,
        employee_code: str = "",
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Read the ESI employee contract from the configured source endpoint.

        The supplied ESIC archive documents the payload contract but does not
        contain the Java source or a definitive controller route.  Requiring a
        configured route prevents this ESI path from silently reusing the PF
        master endpoint or mixing statutory payloads.
        """
        route = os.getenv("EMANAGE_ESI_EMPLOYEE_ROUTE", "").strip()
        if not route:
            raise SourceDataError(
                "EMANAGE_ESI_EMPLOYEE_ROUTE is not configured; ESI source synchronization is disabled until the ESI employee endpoint is supplied."
            )
        method = os.getenv("EMANAGE_ESI_EMPLOYEE_METHOD", "POST").strip().upper() or "POST"
        if method not in {"GET", "POST"}:
            raise SourceDataError("EMANAGE_ESI_EMPLOYEE_METHOD must be GET or POST.")
        body = {
            "CompanyId": int(company_id),
            "DeputedCompanyId": int(deputed_company_id or 0),
            "ClientId": int(client_id or company_id),
            "EmployeeCode": employee_code,
            "Limit": min(max(int(limit or 500), 1), 5000),
            "Offset": max(int(offset or 0), 0),
        }
        if self.user_id:
            body["APIUserId"] = self.user_id
        query = {
            "CompanyID": int(company_id),
            "DeputedCompanyID": int(deputed_company_id or 0),
            "ClientId": int(client_id or company_id),
            "EmployeeCode": employee_code,
            "Limit": body["Limit"],
            "Offset": body["Offset"],
            "ApiUserid": self.user_id,
        }
        payload = self._request(method, route, query=query if method == "GET" else None, body=body if method == "POST" else None)
        return _envelope_items(payload)

    def employee_basic_wages(
        self,
        company_id: int,
        company_code: str,
        employee_ids: list[int],
        deputed_company_id: int | None = None,
    ) -> dict[int, dict[str, Any]]:
        """Read the HRMS salary-screen Basic component through the source API.

        ``GetSelectedEmployeeSalary`` accepts one employee per request and
        returns the salary component tree.  Only the component named Basic is
        extracted; no salary credentials or raw response is returned to the UI.
        """
        result: dict[int, dict[str, Any]] = {}

        def find_basic(value: Any) -> Any:
            if isinstance(value, dict):
                component = _first(value, "component", "Component", "component_name", "ComponentName")
                amount = _first(value, "amount", "Amount", "basic", "Basic")
                if _text(component).lower() in {"basic", "basic wage"} and amount not in (None, ""):
                    return amount
                for child in value.values():
                    found = find_basic(child)
                    if found not in (None, ""):
                        return found
            elif isinstance(value, list):
                for child in value:
                    found = find_basic(child)
                    if found not in (None, ""):
                        return found
            return None

        for employee_id in employee_ids:
            body = [{
                "companyid": str(company_id),
                "companycode": company_code,
                "deputedcompanyid": str(deputed_company_id or 0),
                "financialyearid": "0",
                "financialyear": "0",
                "monthid": "0",
                "transactiontype": "",
                "userid": 0,
                "employeeid": str(employee_id),
            }]
            payload = self._request(
                "POST",
                "/Payroll/GetSelectedEmployeeSalary",
                query={
                    "ApiUserid": self.user_id,
                    "_companyID": str(company_id),
                    "_companycode": company_code,
                },
                body=body,
            )
            value = find_basic(payload)
            if value not in (None, ""):
                result[employee_id] = {
                    "value": _jsonable(value),
                    "source": "emanage_api.Payroll/GetSelectedEmployeeSalary:Basic",
                }
        return result


class CompleteEmployeeDataService:
    """Merge all configured sources into one stable, field-level-traceable contract."""

    def __init__(self, database: ReadOnlyDatabase | None = None, api: EmanageApi | None = None):
        self.database = database or ReadOnlyDatabase()
        self.api = api or EmanageApi()

    def status(self) -> dict[str, Any]:
        return {
            "read_only": True,
            "database_configured": self.database.configured,
            "api_configured": self.api.configured,
            "available_sources": [
                source for source, enabled in (("emanage_api", self.api.configured), ("sql_server_read_only", self.database.configured)) if enabled
            ],
        }

    def companies(self) -> dict[str, Any]:
        warnings: list[str] = []
        # The supplied Emanage portal uses the same SQL company master that
        # scopes employee data. Prefer it so the selector and queue cannot
        # disagree; the HTTP API remains a fallback for deployments without
        # direct read-only database access.
        if self.database.configured:
            try:
                items = self.database.companies()
                return {"companies": items, "source": "sql_server_read_only.dbo.im_companydetails", "read_only": True, "warnings": warnings}
            except SourceDataError as exc:
                warnings.append(str(exc))
        if self.api.configured:
            try:
                items = self.api.companies()
                return {"companies": items, "source": "emanage_api.Company/GetCompanyList", "read_only": True, "warnings": warnings}
            except SourceDataError as exc:
                warnings.append(str(exc))
        raise SourceDataError("No company source is configured.")

    def deputed_companies(self, company_id: int) -> dict[str, Any]:
        warnings: list[str] = []
        if self.database.configured:
            try:
                items = self.database.deputed_companies(company_id)
                return {"company_id": str(company_id), "deputed_companies": items, "source": "sql_server_read_only.dbo.Api_DeputedCompanylist_scope_query", "read_only": True, "warnings": warnings}
            except SourceDataError as exc:
                warnings.append(str(exc))
        if self.api.configured:
            try:
                items = self.api.deputed_companies(company_id)
                return {"company_id": str(company_id), "deputed_companies": items, "source": "emanage_api.Company/GetDeputedCompanyList", "read_only": True, "warnings": warnings}
            except SourceDataError as exc:
                warnings.append(str(exc))
        raise SourceDataError("No deputed-company source is configured.")

    def complete(
        self,
        company_id: int,
        deputed_company_id: int | None = None,
        employee_code: str = "",
        limit: int = 500,
        offset: int = 0,
        updated_since: datetime | None = None,
        joined_since: datetime | None = None,
        recent_joining_only: bool = False,
    ) -> dict[str, Any]:
        limit = min(max(int(limit), 1), 5000)
        offset = max(int(offset), 0)
        warnings: list[str] = []
        # PF display/intake returns the complete source scope. No 48-hour or
        # seven-day joining/created window is applied here.
        joining_cutoff = None
        if recent_joining_only:
            minimum_joining_cutoff = _recent_joining_cutoff()
            requested_joining_cutoff = _as_naive_datetime(joined_since)
            joining_cutoff = max(requested_joining_cutoff or minimum_joining_cutoff, minimum_joining_cutoff)
        company_row = self.database.company(company_id) if self.database.configured else None
        company_code = _text(company_row.get("company_code")) if company_row else ""
        short_name = _text(company_row.get("short_name")) if company_row else ""

        api_rows: list[dict[str, Any]] = []
        if self.api.configured:
            try:
                client_id = self.api.client_id(company_id, company_code) or company_id
                api_rows = self.api.employee_pf_master(client_id, employee_code)
            except SourceDataError as exc:
                warnings.append(str(exc))

        # The PF master API is not guaranteed to enforce the joining-date
        # filter, so apply the same strict boundary before merging it with
        # the read-only employee scope.
        if joining_cutoff is not None:
            api_rows = [
                item for item in api_rows
                if (_as_naive_datetime(_first(item, "DOJ", "DateOfJoining", "JoiningDate", "doj")) or datetime.min) >= joining_cutoff
            ]

        db_rows: list[dict[str, Any]] = []
        if self.database.configured:
            db_rows = self.database.employees(company_id, deputed_company_id, employee_code, limit, offset, updated_since, joining_cutoff)
        if not api_rows and not db_rows:
            raise SourceDataError("No employee data was returned by the configured sources.")

        # When the database is available it is the authoritative scope filter.
        # The PF master API does not expose deputed-company scope in its
        # response, so never let an unscoped API response leak into a selected
        # company/deputed-company batch.
        if db_rows:
            db_keys = {
                _text(item.get("employee_code")) or f"id:{item.get('employee_id')}"
                for item in db_rows
            }
            api_rows = [
                item for item in api_rows
                if (_text(_first(item, "EmployeeCode", "employee_code", "EmpNo", "empno"))
                    or f"id:{_first(item, 'EmployeeId', 'employee_id', default='')}") in db_keys
            ]

        by_code: dict[str, dict[str, Any]] = {}
        for item in db_rows:
            code = _text(item.get("employee_code")) or f"id:{item.get('employee_id')}"
            by_code[code] = dict(item)
        for item in api_rows:
            code = _text(_first(item, "EmployeeCode", "employee_code", "EmpNo", "empno"))
            key = code or f"id:{_first(item, 'EmployeeId', 'employee_id', default='')}"
            target = by_code.setdefault(key, {})
            for field, names in {
                "employee_id": ("EmployeeId", "employee_id"), "employee_code": ("EmployeeCode", "employee_code", "EmpNo", "empno"),
                "name": ("EmployeeName", "FullName", "MemberName", "Name", "name"),
                "father_name": ("FatherName", "FatherOrHusbandName", "father_name"),
                "gender": ("Gender", "gender"),
                "dob": ("DOB", "DateOfBirth", "BirthDate", "dob"),
                "doj": ("DOJ", "DateOfJoining", "JoiningDate", "doj"),
                "uan": ("UAN", "UANNo", "UANNumber", "UAN_No", "uan"),
                "aadhaar": ("Aadhaar", "Aadhar", "AadhaarNo", "AadharNo", "AadhaarNumber", "AadharNumber", "aadhaar", "aadhar"),
                "basic_wage": ("BasicWage", "Basic_Wage", "EPFWages", "EPF_Wages", "EpfWages", "Wages", "Basic", "basic_wage"),
                "pf_number": ("PFNumber", "PFNo", "PF_No", "pf_number"),
                "pf_applicable": ("PFApplicability", "PFApplicable", "pf_applicable"),
            }.items():
                value = _first(item, *names)
                if value not in (None, "") and target.get(field) in (None, ""):
                    target[field] = _jsonable(value)
                    target.setdefault("_api_fields", {})[field] = "emanage_api.PFAutomation/GetEmployeePFMaster"

        rows = list(by_code.values())[:limit]
        selected_deputed_id = str(deputed_company_id) if deputed_company_id is not None else ""
        employee_ids = [_as_int(row.get("employee_id")) for row in rows if _as_int(row.get("employee_id"))]
        fixed_basic = self.database.fixed_basic(company_id, [_text(row.get("employee_code")) for row in rows if _text(row.get("employee_code"))]) if self.database.configured else {}
        salary_basic = self.database.salary_card_basic(short_name, employee_ids) if self.database.configured else {}
        hrms_basic: dict[int, dict[str, Any]] = {}
        if self.database.configured and employee_ids:
            try:
                hrms_basic = self.database.hrms_basic_wage(company_id, employee_ids)
            except SourceDataError as exc:
                warnings.append(str(exc))
        api_salary_basic: dict[int, dict[str, Any]] = {}
        if self.api.configured and employee_ids and os.getenv("EMANAGE_SALARY_API_ENABLED", "true").lower() not in {"0", "false", "no"}:
            try:
                api_salary_basic = self.api.employee_basic_wages(company_id, company_code, employee_ids, deputed_company_id)
            except SourceDataError as exc:
                warnings.append(str(exc))
        records: list[dict[str, Any]] = []
        for row_index, row in enumerate(rows, start=1):
            employee_id = _as_int(row.get("employee_id"))
            employee_code_value = _text(row.get("employee_code"))
            api_basic = row.get("basic_wage")
            wage = (
                (api_salary_basic.get(employee_id) if employee_id is not None else None)
                or (hrms_basic.get(employee_id) if employee_id is not None else None)
                or fixed_basic.get(employee_code_value)
                or (salary_basic.get(employee_id) if employee_id is not None else None)
                or ({"value": _jsonable(api_basic), "source": row.get("_api_fields", {}).get("basic_wage", "emanage_api.PFAutomation/GetEmployeePFMaster")} if api_basic not in (None, "") else None)
            )
            raw_fields = {
                "uan": row.get("uan"),
                "aadhaar": row.get("aadhaar"),
                "name": row.get("name"),
                "dob": row.get("dob"),
                "doj": row.get("doj"),
                "basic_wage": wage.get("value") if wage else None,
            }
            fields = {
                field: _canonical_pf_field(field, raw_value)
                for field, raw_value in raw_fields.items()
            }
            sources = {
                "uan": row.get("_api_fields", {}).get("uan", "sql_server_read_only.dbo.im_employeepfdetails"),
                "aadhaar": row.get("_api_fields", {}).get("aadhaar", "sql_server_read_only.dbo.im_employeedetails.varchar_aadhar"),
                "name": row.get("_api_fields", {}).get("name", "sql_server_read_only.dbo.im_employeedetails"),
                "dob": row.get("_api_fields", {}).get("dob", "sql_server_read_only.dbo.im_employeedetails"),
                "doj": row.get("_api_fields", {}).get("doj", "sql_server_read_only.dbo.im_employeedetails"),
                "basic_wage": wage.get("source") if wage else None,
            }
            missing = [field for field, value in fields.items() if value in (None, "")]
            validation_errors = [
                item
                for item in validate_record_all({"row_number": row_index, **fields})
                if item.get("column") in PF_REQUIRED_FIELD_LABELS
            ]
            records.append({
                "employee_id": employee_id,
                "employee_code": employee_code_value,
                "company_id": str(company_id),
                "deputed_company_id": selected_deputed_id or str(row.get("deputed_company_id") or ""),
                "fields": fields,
                "raw_fields": {field: _jsonable(raw_value) for field, raw_value in raw_fields.items()},
                "conversion": {
                    "status": "CONVERTED",
                    "date_format": "DD-MM-YYYY",
                    "validated": not validation_errors,
                    "errors": validation_errors,
                },
                "mandatory_fields": [
                    {
                        "key": field,
                        "label": label,
                        "value": fields.get(field),
                        "present": fields.get(field) not in (None, ""),
                        "source": sources.get(field),
                    }
                    for field, label in PF_REQUIRED_FIELD_LABELS.items()
                ],
                "extra": {key: value for key, value in row.items() if not key.startswith("_") and key not in fields},
                "field_sources": sources,
                "missing_required_fields": missing,
                "ready_for_pf_intake": not missing and not validation_errors,
                "updated_at": row.get("updated_at"),
                "is_current": row.get("is_current"),
                "employee_status": row.get("employee_status"),
            })
        return {
            "company_id": str(company_id),
            "deputed_company_id": selected_deputed_id,
            "required_fields": [
                {"key": field, "label": label, "required": True}
                for field, label in PF_REQUIRED_FIELD_LABELS.items()
            ],
            "records": records,
            "count": len(records),
            "pagination": {"offset": offset, "limit": limit, "has_more": len(records) == limit},
            "read_only": True,
            "sources": {
                "company": "emanage_api.Company/GetCompanyList or sql_server_read_only.dbo.im_companydetails",
                "deputed_company": "emanage_api.Company/GetDeputedCompanyList or sql_server_read_only.dbo.Api_DeputedCompanylist_scope_query",
                "employee_pf": "emanage_api.PFAutomation/GetEmployeePFMaster or sql_server_read_only employee/PF masters",
                "aadhaar": "sql_server_read_only.dbo.im_employeedetails.varchar_aadhar",
                "basic_wage": "emanage_api.Payroll/GetSelectedEmployeeSalary:Basic or sql_server_read_only.im_hrsalarystructure.Basic+im_hrsalarymaster.double_amount, with legacy read-only fallbacks",
            },
            "warnings": warnings,
            "queried_at": _now(),
            "updated_since": updated_since.isoformat() if updated_since else None,
            "joining_window_hours": RECENT_JOINING_WINDOW_HOURS,
            "joined_since": joining_cutoff.isoformat(timespec="seconds"),
        }


SOURCE_DATA = CompleteEmployeeDataService()

