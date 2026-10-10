import unittest
from unittest.mock import Mock

import requests
from textual.widgets import DataTable, Input, Static, TabbedContent
from textual.events import MouseScrollDown

from stars_cli import START_URL, safe_request
from stars_client import Profile, SessionExpired, StarsClient, Table, View, parse_student, parse_view
from stars_tui import Dashboard, Login, StarsApp
from tests.test_stars_cli import HOME


PROFILE = Profile("Example Deniz Örnek", "2.75", "TEST", "Good standing", "2", "example@example.invalid")
PASSWORD = '<form method="post" action="/auth"><input name="username"><input name="password" type="password"><input name="state" value="fictional"></form>'
OTP = '<form method="post" action="/verify"><div data-pin-input>' + '<input type="text">' * 5 + '</div><input name="code" type="hidden"></form>'


def response(html, url=START_URL, status=200, headers=None):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = html.encode()
    result.encoding = 'utf-8'
    result.headers.update(headers or {"Content-Type": "text/html"})
    return result


class ClientTests(unittest.TestCase):
    def test_dynamic_password_and_otp_then_protected_profile(self):
        client = StarsClient()
        client.session = Mock()
        client.session.request.side_effect = [
            response(PASSWORD, 'https://signin.bilkent.edu.tr/start'),
            response(OTP, 'https://signin.bilkent.edu.tr/step'),
            response('<script>completeLogin("HOME")</script>'), response(HOME),
        ]
        self.assertEqual(client.start('fictional-id', 'fictional-password'), 'otp')
        self.assertFalse(client.ready)
        self.assertEqual(client.verify('12345').name, PROFILE.name)
        self.assertTrue(client.ready)
        calls = client.session.request.call_args_list
        self.assertEqual(calls[1].kwargs['data']['state'], 'fictional')
        self.assertEqual(calls[2].args[:2], ('POST', 'https://signin.bilkent.edu.tr/verify'))
        self.assertEqual(calls[2].kwargs['data']['code'], '12345')
        with self.assertRaises(RuntimeError):
            client.verify('12345')
        self.assertEqual(client.session.request.call_count, 4)

    def test_rejected_otp_is_not_retried(self):
        client = StarsClient()
        client.response = response(OTP, 'https://signin.bilkent.edu.tr/step')
        client.session = Mock()
        client.session.request.return_value = response(OTP, 'https://signin.bilkent.edu.tr/step')
        with self.assertRaisesRegex(RuntimeError, 'rejected'):
            client.verify('12345')
        with self.assertRaises(RuntimeError):
            client.verify('12345')
        client.session.request.assert_called_once()

    def test_block_redirect_before_sending(self):
        for location in ('https://untrusted.invalid/', 'http://stars.bilkent.edu.tr/', 'https://user@stars.bilkent.edu.tr/'):
            session = Mock()
            session.request.return_value = response('', status=302, headers={'Location': location})
            with self.assertRaises(RuntimeError):
                safe_request(session, 'POST', START_URL, data={'code': 'fictional'})
            session.request.assert_called_once()

    def test_post_redirect_drops_body(self):
        session = Mock()
        session.request.side_effect = [response('', status=302, headers={'Location': '/srs/'}), response(HOME)]
        safe_request(session, 'POST', 'https://signin.bilkent.edu.tr/auth', data={'code': 'fictional'})
        self.assertEqual(session.request.call_args.args[0], 'GET')
        self.assertNotIn('data', session.request.call_args.kwargs)

    def test_expired_html_response(self):
        client = StarsClient()
        client.ready = True
        client.session = Mock()
        client.session.request.return_value = response('Logout::expired')
        with self.assertRaises(SessionExpired):
            client.view('weekly')
        self.assertFalse(client.ready)

    def test_profile_email_and_missing_values(self):
        profile = parse_student(HOME.replace('</fieldset>', '<p>Contact E-Mail Address: example@example.invalid</p></fieldset>'))
        self.assertEqual(profile.email, 'example@example.invalid')
        self.assertEqual(profile.phone, '')

    def test_weekly_spans_preserve_alignment(self):
        html = '<table><tr><th>Time</th><th>Monday</th><th>Tuesday</th></tr><tr><td>09:00</td><td rowspan="2">TEST 201</td><td>A</td></tr><tr><td>10:00</td><td>B</td></tr></table>'
        table = parse_view(html, 'weekly').tables[0]
        self.assertEqual(table.columns, ['Time', 'Monday', 'Tuesday'])
        self.assertEqual(table.rows[1], ['10:00', 'TEST 201', 'B'])

    def test_headerless_table_preserves_first_record(self):
        view = parse_view('<table><tr><td>TEST 201</td><td>12:00</td></tr></table>', 'exams')
        self.assertEqual(view.tables[0].rows, [['TEST 201', '12:00']])

    def test_missing_table_is_not_empty_schedule(self):
        self.assertIn('JavaScript', parse_view('<div id="calendar"></div>', 'weekly').message)
        self.assertEqual(parse_view('<p>No scheduled exams found</p>', 'exams').message, 'No scheduled exams found')


