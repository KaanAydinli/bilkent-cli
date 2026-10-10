import threading

import requests
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.theme import Theme

from stars_presentation import FittedTable, weekly_tables
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static, TabbedContent, TabPane

from stars_client import SessionExpired, StarsClient


class Login(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical(id="login-card"):
            yield Label("STARS", id="brand")
            yield Label("Your academic workspace", classes="subtitle")
            yield Label("Bilkent ID")
            yield Input(placeholder="Student ID", id="username")
            yield Label("Password")
            yield Input(placeholder="Password", password=True, id="password")
            yield Button("Sign in", id="sign-in", variant="primary")
            with Vertical(id="otp-box"):
                yield Label("SMS verification", id="otp-label")
                yield Input(placeholder="5-digit SMS code", password=True, id="otp", max_length=8)
                yield Button("Verify", id="verify", variant="primary")
            yield Static("Credentials and session stay in memory only.", id="login-status", markup=False)
            yield Button("Start a fresh login", id="restart")
        yield Footer()

    def on_mount(self):
        self.query_one("#otp-box").display = False
        self.query_one("#restart").display = False
        self.query_one("#username").focus()

    @on(Button.Pressed, "#sign-in")
    @on(Input.Submitted, "#password")
    def sign_in(self):
        if self.app.busy:
            return
        username = self.query_one("#username", Input).value.strip()
        password = self.query_one("#password", Input).value
        if not username or not password:
            self.query_one("#login-status", Static).update("Enter your Bilkent ID and password.")
            return
        self.query_one("#password", Input).value = ""
        self.set_busy("Signing in…")
        self.app.authenticate(username, password)

    @on(Input.Submitted, "#username")
    def next_field(self):
        self.query_one("#password").focus()

    @on(Button.Pressed, "#verify")
    @on(Input.Submitted, "#otp")
    def verify(self):
        if self.app.busy:
            return
        code = self.query_one("#otp", Input).value.strip()
        length = self.app.client.otp_length
        if len(code) != length or not code.isascii() or not code.isdigit():
            self.query_one("#login-status", Static).update(f"Enter the {length}-digit SMS code.")
            return
        self.query_one("#otp", Input).value = ""
        self.set_busy("Verifying SMS code…")
        self.app.authenticate(code=code)

    @on(Button.Pressed, "#restart")
    def restart(self):
        if not self.app.busy:
            self.app.fresh_login()

    def set_busy(self, message):
        self.app.busy = True
        for button in self.query(Button):
            button.disabled = True
        self.query_one("#login-status", Static).update(message)

    def result(self, result=None, error=None):
        self.app.busy = False
        if error:
            self.query_one("#login-status", Static).update(error)
            self.query_one("#restart").display = True
            self.query_one("#restart", Button).disabled = False
            self.query_one("#restart").focus()
        elif result == "otp":
            self.query_one("#otp-box").display = True
            self.query_one("#otp-label", Label).update(f"SMS verification · {self.app.client.otp_length} digits")
            self.query_one("#verify", Button).disabled = False
            self.query_one("#login-status", Static).update("Enter the code sent to your phone. It is submitted once.")
            self.query_one("#otp").focus()
        else:
            self.app.show_dashboard(result)


class Dashboard(Screen):
    BINDINGS = [("1", "tab('courses')", "Courses"), ("2", "tab('weekly')", "Week"),
                ("3", "tab('exams')", "Exams"), ("r", "refresh", "Refresh")]

    def __init__(self, profile):
        super().__init__()
        self.profile = profile
        self.loaded = set()
        self.pending = set()

    def compose(self):
        yield Header()
        with Horizontal(id="workspace"):
            with Vertical(id="sidebar"):
                with VerticalScroll(id="profile-details"):
                    yield Label("ACADEMIC PROFILE", classes="eyebrow")
                    yield Static(Text(self.profile.name, style="bold"), id="student-name")
                    yield Static(Text(self.profile.department), classes="subtitle")
                    yield Label("CGPA", classes="eyebrow")
                    yield Static(Text(self.profile.cgpa, style="bold #9cc7ee"), id="cgpa")
                    for label, value in (("Standing", self.profile.standing), ("Class", self.profile.year),
                                         ("Email", self.profile.email), ("Phone", self.profile.phone)):
                        yield Label(label, classes="eyebrow")
                        yield Static(Text(value or "Not available"), classes="profile-value")
                    yield Static("Session in memory\nRead-only workspace", classes="session-note")
                with Vertical(id="sidebar-actions"):
                    yield Button("Refresh", id="refresh", classes="text-action")
                    yield Button("Logout", id="relogin", classes="text-action")
            with Vertical(id="main-panel"):
                with TabbedContent(initial="courses", id="navigation"):
                    for key, label in (("courses", "Courses"), ("weekly", "Weekly schedule"), ("exams", "Scheduled exams")):
                        with TabPane(label, id=key):
                            with VerticalScroll(id=f"view-{key}"):
                                yield Static("Select this view to load it.", id=f"status-{key}", markup=False)
                yield Static("Tab: focus  •  Arrow keys: navigate  •  Mouse: select and scroll", id="hint")
        yield Footer()

    def on_mount(self):
        self.load("courses")

    def action_tab(self, name):
        self.query_one(TabbedContent).active = name

    @on(TabbedContent.TabActivated)
    def activated(self, event):
        if event.pane.id not in self.loaded:
            self.load(event.pane.id)

    @on(Button.Pressed, "#refresh")
    def refresh_clicked(self):
        self.action_refresh()

    def action_refresh(self):
        self.load(self.query_one(TabbedContent).active, refresh=True)

    @on(Button.Pressed, "#relogin")
    def relogin(self):
        if not self.pending:
            self.app.fresh_login()
        else:
            self.app.notify("Wait for the current request to finish.")

    def load(self, key, refresh=False):
        if key in self.pending or (key in self.loaded and not refresh):
            return
        self.pending.add(key)
        self.query_one(f"#status-{key}", Static).display = True
        self.query_one(f"#status-{key}", Static).update("Loading…")
        self.app.fetch_view(self, key)

    async def show_view(self, key, view=None, error=None, expired=False):
        self.pending.discard(key)
        status = self.query_one(f"#status-{key}", Static)
        container = self.query_one(f"#view-{key}", VerticalScroll)
        if error:

            await container.query("DataTable, .table-title").remove()
            status.display = True
            status.update(error)
            self.loaded.discard(key)
            if expired:
                for table in self.query(DataTable):
                    table.clear()
                self.loaded.clear()
                self.query_one("#relogin").focus()
            return
        await container.query("DataTable, .table-title").remove()
        self.loaded.add(key)
        status.update(view.message or ("" if key == "weekly" else view.title))
        status.display = bool(view.message) or key != "weekly"
        visible_tables = weekly_tables(view.tables) if key == "weekly" else view.tables
        for index, table in enumerate(visible_tables):
            widget = FittedTable(table, key, id=f"table-{key}-{index}")
            if key != "weekly":
                await container.mount(Static(Text(table.title, style="bold"), classes="table-title"))
            await container.mount(widget)
        if self.query_one(TabbedContent).active == key and visible_tables:
            self.query_one(f"#table-{key}-0").focus()


class StarsApp(App):
    TITLE = "STARS  /  Academic workspace"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [("ctrl+q", "quit", "Quit")]
    CSS = """
    Screen { background: #1c1c1f; color: #dedee3; }
    Header, Footer { background: #252528; }
    Login { align: center middle; }
    #login-card { width: 58; height: auto; max-height: 95%; overflow-y: auto; padding: 1 3; border: round #414147; background: #252528; }
    #brand { color: #9cc7ee; text-style: bold; margin-bottom: 1; }
    .subtitle { color: #a5a5ae; margin-bottom: 1; }
    Input { margin-bottom: 1; background: #1c1c1f; }
    Button { min-width: 12; }
    Button:hover { tint: #9cc7ee 12%; }
    #sign-in, #verify, #restart { width: 100%; margin-bottom: 1; }
    #otp-box { height: auto; }
    #login-status { height: auto; margin: 1 0; color: #c3c3cd; }
    #workspace { height: 1fr; padding: 1; }
    #sidebar { width: 30; min-width: 22; padding: 1 2; background: #252528; border: round #414147; margin-right: 1; }
    .eyebrow { color: #a5a5ae; margin-top: 1; }
    #student-name { height: auto; margin-top: 1; }
    #cgpa { height: 3; content-align: left middle; }
    .profile-value { height: auto; margin-bottom: 1; }
    .session-note { height: auto; color: #91919d; margin: 2 0 1 0; }
    #profile-details { height: 1fr; }
    #sidebar-actions { height: 4; }
    .text-action { width: auto; min-width: 0; height: 1; min-height: 1; border: none; background: transparent; padding: 0; margin-top: 1; color: #9cc7ee; text-style: none; content-align: left middle; }
    .text-action:hover, .text-action:focus, .text-action.-active { background: transparent; tint: transparent; text-style: underline; border: none; }
    #view-weekly { scrollbar-size: 0 0; }
    #view-exams { overflow-x: hidden; }
    DataTable > .datatable--even-row { background: #222225; }
    DataTable > .datatable--odd-row { background: #29292d; }
    Tabs { background: #252528; }
    Tab { background: #252528; color: #a5a5ae; }
    Tab.-active { color: #9cc7ee; }
    Underline > .underline--bar { color: #9cc7ee; background: #25364a; }
    #main-panel { width: 1fr; }
    #navigation { height: 1fr; }
    TabPane { padding: 1; }
    TabPane VerticalScroll { height: 1fr; }
    .table-title { height: auto; margin: 1 0; color: #9cc7ee; }
    DataTable { background: #222225; margin-bottom: 1; }
    DataTable > .datatable--header { background: #25364a; color: #d9e8f6; text-style: bold; }
    DataTable > .datatable--cursor { background: #2a4059; color: #ffffff; }
    #hint { height: 1; color: #a5a5ae; text-wrap: nowrap; text-overflow: ellipsis; }
    """

    def __init__(self, client_factory=StarsClient):
        super().__init__()
        self.client_factory = client_factory
        self.client = client_factory()
        self.lock = threading.Lock()
        self.busy = False

    def on_mount(self):
        self.register_theme(Theme(
            name="stars-gray", primary="#9cc7ee", secondary="#25364a", accent="#9cc7ee",
            foreground="#dedee3", background="#1c1c1f", surface="#252528", panel="#29292d",
            dark=True,
        ))
        self.theme = "stars-gray"
        self.push_screen(Login())

    def fresh_login(self):
        self.client.close()
        self.client = self.client_factory()
        self.switch_screen(Login())

    def show_dashboard(self, profile):
        self.switch_screen(Dashboard(profile))

    @work(thread=True, exit_on_error=False)
    def authenticate(self, username="", password="", code=None):
        screen = self.screen
        try:
            with self.lock:
                result = self.client.verify(code) if code is not None else self.client.start(username, password)
            self.call_from_thread(screen.result, result)
        except (RuntimeError, requests.RequestException) as exc:
            message = "Network request failed. Start a fresh login." if isinstance(exc, requests.RequestException) else str(exc)
            self.call_from_thread(screen.result, error=message)
        except Exception:
            self.call_from_thread(screen.result, error="Unexpected response while signing in. Start a fresh login.")
        finally:
            username = password = code = None

    @work(thread=True, exit_on_error=False)
    def fetch_view(self, screen, key):
        try:
            with self.lock:
                view = self.client.view(key)
            self.call_from_thread(screen.show_view, key, view)
        except (RuntimeError, requests.RequestException) as exc:
            message = "Network request failed. Use Refresh to try again." if isinstance(exc, requests.RequestException) else str(exc)
            self.call_from_thread(screen.show_view, key, error=message, expired=isinstance(exc, SessionExpired))

        except Exception:
            self.call_from_thread(screen.show_view, key, error="This response could not be displayed. A redacted response is needed to inspect its structure.")

    def on_unmount(self):
        self.client.close()
