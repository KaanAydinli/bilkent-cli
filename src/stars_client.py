from dataclasses import dataclass
import re
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from stars_cli import START_URL, form_fields, form_kind, get_page, parse_profile, submit, user_field


class SessionExpired(RuntimeError):
    pass


def text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


@dataclass
class Profile:
    name: str
    cgpa: str
    department: str = ""
    standing: str = ""
    year: str = ""
    email: str = ""
    phone: str = ""


@dataclass
class Table:
    title: str
    columns: list[str]
    rows: list[list[str]]


@dataclass
class View:
    title: str
    tables: list[Table]
    message: str = ""


def parse_student(html):
    name, cgpa = parse_profile(html)
    soup = BeautifulSoup(html, "html.parser")
    status = next(f for f in soup.select("fieldset") if f.legend and text(f.legend) == "Academic Status")
    identity = status.select_one("#logoutButton").find_parent("table")
    rows = identity.find_all("tr")
    labels = {}
    for row in status.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) == 2 and not any(c.find("table") for c in cells):
            labels[text(cells[0])] = text(cells[1])
    flat = text(status)
    email = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", flat)
    phone = re.search(r"Mobile Phone:\s*([+\d][\d ()-]{7,})", flat)
    return Profile(name, str(cgpa), text(rows[2]) if len(rows) > 2 else "",
                   labels.get("Standing", ""), labels.get("Class", ""),
                   email.group() if email else "", phone.group(1).strip() if phone else "")


def table_grid(table):

    grid = {}
    header_rows = set()
    rows = [r for r in table.find_all("tr") if r.find_parent("table") is table]
    if len(rows) > 500:
        raise RuntimeError("The returned table is too large to display safely.")
    for y, row in enumerate(rows):
        cells = row.find_all(["td", "th"], recursive=False)
        if row.find_parent("thead") or (cells and all(c.name == "th" for c in cells)):
            header_rows.add(y)
        x = 0
        for cell in cells:
            while (y, x) in grid:
                x += 1
            try:
                height = min(max(int(cell.get("rowspan", 1)), 1), 100)
                width = min(max(int(cell.get("colspan", 1)), 1), 30)
            except ValueError:
                height = width = 1
            if x + width > 60:
                raise RuntimeError("The returned table has too many columns.")
            value = text(cell)
            for dy in range(height):
                for dx in range(width):
                    grid[y + dy, x + dx] = value
            x += width
    width = max((x + 1 for y, x in grid), default=0)
    data = [[grid.get((y, x), "") for x in range(width)] for y in range(len(rows))]
    if not data or not width:
        return [], []
    if not header_rows:
        known_headers = {"time", "day", "date", "course", "course code", "exam", "assessment", "monday", "type"}
        if any(value.casefold() in known_headers for value in data[0]):
            header_rows = {0}
    columns = []
    for x in range(width):
        parts = list(dict.fromkeys(data[y][x] for y in sorted(header_rows) if data[y][x]))
        columns.append(" / ".join(parts) or f"Column {x + 1}")
    return columns, [r for y, r in enumerate(data) if y not in header_rows and any(r)]


def parse_view(html, kind):
    soup = BeautifulSoup(html, "html.parser")
    titles = {"courses": "Current courses", "weekly": "Weekly schedule", "exams": "Scheduled exams"}
    title = titles[kind]
    if kind == "courses":
        table = soup.select_one("#coursesHome")
        candidates = [table] if table else []
    else:
        root = soup.select_one("main, #content, .content-wrapper") or soup
        candidates = [t for t in root.find_all("table") if not t.find("table")]
    tables = []
    for table in candidates:
        for unwanted in table.select("script, style, button, input"):
            unwanted.decompose()
        columns, rows = table_grid(table)
        if columns:
            heading = table.find_previous(["h1", "h2", "h3", "h4"])
            caption = text(table.caption) or text(heading) or title
            tables.append(Table(caption, columns, rows))
    semester = soup.select_one(".home-semester b")
    if semester:
        title = f"Current courses · {text(semester)}"
    if tables:
        return View(title, tables)
    for node in soup.select("script, style, nav, header, footer, form"):
        node.decompose()
    visible = text(soup)
    empty = re.search(r"\bno (?:scheduled )?(?:exams|assessments|courses|events)(?:\s+(?:found|scheduled|available))?\b", visible, re.I)
    if empty:
        return View(title, [], empty.group(0))
    return View(title, [], "This page did not contain a recognizable table. It may load its data with JavaScript. A redacted response from this view is needed to finish its parser.")


