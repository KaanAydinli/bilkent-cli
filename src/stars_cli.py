from decimal import Decimal, InvalidOperation
from getpass import getpass
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

START_URL = "https://stars.bilkent.edu.tr/srs/"
ALLOWED_HOSTS = {"signin.bilkent.edu.tr", "stars.bilkent.edu.tr"}
TIMEOUT = 25


def safe_url(url):
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS
            or parsed.username or parsed.password or parsed.port not in (None, 443)):
        raise RuntimeError("Beklenmeyen adres (istek durduruldu).")
    return url


def safe_request(session, method, url, **kwargs):
    for _ in range(10):
        safe_url(url)
        response = session.request(method, url, timeout=TIMEOUT, allow_redirects=False, **kwargs)
        response.raise_for_status()
        if response.status_code not in (301, 302, 303, 307, 308):
            return response
        target = safe_url(urljoin(url, response.headers.get("Location", "")))
        if not response.headers.get("Location"):
            raise RuntimeError("Redirect destination is missing.")
        if response.status_code == 303 or (response.status_code in (301, 302) and method == "POST"):
            method = "GET"
            kwargs.pop("data", None)
        elif method == "POST" and urlparse(target).hostname != urlparse(url).hostname:
            raise RuntimeError("Cross-origin form forwarding was blocked.")
        kwargs.pop("headers", None)
        url = target
    raise RuntimeError("Too many redirects.")


def get_page(session, url):
    return safe_request(session, "GET", url)


def form_fields(form):

    data = {}
    for field in form.select("input[name]"):
        if field.has_attr("disabled"):
            continue
        kind = field.get("type", "text").lower()
        if kind in ("button", "submit", "reset", "file", "image"):
            continue
        if kind in ("checkbox", "radio") and not field.has_attr("checked"):
            continue
        data[field["name"]] = field.get("value", "")
    return data


def submit(session, response, form, fields):
    action = safe_url(urljoin(response.url, form.get("action") or response.url))
    if form.get("method", "get").lower() != "post":
        raise RuntimeError("Beklenmeyen form yöntemi: POST değil.")

    headers = {"Referer": response.url, "Origin": f"{urlparse(response.url).scheme}://{urlparse(response.url).netloc}"}
    next_response = safe_request(session, "POST", action, data=fields, headers=headers)
    next_response.raise_for_status()
    safe_url(next_response.url)
    return next_response


def form_kind(soup):
    forms = soup.find_all("form")
    for form in forms:
        if form.select_one('input[name="code"]') and (form.select_one("[data-pin-input]") or form.select_one('input[autocomplete="one-time-code"]')):
            return "otp", form
    for form in forms:
        if form.select_one('input[type="password"]'):
            return "password", form
    for form in forms:
        if form.select_one('input[name="username"], input[name="email"], input[name="identifier"]'):
            return "username", form
    return None, None


def user_field(form):
    return form.select_one('input[name="username"], input[name="email"], input[name="identifier"], input[autocomplete="username"]')


def logged_in(response):
    parsed = urlparse(response.url)
    return parsed.hostname == "stars.bilkent.edu.tr" and parsed.path.startswith("/srs") and "keycloak" not in parsed.path


def parse_profile(html):
    soup = BeautifulSoup(html, "html.parser")
    status = next(
        (fieldset for fieldset in soup.select("fieldset")
         if fieldset.legend and fieldset.legend.get_text(" ", strip=True) == "Academic Status"),
        None,
    )
    if status is None:
        raise RuntimeError("Academic Status was not found in the profile response.")

    logout = status.select_one("#logoutButton")
    identity = logout.find_parent("table") if logout else None
    rows = identity.find_all("tr") if identity else []
    name_parts = [" ".join(row.get_text(" ", strip=True).split()) for row in rows[:2]]
    if len(name_parts) != 2 or not all(name_parts):
        raise RuntimeError("Student name was not found in Academic Status.")

    for row in status.find_all("tr"):
        cells = row.find_all("td", recursive=False)
        if len(cells) == 2 and cells[0].get_text(" ", strip=True) == "CGPA":
            value = cells[1].get_text(strip=True).replace(",", ".")
            try:
                cgpa = Decimal(value)
            except InvalidOperation:
                break
            if cgpa.is_finite():
                return " ".join(name_parts), cgpa
            break
    raise RuntimeError("CGPA is missing or invalid in Academic Status.")