class FakeClient:
    otp_length = 5

    def __init__(self):
        self.calls = []
        self.closed = False
        self.ready = False

    def start(self, username, password):
        self.calls.append('start')
        return 'otp'

    def verify(self, code):
        self.calls.append('verify')
        self.ready = True
        return PROFILE

    def close(self):
        self.closed = True

    def view(self, kind):
        self.calls.append(kind)
        return View(kind, [Table('Fictional academic data', ['Time', 'Monday', 'Tuesday'],
                                  [[str(i), 'TEST 201 · Room A', 'TEST 202'] for i in range(35)])])


class TUITests(unittest.IsolatedAsyncioTestCase):
    async def test_login_keyboard_mouse_tabs_scroll_refresh_and_cleanup(self):
        app = StarsApp(client_factory=FakeClient)
        async with app.run_test(size=(130, 42)) as pilot:
            await pilot.pause()
            app.screen.query_one('#username', Input).value = 'fictional-id'
            await pilot.press('enter')
            self.assertEqual(app.focused.id, 'password')
            app.screen.query_one('#password', Input).value = 'fictional-password'
            await pilot.click('#sign-in')
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app.focused.id, 'otp')
            self.assertEqual(app.screen.query_one('#password', Input).value, '')
            app.screen.query_one('#otp', Input).value = '12345'
            await pilot.press('enter')
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIsInstance(app.screen, Dashboard)
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.screen.query_one('#table-courses-0', DataTable)
            self.assertEqual(table.row_count, 35)
            await pilot.press('down')
            self.assertEqual(table.cursor_row, 1)
            table.post_message(MouseScrollDown(table, 5, 5, 0, 0, 0, False, False, False))
            await pilot.pause()
            self.assertGreater(table.scroll_y, 0)
            await pilot.click('#--content-tab-weekly')
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app.screen.query_one(TabbedContent).active, 'weekly')
            self.assertEqual(app.screen.query_one('#table-weekly-0', DataTable).row_count, 35)
            await pilot.press('3')
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app.screen.query_one(TabbedContent).active, 'exams')
            await pilot.click('#refresh')
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app.client.calls.count('exams'), 2)
            await pilot.resize_terminal(85, 28)
            await pilot.pause()
            self.assertGreater(app.screen.query_one(TabbedContent).size.width, 30)
        self.assertTrue(app.client.closed)

    async def test_expired_session_clears_tables_and_offers_login(self):
        class ExpiredClient(FakeClient):
            def view(self, kind):
                if kind == 'weekly':
                    raise SessionExpired('Your session expired. Sign in again.')
                return super().view(kind)
        app = StarsApp(client_factory=ExpiredClient)
        async with app.run_test(size=(120, 38)) as pilot:
            app.show_dashboard(PROFILE)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            await pilot.press('2')
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertEqual(app.focused.id, 'relogin')
            self.assertIn('expired', str(app.screen.query_one('#status-weekly', Static).render()))
            self.assertEqual(app.screen.query_one('#table-courses-0', DataTable).row_count, 0)
            await pilot.click('#relogin')
            await pilot.pause()
            self.assertIsInstance(app.screen, Login)

    async def test_authentication_error_stays_in_ui(self):
        class FailedClient(FakeClient):
            def start(self, username, password):
                raise RuntimeError('Authentication rejected.')
        app = StarsApp(client_factory=FailedClient)
        async with app.run_test(size=(100, 36)) as pilot:
            app.screen.query_one('#username', Input).value = 'fictional-id'
            app.screen.query_one('#password', Input).value = 'fictional-password'
            await pilot.click('#sign-in')
            await app.workers.wait_for_complete()
            await pilot.pause()
            self.assertIsInstance(app.screen, Login)
            self.assertEqual(app.focused.id, 'restart')
            self.assertIn('rejected', str(app.screen.query_one('#login-status', Static).render()))
            await pilot.click('#restart')
            await pilot.pause()
            self.assertEqual(app.focused.id, 'username')


if __name__ == '__main__':
    unittest.main()