class StarsClient:
    PATHS = {"courses": "ajax/home.php", "weekly": "/srs-v2/schedule/index/weekly", "exams": "ajax/exam/index.php"}

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0 STARS-CLI/0.2"})
        self.response = None
        self.ready = False
        self.otp_length = 5
        self.otp_sent = False
        self.credentials_sent = False
        self.username_sent = False

    def close(self):
        self.ready = False
        self.session.cookies.clear()
        self.session.close()

    def start(self, username, password):
        self.response = get_page(self.session, START_URL)
        return self._advance(username, password)

    def verify(self, code):
        if self.otp_sent:
            raise RuntimeError("SMS code was already submitted. Start a fresh login.")
        if not re.fullmatch(rf"[0-9]{{{self.otp_length}}}", code):
            raise RuntimeError(f"Enter the {self.otp_length}-digit SMS code.")
        kind, form = form_kind(BeautifulSoup(self.response.text, "html.parser"))
        if kind != "otp":
            raise RuntimeError("The SMS form is no longer available. Start a fresh login.")
        fields = form_fields(form)
        fields["code"] = code
        self.otp_sent = True
        self.response = submit(self.session, self.response, form, fields)
        return self._advance("", "")

    def _advance(self, username, password):
        for _ in range(6):
            soup = BeautifulSoup(self.response.text, "html.parser")
            kind, form = form_kind(soup)
            if kind == "otp":
                if self.otp_sent:
                    raise RuntimeError("The SMS code was rejected or expired. Start a fresh login.")
                self.otp_length = len(form.select("[data-pin-input] input[type='text']")) or 5
                return "otp"
            if kind in ("username", "password"):
                if self.credentials_sent or self.otp_sent or (kind == "username" and self.username_sent):
                    raise RuntimeError("Authentication was rejected. Start a fresh login.")
                fields = form_fields(form)
                field = user_field(form)
                if field:
                    fields[field["name"]] = username
                if kind == "password":
                    fields[form.select_one('input[type="password"]')["name"]] = password
                    self.credentials_sent = True
                else:
                    self.username_sent = True
                self.response = submit(self.session, self.response, form, fields)
                continue
            parsed = urlparse(self.response.url)
            if parsed.hostname == "stars.bilkent.edu.tr" and parsed.path.startswith("/srs/") and "keycloak" not in parsed.path:

                profile = self.profile()
                self.ready = True
                return profile
            raise RuntimeError("Unexpected login response. Please start a fresh login.")
        raise RuntimeError("Too many authentication steps. Please start a fresh login.")

    def read(self, path):
        response = get_page(self.session, urljoin(START_URL, path))
        soup = BeautifulSoup(response.text, "html.parser")
        if urlparse(response.url).hostname != "stars.bilkent.edu.tr" or form_kind(soup)[1] or response.text.lstrip().startswith("Logout"):
            self.ready = False
            raise SessionExpired("Your session expired. Use Sign in to start again.")
        mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower().strip()
        if mime not in ("text/html", "application/xhtml+xml", "text/plain"):
            raise RuntimeError("The service returned an unsupported response format.")
        return response.text

    def profile(self):
        return parse_student(self.read("ajax/userInfo.php"))

    def view(self, kind):
        if not self.ready:
            raise SessionExpired("Please sign in first.")
        return parse_view(self.read(self.PATHS[kind]), kind)
