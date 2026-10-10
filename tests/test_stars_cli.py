import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

import stars_cli


HOME = """
<div id="westContent"><fieldset><legend>Academic Status</legend>
<table><tr><td><img src="/unused-photo"></td><td><table>
<tr><td>Example&nbsp; Deniz</td></tr><tr><td>Örnek</td></tr>
<tr><td>TEST</td></tr><tr><td>00000000</td></tr>
<tr><td><input id="logoutButton" type="button"></td></tr>
</table></td></tr><tr><td colspan="2"><table>
<tr><td>CGPA</td><td>2.75</td></tr>
<tr><td>Standing</td><td>Example</td></tr>
</table></td></tr></table></fieldset></div>
"""


class ProfileTests(unittest.TestCase):
    def test_profile_with_nested_tables_and_unicode(self):
        self.assertEqual(stars_cli.parse_profile(HOME), ("Example Deniz Örnek", Decimal("2.75")))

    def test_comma_decimal(self):
        self.assertEqual(stars_cli.parse_profile(HOME.replace("2.75", "2,75"))[1], Decimal("2.75"))

    def test_missing_or_invalid_profile(self):
        for html in (
            "<form><input type='password'></form>",
            "Logout",
            HOME.replace('id="logoutButton"', ''),
            HOME.replace("Örnek", ""),
            HOME.replace("CGPA", "Other"),
            HOME.replace("2.75", ""),
            HOME.replace("2.75", "unavailable"),
            HOME.replace("2.75", "NaN"),
        ):
            with self.subTest(html_kind=html[:20]):
                with self.assertRaises(RuntimeError):
                    stars_cli.parse_profile(html)

    def test_profile_fragment_without_pane_wrapper(self):
        fragment = HOME.replace('<div id="westContent">', '').replace('</div>', '')
        self.assertEqual(stars_cli.parse_profile(fragment), ("Example Deniz Örnek", Decimal("2.75")))

    def test_fetches_profile_fragment_with_existing_session(self):
        session = Mock()
        session.get.return_value = Mock(
            status_code=200, headers={"Content-Type": "text/html; charset=utf-8"},
            text=HOME.replace('<div id="westContent">', '').replace('</div>', ''),
        )
        response = Mock(text='<div id="westContent"></div><script>completeLogin("HOME")</script>')
        self.assertEqual(stars_cli.get_profile(session, response), ("Example Deniz Örnek", Decimal("2.75")))
        session.get.assert_called_once_with(
            stars_cli.START_URL + "ajax/userInfo.php",
            headers={"Referer": stars_cli.START_URL}, timeout=stars_cli.TIMEOUT,
            allow_redirects=False,
        )

    def test_complete_home_does_not_need_another_request(self):
        session = Mock()
        stars_cli.get_profile(session, Mock(text=HOME))
        session.get.assert_not_called()

    def test_profile_errors_do_not_follow_redirects_or_retry(self):
        for status, content_type, body in (
            (302, "text/html", ""),
            (200, "text/plain", "Logout::expired"),
            (200, "text/html", '<form><input type="password"></form>'),
            (200, "application/json", '{}'),
            (200, "text/html", '<div>Unavailable</div>'),
        ):
            with self.subTest(status=status, content_type=content_type, body=body):
                session = Mock()
                session.get.return_value = Mock(
                    status_code=status, headers={"Content-Type": content_type}, text=body,
                )
                with self.assertRaises(RuntimeError):
                    stars_cli.get_profile(session, Mock(text='<div id="westContent"></div>'))
                session.get.assert_called_once()

    def test_terminal_output_after_login(self):
        response = Mock(url=stars_cli.START_URL, text='<div id="westContent"></div>')
        with patch("builtins.input", side_effect=["00000000", ""]), \
             patch("stars_cli.getpass", return_value="fictional-password"), \
             patch("stars_cli.requests.Session") as session_factory, \
             patch("stars_cli.get_page", return_value=response) as get_page, \
             patch("builtins.print") as output:
            session = session_factory.return_value.__enter__.return_value
            session.get.return_value = Mock(
                status_code=200, headers={"Content-Type": "text/html"}, text=HOME,
            )
            stars_cli.main()
            session.get.assert_called_once()
        output.assert_any_call("Name: Example Deniz Örnek")
        output.assert_any_call("CGPA: 2.75")
        get_page.assert_called_once()


if __name__ == "__main__":
    unittest.main()
