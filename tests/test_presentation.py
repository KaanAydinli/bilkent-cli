import unittest

from textual.widgets import Button

from stars_client import Table, View, parse_view
from stars_presentation import FittedTable, prepare_table, weekly_tables
from stars_tui import StarsApp
from tests.test_dashboard import FakeClient, PROFILE


class PresentationTests(unittest.TestCase):
    def test_course_name_limit_counts_spaces_and_keeps_original(self):
        name = 'An introduction to fabricated course topics and examples'
        table = Table('Courses', ['Course Name'], [[name], ['A' * 40]])
        result = prepare_table(table, 'courses')
        self.assertEqual(result.rows[0][0], name[:37] + '...')
        self.assertEqual(len(result.rows[0][0]), 40)
        self.assertEqual(result.rows[1][0], 'A' * 40)
        self.assertEqual(table.rows[0][0], name)

    def test_spare_labels_have_exactly_one_pair_of_parentheses(self):
        for value in ('Spare Hour', '(Spare Hour)', '((Spare Hour))', 'S', '(S)', '((S))'):
            with self.subTest(value=value):
                table = Table('Week', ['Time', 'Monday'], [['09:30', value]])
                self.assertEqual(prepare_table(table, 'weekly').rows[0][1], '(S)')

    def test_weekly_cutoff_and_labels(self):
        table = Table('Schedule » Weekly', ['Time', 'Monday'], [
            ['16:30 - 17:20', 'TEST 201 (Face-to-face)'],
            ['17.30 - 18.20', '(Spare Hour)'],
            ['18:30', 'Late course'],
        ])
        result = prepare_table(table, 'weekly')
        self.assertEqual(result.rows, [['16:30 - 17:20', 'TEST 201'], ['17.30 - 17:30', '(S)']])

    def test_weekly_legend_tables_do_not_compete_with_timetable(self):
        html = """
        <table><tr><td>Online</td><td>Lab/Studio</td><td>Face-to-face</td></tr></table>
        <table><tr><th>Time</th><th>Monday</th><th>Tuesday</th></tr>
        <tr><td>09:30</td><td>TEST 201 (Lab/Studio) Room A</td><td>TEST 202</td></tr>
        <tr><td>10:30</td><td>TEST 203</td><td>Spare Hour</td></tr></table>
        <table><tr><td>Unrecognized legend explanation</td></tr></table>
        """
        tables = weekly_tables(parse_view(html, 'weekly').tables)
        self.assertEqual(len(tables), 1)
        shown = prepare_table(tables[0], 'weekly')
        self.assertEqual(shown.columns, ['Time', 'Monday', 'Tuesday'])
        self.assertNotIn('Lab/Studio', str(shown.rows))
        self.assertIn('TEST 201', shown.rows[0][1])
        self.assertIn('Room A', shown.rows[0][1])
        self.assertEqual(shown.rows[0][2], 'TEST 202')
        self.assertEqual(shown.rows[1], ['10:30', 'TEST 203', '(S)'])

    def test_embedded_legend_row_is_removed_without_shifting_courses(self):
        table = Table('Weekly', ['Time', 'Monday', 'Tuesday'], [
            ['09:30', 'TEST 201', 'TEST 202'],
            ['Online', 'Lab / Studio', 'Face-to-face'],
        ])
        self.assertEqual(prepare_table(table, 'weekly').rows, [['09:30', 'TEST 201', 'TEST 202']])