def get_profile(session, response):
    try:
        return parse_profile(response.text)
    except RuntimeError:
        pass


    url = safe_url(urljoin(START_URL, "ajax/userInfo.php"))
    profile = session.get(
        url, headers={"Referer": START_URL}, timeout=TIMEOUT, allow_redirects=False,
    )
    profile.raise_for_status()
    if 300 <= profile.status_code < 400:
        raise RuntimeError("Profile request was redirected; please start a fresh login.")
    content_type = profile.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type not in ("text/html", "application/xhtml+xml", "text/plain"):
        raise RuntimeError("Profile request returned an unexpected content type.")
    soup = BeautifulSoup(profile.text, "html.parser")
    if profile.text.lstrip().startswith("Logout") or form_kind(soup)[1] is not None:
        raise RuntimeError("Session expired or login is incomplete; please log in again.")
    return parse_profile(profile.text)

def display_error(soup):
    msg = soup.select_one('[role="alert"], .bk-form-error, .alert-error, .kc-feedback-text, #input-error')
    if msg:
        print("Sunucu mesajı:", msg.get_text(" ", strip=True)[:250])


def main():
    print("=== Bilkent STARS CLI (deneysel) ===")
    username = input("Bilkent ID: ").strip()
    if not username:
        raise RuntimeError("Bilkent ID boş olamaz.")
    password = getpass("Şifre: ")
    if not password:
        raise RuntimeError("Şifre boş olamaz.")


    with requests.Session() as session:
        session.headers.update({"User-Agent": "Mozilla/5.0 STARS-CLI/0.1"})
        print("\nSTARS giriş sayfası açılıyor...")
        response = get_page(session, START_URL)
        credentials_sent = False
        otp_sent = False

        for _ in range(6):
            if logged_in(response):
                print("\n✓ STARS/SRS giriş başarılı görünüyor!")
                try:
                    name, cgpa = get_profile(session, response)
                except RuntimeError as exc:
                    print(f"Profile unavailable: {exc}")
                else:
                    print(f"Name: {name}")
                    print(f"CGPA: {cgpa}")
                print("Oturum yalnızca bu uygulamanın belleğinde tutuluyor.")
                input("Çıkmak için Enter'a bas...")
                return

            soup = BeautifulSoup(response.text, "html.parser")
            kind, form = form_kind(soup)
            if form is None:
                print("Giriş formu bulunamadı. Şu sayfadayız:")
                print(urlparse(response.url).hostname, urlparse(response.url).path)
                display_error(soup)
                return

            fields = form_fields(form)
            if kind == "password":
                if credentials_sent:
                    print("\nKullanıcı adı/şifre adımına geri dönüldü. Giriş başarısız olabilir.")
                    display_error(soup)
                    return
                field = user_field(form)
                if field:
                    fields[field["name"]] = username
                elif "username" in fields:
                    fields["username"] = username
                fields[form.select_one('input[type="password"]')["name"]] = password
                print("Kullanıcı adı ve şifre gönderiliyor...")
                credentials_sent = True
                response = submit(session, response, form, fields)

            elif kind == "username":
                if credentials_sent:
                    print("Kullanıcı adı aşamasına geri dönüldü.")
                    display_error(soup)
                    return
                field = user_field(form)
                fields[field["name"]] = username
                print("Bilkent ID gönderiliyor...")
                response = submit(session, response, form, fields)

            elif kind == "otp":
                if otp_sent:
                    print("\nSMS doğrulama ekranına geri dönüldü; kod kabul edilmemiş olabilir.")
                    display_error(soup)
                    return
                length = len(form.select("[data-pin-input] input[type='text']")) or 5
                print("\nSMS doğrulama aşamasına ulaşıldı.")
                code = input(f"SMS kodu ({length} hane): ").strip()
                if not code.isdigit() or len(code) != length:
                    raise RuntimeError(f"Kod {length} rakamdan oluşmalı.")
                fields["code"] = code
                otp_sent = True
                print("Kod doğrulanıyor...")
                response = submit(session, response, form, fields)

        print("Beklenmeyen sayfa akışı. İşlem durduruldu.")


def run():
    try:
        import sys
        if "--classic" in sys.argv:
            main()
        else:
            from stars_tui import StarsApp
            StarsApp().run()
    except requests.RequestException as exc:
        print("\nAğ/HTTP hatası:", type(exc).__name__)
        print("Bağlantıyı ve yönlendirmeyi kontrol et.")
    except RuntimeError as exc:
        print("\nHata:", str(exc))
    except KeyboardInterrupt:
        print("\nİptal edildi.")


if __name__ == "__main__":
    run()
