from __future__ import annotations

import html
import os
import secrets
import string
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlparse


def _env(name: str, fallback: str = "") -> str:
    return os.getenv(name, fallback)


class MockEpfoServer:
    """Local-only EPFO-shaped test server; it never contacts EPFO."""

    def __init__(self, scenario: str = "SUCCESS"):
        self.scenario = scenario.upper()
        self.employer_id = _env("MOCK_EPFO_EMPLOYER_ID", "TEST_EMPLOYER")
        self.password = _env("MOCK_EPFO_PASSWORD", "TEST_PASSWORD")
        self.captcha = ""
        self.sessions: dict[str, dict[str, str]] = {}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread: Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}"

    def start(self) -> str:
        self.thread = Thread(target=self.httpd.serve_forever, name="mock-epfo", daemon=True)
        self.thread.start()
        return self.base_url

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=2)

    def _handler(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _format, *_args):
                return

            def _session(self) -> dict[str, str]:
                cookie = self.headers.get("Cookie", "")
                sid = cookie.replace("MOCKSID=", "").split(";", 1)[0]
                if not sid or sid not in server.sessions:
                    sid = secrets.token_urlsafe(16)
                    server.sessions[sid] = {"stage": "login", "captcha": server.new_captcha()}
                    self.new_sid = sid
                else:
                    self.new_sid = sid
                return server.sessions[sid]

            def _new_captcha(self, session):
                session["captcha"] = server.new_captcha()
                return session["captcha"]

            def _send(self, body: str, status=HTTPStatus.OK):
                payload = body.encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                if getattr(self, "new_sid", None):
                    self.send_header("Set-Cookie", f"MOCKSID={self.new_sid}; HttpOnly")
                self.end_headers()
                self.wfile.write(payload)

            def _form(self) -> dict[str, str]:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length).decode("utf-8")
                return {key: values[-1] for key, values in parse_qs(raw).items()}

            def do_GET(self):
                session = self._session()
                path = urlparse(self.path).path
                if path == "/login":
                    # Local test behavior: every login-page load receives a new
                    # CAPTCHA challenge. The real portal is never contacted.
                    self._new_captcha(session)
                    return self._send(server.login_page(captcha_value=session.get("captcha")))
                if path == "/__test__/captcha":
                    return self._send(session.get("captcha", ""))
                if path == "/dashboard" and session.get("stage") == "authenticated":
                    return self._send(server.dashboard_page())
                if path == "/register" and session.get("stage") == "authenticated":
                    return self._send(server.register_page())
                return self._send(server.error_page("Session expired or unexpected mock page."), HTTPStatus.UNAUTHORIZED)

            def do_POST(self):
                session = self._session()
                path = urlparse(self.path).path
                values = self._form()
                if path == "/login":
                    if values.get("username") != server.employer_id or values.get("password") != server.password:
                        self._new_captcha(session)
                        return self._send(server.login_page("Invalid credentials", session.get("captcha")))
                    if server.scenario == "CAPTCHA_INVALID":
                        self._new_captcha(session)
                        return self._send(server.login_page("Invalid CAPTCHA", session.get("captcha")))
                    if values.get("captcha") != session.get("captcha"):
                        self._new_captcha(session)
                        return self._send(server.login_page("Invalid CAPTCHA", session.get("captcha")))
                    if server.scenario == "CAPTCHA_TIMEOUT":
                        self._new_captcha(session)
                        return self._send(server.login_page("CAPTCHA timeout", session.get("captcha")))
                    if server.scenario == "SESSION_EXPIRED":
                        session["stage"] = "login"
                        return self._send(server.error_page("Session expired"), HTTPStatus.UNAUTHORIZED)
                    session["stage"] = "authenticated"
                    return self._send(server.dashboard_page())
                if path == "/verify-member" and session.get("stage") == "authenticated":
                    return self._send(server.member_page("Member identity verified", verified=True))
                if path == "/register" and session.get("stage") == "authenticated":
                    return self._send(server.member_page("Registered - Pending Approval", saved=True))
                return self._send(server.error_page("Unexpected mock workflow state."), HTTPStatus.BAD_REQUEST)

        return Handler

    @staticmethod
    def shell(title: str, content: str) -> str:
        return f"""<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>
        :root {{ --navy:#17365d; --blue:#1261a0; --light:#f3f6fa; --border:#d7dee8; --green:#16835b; --red:#b42318; }}
        * {{ box-sizing:border-box; }} body {{ margin:0; font-family:Segoe UI,Arial,sans-serif; color:#26364a; background:var(--light); font-size:14px; }}
        .govbar {{ height:34px; background:#3d3d3d; color:#fff; display:flex; align-items:center; padding:0 34px; font-size:12px; }} .govbar span {{ margin-left:auto; opacity:.9; }}
        .top {{ height:88px; background:#fff; display:flex; align-items:center; padding:0 34px; border-bottom:1px solid #dfe5ec; }}
        .brand {{ display:flex; align-items:center; gap:12px; color:var(--navy); font-weight:700; font-size:20px; }} .emblem {{ width:42px; height:42px; border:3px solid #d8a928; border-radius:50%; display:grid; place-items:center; color:var(--navy); font-size:12px; }}
        .brand small {{ display:block; font-size:10px; font-weight:500; color:#63748a; letter-spacing:.6px; }} .top-right {{ margin-left:auto; color:#586b80; font-size:13px; }}
        .nav {{ background:var(--navy); color:#fff; height:43px; display:flex; align-items:center; padding:0 34px; gap:28px; font-size:13px; }} .nav span:last-child {{ margin-left:auto; opacity:.8; }}
        .layout {{ display:flex; min-height:calc(100vh - 115px); }} .side {{ width:226px; background:#fff; border-right:1px solid var(--border); padding:22px 12px; }} .side h4 {{ margin:0 12px 14px; text-transform:uppercase; letter-spacing:.8px; color:#8a98a8; font-size:11px; }} .side a {{ display:block; padding:12px 14px; color:#44566c; text-decoration:none; border-radius:4px; margin:3px 0; }} .side a.active {{ background:#eaf2fa; color:var(--blue); font-weight:600; border-left:3px solid var(--blue); }}
        .content {{ flex:1; padding:28px 36px 48px; max-width:1250px; }} .crumb {{ color:#728299; font-size:12px; margin-bottom:12px; }} h1 {{ margin:0 0 6px; font-size:25px; color:var(--navy); font-weight:600; }} h2 {{ font-size:17px; color:var(--navy); margin:0; }} .sub {{ color:#6b7d91; margin:0 0 22px; }}
        .card {{ background:#fff; border:1px solid var(--border); border-radius:5px; box-shadow:0 2px 7px rgba(26,55,86,.06); padding:23px; margin:16px 0; }} .card-head {{ display:flex; align-items:center; justify-content:space-between; border-bottom:1px solid #e5eaf0; padding-bottom:14px; margin-bottom:18px; }} .badge {{ background:#eef6ff; color:#1261a0; border-radius:14px; padding:6px 12px; font-size:12px; font-weight:600; }} .success {{ color:var(--green); }} .error {{ color:var(--red); background:#fff1f0; border:1px solid #f4c7c3; border-radius:4px; padding:10px 12px; }}
        label {{ display:block; color:#52657a; font-size:12px; font-weight:600; margin-bottom:6px; }} input, select {{ width:100%; border:1px solid #bfcbd8; border-radius:4px; padding:10px 11px; font-size:14px; color:#26364a; background:#fff; }} input:focus {{ outline:2px solid #c7e0f5; border-color:var(--blue); }} .grid {{ display:grid; grid-template-columns:repeat(2,minmax(220px,1fr)); gap:18px 22px; }} .grid.three {{ grid-template-columns:repeat(3,minmax(170px,1fr)); }} .wide {{ grid-column:1/-1; }} .actions {{ display:flex; gap:12px; align-items:center; margin-top:23px; }} button {{ border:0; border-radius:4px; background:var(--blue); color:#fff; padding:11px 22px; font-weight:600; cursor:pointer; }} button.secondary {{ background:#edf2f7; color:#3f536a; }} .hint {{ color:#74859a; font-size:12px; margin-top:8px; }}
        .captcha-box {{ display:flex; align-items:center; gap:12px; margin-top:8px; }} .captcha-image {{ width:145px; height:43px; background:repeating-linear-gradient(12deg,#e5eef7,#e5eef7 4px,#c8d8e8 5px,#eef5fb 9px); border:1px solid #b5c6d8; display:grid; place-items:center; color:#17365d; font:italic 700 17px Georgia; letter-spacing:3px; }}
        .portal-grid {{ display:grid; grid-template-columns:1.7fr .9fr .9fr; gap:24px; align-items:stretch; }} .portal-panel {{ background:#fff; border:1px solid #d7dee8; box-shadow:0 2px 6px rgba(26,55,86,.07); min-height:360px; }} .portal-panel .panel-title {{ color:#fff; padding:13px 16px; font-size:17px; font-weight:700; }} .portal-panel.orange .panel-title {{ background:#f58218; }} .portal-panel.red .panel-title {{ background:#e94b5b; }} .portal-panel.cyan .panel-title {{ background:#16b3c2; }} .portal-panel .panel-body {{ padding:18px; }} .portal-panel ul {{ margin:0; padding-left:21px; line-height:1.75; color:#465970; }} .portal-panel li {{ margin-bottom:11px; }} .news {{ color:#b21f2d; font-weight:600; }} .mini-link {{ color:#1261a0; text-decoration:none; font-weight:600; }}
        .stepper {{ display:flex; justify-content:center; gap:0; margin:8px 0 25px; }} .step {{ padding:9px 21px; border-bottom:3px solid #d8e0e9; color:#8795a5; font-size:12px; }} .step.done {{ border-color:var(--green); color:var(--green); }} .step.current {{ border-color:var(--blue); color:var(--blue); }}
        .kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:15px; }} .kpi {{ background:#fff; border:1px solid var(--border); border-radius:5px; padding:17px; }} .kpi strong {{ display:block; font-size:25px; color:var(--navy); margin-top:5px; }} .kpi small {{ color:#78899c; }} .info-table {{ width:100%; border-collapse:collapse; }} .info-table th,.info-table td {{ text-align:left; padding:10px 9px; border-bottom:1px solid #e6ebf1; }} .info-table th {{ color:#6f8094; font-size:12px; font-weight:600; }}
        footer {{ text-align:center; color:#8a98a8; font-size:11px; padding:18px; }} @media(max-width:1100px) {{ .portal-grid {{ grid-template-columns:1fr 1fr; }} .portal-panel.orange {{ grid-column:1/-1; min-height:auto; }} .content {{ padding:24px; }} }} @media(max-width:850px) {{ .govbar {{ height:auto; min-height:34px; padding:8px 16px; }} .govbar span {{ display:none; }} .top {{ height:auto; min-height:72px; padding:12px 16px; }} .brand {{ font-size:16px; }} .top-right {{ display:none; }} .nav {{ height:auto; min-height:42px; padding:10px 16px; gap:14px; flex-wrap:wrap; }} .nav span:nth-child(n+4) {{ display:none; }} .side {{ display:none; }} .content {{ padding:18px 14px 36px; }} .portal-grid,.grid,.grid.three,.kpis {{ grid-template-columns:1fr; }} .portal-panel.orange {{ grid-column:auto; }} .card {{ padding:16px; }} h1 {{ font-size:21px; }} .actions {{ flex-wrap:wrap; }} button {{ max-width:100%; }} }}
        </style></head><body><div class='govbar'>Government of India<span>Screen Reader Access &nbsp; Technical Help: 18001-18005 &nbsp; Contact Us &nbsp; A+ &nbsp; A &nbsp; A-</span></div><header class='top'><div class='brand'><div class='emblem'>EPFO</div><div><span style='color:#9e2332'>Employees' Provident Fund Organisation, India</span><small>Ministry of Labour & Employment, Government of India</small></div></div><div class='top-right'>G20 &nbsp; | &nbsp; MOCK PORTAL</div></header><nav class='nav'><span>Home</span><span>About Us</span><span>Employers</span><span>Employees</span><span>e-Nomination</span><span>Contact Us</span><span>MOCK · 127.0.0.1</span></nav><div class='layout'><aside class='side'><h4>Employer Services</h4><a class='active'>Member Management</a><a>Individual Member Registration</a><a>Member List</a><a>UAN Services</a><a>Returns</a><a>Payment</a><h4 style='margin-top:30px'>Support</h4><a>Notifications</a><a>Contact Helpdesk</a></aside><main class='content'>{content}</main></div><footer>Local test environment · No real EPFO data or authentication is used · Automation stops at Registered - Pending Approval</footer></body></html>"""

    @staticmethod
    def new_captcha() -> str:
        alphabet = string.ascii_uppercase + string.digits
        return "".join(secrets.choice(alphabet) for _ in range(6))

    def login_page(self, error="", captcha_value="") -> str:
        error_html = f"<p class='error'>{html.escape(error)}</p>" if error else ""
        content = f"<div class='crumb'>Home / Employer Sign In</div><div class='portal-grid'><section class='portal-panel orange'><div class='panel-title'>Welcome Employers !!</div><div class='panel-body'><ul><li class='news'>For all news and updates on EPF, please refer to official EPFO communications.</li><li>No last date is declared by EPFO for filing nomination.</li><li>Review establishment notifications and member-registration instructions before processing.</li><li>Use the approved employee contract and verify details before submission.</li></ul><p class='hint'>This local page reproduces the video’s employer home layout for testing only.</p></div></section><section class='portal-panel red'><div class='panel-title'>Instructions</div><div class='panel-body'><ul><li>Please create and use a permanent login ID and password after first login.</li><li>Use Forgot Password or Unlock Account if required.</li><li>Complete the CAPTCHA checkpoint shown in the sign-in panel.</li><li>After CAPTCHA, this local mock goes directly to the dashboard.</li></ul></div></section><section class='portal-panel cyan'><div class='panel-title'>Establishment Sign In</div><div class='panel-body'><div style='text-align:center;font-size:42px;color:#777;margin:8px 0 16px'>🏛</div>{error_html}<form method='post' action='/login'><label>Employer Login ID</label><input name='username' autocomplete='username' placeholder='Enter employer ID'><label style='margin-top:13px'>Password</label><input name='password' type='password' autocomplete='current-password' placeholder='Enter password'><label id='captcha-label' style='margin-top:13px'>Enter Captcha</label><div class='captcha-box' style='display:block'><div class='captcha-image' style='width:100%;margin-bottom:9px'>{html.escape(captcha_value)}</div><input name='captcha' placeholder='Enter Captcha'></div><div class='hint'>A new CAPTCHA is generated on every login.</div><div class='actions' style='justify-content:center'><button type='submit' style='background:#239b59'>Sign In ✓</button><button type='reset' class='secondary'>Reset ↻</button></div></form><div style='text-align:center;margin-top:7px'><a class='mini-link'>Forgot Password</a> &nbsp;|&nbsp; <a class='mini-link'>Unlock Account</a></div><p class='hint' style='text-align:center'>Employer Sign In<br>Uncovered Principal Employer Sign In</p></div></section></div><div class='card' style='margin-top:24px'><div class='card-head'><h2>What's New</h2><span class='badge'>Information</span></div><p class='hint'>EPFO services and member registration are available through the employer portal. This demonstration uses dummy records and a localhost-only authentication service.</p></div>"
        return self.shell("Employer Login", content)

    def dashboard_page(self) -> str:
        content = "<div class='crumb'>Home / Dashboard</div><h1>Employer Dashboard</h1><p class='sub'>Welcome to the employer services dashboard.</p><div class='kpis'><div class='kpi'><small>Establishment status</small><strong class='success'>Active</strong></div><div class='kpi'><small>Registered members</small><strong>248</strong></div><div class='kpi'><small>Pending approvals</small><strong>5</strong></div><div class='kpi'><small>Last login</small><strong style='font-size:17px'>05 Oct 2026</strong></div></div><div class='card'><div class='card-head'><h2>Member services</h2><span class='badge'>Authenticated</span></div><p>Use the services below to manage establishment member records.</p><div class='actions'><a href='/register'><button type='button'>Individual Member Registration</button></a><button type='button' class='secondary'>View Member List</button><button type='button' class='secondary'>Generate Reports</button></div></div>"
        return self.shell("Employer Dashboard", content)

    def register_page(self) -> str:
        content = "<div class='crumb'>Home / Member Management / Individual Member Registration</div><h1>Individual Member Registration</h1><p class='sub'>Register a member with an existing Universal Account Number.</p><div class='card'><div class='card-head'><h2>Member identity verification</h2><span class='badge'>Step 1 of 3</span></div><form method='post' action='/verify-member'><div class='grid'><div class='wide'><label>Does the member have a previous UAN?</label><label style='font-weight:400'><input type='radio' name='previous' value='Y' style='width:auto;margin-right:8px'> Yes, member has a previous UAN</label></div><div><label>Universal Account Number (UAN)</label><input name='uan' maxlength='12' placeholder='12-digit UAN'></div><div><label>Aadhaar Number</label><input name='aadhaar' maxlength='12' placeholder='12-digit Aadhaar'></div><div class='wide'><label><input type='checkbox' name='consent' style='width:auto;margin-right:8px'> I confirm that the member identity details are being verified for registration.</label></div></div><div class='actions'><button type='submit'>Verify Member Identity</button><button type='button' class='secondary'>Clear</button></div></form></div><div class='card'><h2>Process guidance</h2><p class='hint'>Enter UAN and Aadhaar, verify the returned member identity, then complete date of joining and wage details. The workflow stops at Registered - Pending Approval.</p></div>"
        return self.shell("Individual Member Registration", content)

    def member_page(self, message: str, verified=False, saved=False) -> str:
        if verified:
            content = f"<div class='crumb'>Home / Member Management / Individual Member Registration</div><h1>Individual Member Registration</h1><p class='sub'>Complete the member and employment details.</p><div class='card'><div class='card-head'><h2>Identity verification result</h2><span class='badge success'>Verified</span></div><p class='success'><strong>✓ {html.escape(message)}</strong></p><table class='info-table'><tr><th>Member name</th><td>Test Employee</td><th>Member status</th><td>Identity verified</td></tr><tr><th>UAN</th><td>XXXXXXXX0001</td><th>Aadhaar</th><td>XXXXXXXX0052</td></tr></table><div class='actions'><button type='button'>OK</button></div></div><div class='card'><div class='card-head'><h2>Employment details</h2><span class='badge'>Step 2 of 3</span></div><form method='post' action='/register'><div class='grid three'><div><label>Employee Code</label><input name='emp_code' value='TEST001' readonly></div><div><label>Company</label><input name='company' value='Mock Consulting Ltd' readonly></div><div><label>Employee Name</label><input name='employee_name' value='Test Employee' readonly></div><div><label>Date of Birth</label><input name='dob' value='19/09/1997' readonly></div><div><label>Gender</label><input name='gender' value='Male' readonly></div><div><label>Marital Status</label><input name='marital_status' value='Un-Married' readonly></div><div><label>Father Name</label><input name='father_name' value='Test Father'></div><div><label>Mobile</label><input name='mobile' value='9000000001'></div><div><label>Location</label><input name='location' value='Bengaluru' readonly></div><div><label>Date of Joining (DOJ)</label><input name='joining_date' placeholder='DD/MM/YYYY'></div><div><label>Basic Wage</label><input name='wage' placeholder='Monthly EPF wage'></div><div><label>Husband/Wife Name</label><input name='spouse_name' value='N/A' readonly></div></div><div class='actions'><button type='submit'>Save Member Registration</button><button type='button' class='secondary'>Cancel</button></div></form></div>"
            return self.shell("Individual Member Registration", content)
        if saved:
            content = f"<div class='crumb'>Home / Member Management / Registration Result</div><h1>Registration Result</h1><div class='card'><div class='card-head'><h2>Member registration completed</h2><span class='badge success'>Completed</span></div><div class='success' style='font-size:18px;padding:18px 0'><strong>✓ {html.escape(message)}</strong></div><table class='info-table'><tr><th>Registration status</th><td>Registered - Pending Approval</td></tr><tr><th>Next action</th><td>Final approval must be completed manually by an authorized user.</td></tr></table><div class='actions'><button>OK</button><a href='/register'><button type='button' class='secondary'>Register Another Member</button></a></div></div>"
            return self.shell("Registration Result", content)
        return self.shell("Individual Member Registration", f"<p>{html.escape(message)}</p><button>OK</button>")

    def error_page(self, message: str) -> str:
        return self.shell("Mock EPFO Error", f"<p>{html.escape(message)}</p>")