class LayoutTests(unittest.IsolatedAsyncioTestCase):
    async def test_weekly_mouse_targets_and_persistent_cell_shading(self):
        class Client(FakeClient):
            def view(self, kind):
                if kind == 'weekly':
                    return View('Week', [Table('Weekly', ['Time', 'Monday', 'Tuesday'],
                                              [['09:30', 'TEST 201 Room A', '(S)'],
                                               ['10:30', '', 'TEST 202']])])
                return super().view(kind)

        app = StarsApp(client_factory=Client)
        async with app.run_test(size=(120, 38)) as pilot:
            app.show_dashboard(PROFILE)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press('2')
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.screen.query_one('#table-weekly-0', FittedTable)
            monday_x = table.ordered_columns[0].width
            tuesday_x = monday_x + table.ordered_columns[1].width
            y = table.header_height + 1

            def style_at(x):
                return app.screen.get_style_at(table.region.x + x, table.region.y + y)

            await pilot.hover('#hint')
            await pilot.pause()
            occupied = style_at(monday_x + 2).bgcolor
            spare = style_at(tuesday_x + 2).bgcolor
            border = style_at(monday_x).bgcolor
            self.assertNotEqual(occupied, spare)
            self.assertNotEqual(border, occupied)
            self.assertTrue(style_at(monday_x).meta['out_of_bounds'])
            await pilot.click(table, offset=(monday_x + 2, y))
            await pilot.pause()
            self.assertEqual(table.cursor_column, 1)
            self.assertEqual(style_at(monday_x).bgcolor, border)
            self.assertNotEqual(style_at(monday_x + 2).bgcolor, occupied)
            await pilot.click(table, offset=(tuesday_x, y))
            await pilot.pause()
            self.assertEqual(table.cursor_column, 1)
            await pilot.hover(table, offset=(tuesday_x + 2, y))
            await pilot.pause()
            self.assertEqual(table.hover_coordinate.column, 2)
            self.assertEqual(style_at(tuesday_x).bgcolor, border)
            self.assertNotEqual(style_at(tuesday_x + 2).bgcolor, spare)
            await pilot.hover(table, offset=(tuesday_x, y))
            await pilot.pause()
            self.assertFalse(table._show_hover_cursor)
            await pilot.click(table, offset=(tuesday_x + 2, y))
            await pilot.pause()
            self.assertEqual(table.cursor_column, 2)
            await pilot.hover('#hint')
            await pilot.pause()
            self.assertEqual(style_at(monday_x + 2).bgcolor, occupied)
            for line in table.get_row_at(0)[1].plain.splitlines():
                self.assertTrue(line.startswith('┃ '))

    async def test_full_week_fits_height_and_reflows_on_height_only_resize(self):
        class Client(FakeClient):
            def view(self, kind):
                if kind != 'weekly':
                    return super().view(kind)
                rows = [[f'{hour:02}:30', 'Lunch break' if hour == 12 else
                         'TEST 201 A very long course description Room B-123', 'S', '', '', 'TEST 202']
                        for hour in range(9, 18)]
                return View('Week', [Table('Weekly', ['Time', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'], rows)])

        app = StarsApp(client_factory=Client)
        async with app.run_test(size=(130, 44)) as pilot:
            app.show_dashboard(PROFILE)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press('2')
            await app.workers.wait_for_complete()
            await pilot.pause()
            heights = []
            for width, height in ((130, 44), (130, 30), (95, 30), (130, 44)):
                await pilot.resize_terminal(width, height)
                await pilot.pause()
                table = app.screen.query_one('#table-weekly-0', FittedTable)
                self.assertEqual(table.row_count, 9)
                total = sum(row.height for row in table.rows.values()) + table.header_height
                self.assertEqual(total, table.size.height)
                self.assertLessEqual(table.virtual_size.height, table.size.height)
                self.assertLessEqual(table.virtual_size.width, table.size.width)
                self.assertEqual(table.max_scroll_y, 0)
                self.assertEqual(table.max_scroll_x, 0)
                self.assertIn('Lunch break', table.source.rows[3][1])
                self.assertIn('17:30', table.get_row_at(8)[0].plain)
                heights.append([row.height for row in table.rows.values()])
                await pilot.press('end')
                self.assertEqual(table.scroll_y, 0)
            self.assertNotEqual(heights[0], heights[1])
            self.assertEqual(heights[1], heights[2])
            self.assertEqual(heights[0], heights[3])

    async def test_wrapped_exam_rooms_and_compact_sidebar_at_two_sizes(self):
        rooms = ', '.join(f'Building-{i} Room-123' for i in range(12))

        class Client(FakeClient):
            def view(self, kind):
                if kind == 'exams':
                    return View('Exams', [Table('Exams', ['Course', 'Date', 'Classrooms'], [['TEST 201', '2030-01-15', rooms]])])
                if kind == 'weekly':
                    return View('Weekly schedule', [Table('Legend', ['Column 1', 'Column 2'], [['Online', 'Lab/Studio']]), Table('Schedule » Weekly', ['Time', 'Monday'], [['17:30', '(Spare Hour)'], ['18:30', 'Late']])])
                return super().view(kind)

        app = StarsApp(client_factory=Client)
        async with app.run_test(size=(120, 42)) as pilot:
            app.show_dashboard(PROFILE)
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.press('3')
            await app.workers.wait_for_complete()
            await pilot.pause()
            for size in ((120, 42), (85, 32)):
                await pilot.resize_terminal(*size)
                await pilot.pause()
                table = app.screen.query_one('#table-exams-0', FittedTable)
                self.assertFalse(table.show_horizontal_scrollbar)
                self.assertLessEqual(table.virtual_size.width, table.size.width)
                displayed = table.get_row_at(0)[2].plain
                self.assertIn('\n', displayed)
                self.assertEqual(''.join(displayed.split()), ''.join(rooms.split()))
                self.assertGreater(next(iter(table.rows.values())).height, 1)
                for id in ('refresh', 'relogin'):
                    button = app.screen.query_one('#' + id, Button)
                    self.assertEqual(button.size.height, 1)
                    self.assertIn("sidebar", [ancestor.id for ancestor in button.ancestors])
            await pilot.press('2')
            await app.workers.wait_for_complete()
            await pilot.pause()
            table = app.screen.query_one('#table-weekly-0', FittedTable)
            self.assertEqual(table.row_count, 1)
            self.assertEqual(len(app.screen.query('#view-weekly FittedTable')), 1)
            self.assertEqual(table.get_row_at(0)[1].plain.replace('┃', '').strip(), '(S)')
            self.assertGreaterEqual(next(iter(table.rows.values())).height, 4)
            self.assertEqual(len(app.screen.query('#toolbar')), 0)
            self.assertEqual(app.screen.query_one('#navigation').region.y, app.screen.query_one('#sidebar').region.y)
            self.assertEqual(len(app.screen.query('#view-weekly .table-title')), 0)
            self.assertEqual(table.styles.scrollbar_size_vertical, 0)
            self.assertFalse(app.screen.query_one('#status-weekly').display)
            old_client = app.client
            await pilot.click('#relogin')
            await pilot.pause()
            self.assertTrue(old_client.closed)
            self.assertEqual(app.focused.id, 'username')
