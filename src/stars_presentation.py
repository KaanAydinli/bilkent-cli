import re

from rich.cells import cell_len
from rich.text import Text
from rich.segment import Segment
from rich.style import Style
from textual.widgets import DataTable

from stars_client import Table


TIME = re.compile(r"(?<!\d)([01]?\d|2[0-3])[:.]([0-5]\d)(?!\d)")


LEGEND = re.compile(r"(?:column\s*\d+|online(?:\s+lab)?|lab(?:\s*/\s*studio)?|studio|lecture|face[ -]*to[ -]*face|spare\s+hour|\(*S\)*)", re.I)


def weekly_heading(value):
    return " / ".join(part.strip() for part in value.split(" / ")
                      if not LEGEND.fullmatch(part.strip()))


def is_weekly_legend(table):
    values = table.columns + [cell for row in table.rows for cell in row]
    return bool(values) and all(not value.strip() or LEGEND.fullmatch(value.strip()) for value in values)


def weekly_tables(tables):
    candidates = [table for table in tables if not is_weekly_legend(table)]
    timed = [table for table in candidates if any(
        row and TIME.match(row[0].strip()) for row in table.rows
    )]

    return timed or candidates


def prepare_table(table, kind):
    rows = []
    time_column = next((i for i, name in enumerate(table.columns)
                        if name.casefold().strip() in ("time", "hour", "hours", "saat")), 0)
    for original in table.rows:
        row = list(original)
        if kind == "courses":
            for i, name in enumerate(table.columns):
                if name.casefold().strip() == "course name" and len(row[i]) > 40:
                    row[i] = row[i][:37] + "..."
        elif kind == "weekly":
            if row and all(not cell.strip() or LEGEND.fullmatch(cell.strip()) for cell in row):
                continue
            times = list(TIME.finditer(row[time_column])) if row else []
            if times and int(times[0][1]) * 60 + int(times[0][2]) > 17 * 60 + 30:
                continue
            if times:
                row[time_column] = TIME.sub(
                    lambda m: "17:30" if int(m[1]) * 60 + int(m[2]) > 1050 else m[0],
                    row[time_column],
                )
            row = [re.sub(r"\(*\bspare\s+hour\b\)*", "(S)",
                          re.sub(r"\(?\bface[\s-]*to[\s-]*face\b\)?", "", cell, flags=re.I),
                          flags=re.I).strip() for cell in row]
            row = ["(S)" if re.fullmatch(r"\(*S\)*", cell.strip()) else
                   re.sub(r"\(+S\)+", "(S)", cell) for cell in row]
            row = [re.sub(r"\(?\bLab\s*/\s*Studio\b\)?", "", cell, flags=re.I).strip()
                   for cell in row]
        rows.append(row)
    columns = [weekly_heading(name) for name in table.columns] if kind == "weekly" else list(table.columns)
    return Table(table.title, columns, rows)


class FittedTable(DataTable):


    def __init__(self, table, kind, **kwargs):
        super().__init__(zebra_stripes=True, cursor_type="cell" if kind == "weekly" else "row",
                         cell_padding=0 if kind == "weekly" else 1, **kwargs)
        self.source = prepare_table(table, kind)
        self.kind = kind
        self.last_size = None
        self.styles.overflow_x = "hidden"
        if kind == "weekly":
            self.styles.height = "1fr"
            self.styles.margin = 0
            self.styles.scrollbar_size_vertical = 0
            self.styles.scrollbar_size_horizontal = 0

    def on_mount(self):
        self.reflow()

    def on_resize(self):
        self.call_after_refresh(self.reflow)

    def reflow(self):
        available = self.size.width - (0 if self.kind == "weekly" else 2)
        count = len(self.source.columns)
        size = (available, self.size.height if self.kind == "weekly" else 0)
        if not count or available < count * 3 or size == self.last_size:
            return
        self.last_size = size
        widths = [max(1, cell_len(name)) for name in self.source.columns]
        for row in self.source.rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], min(40, cell_len(cell)))
        budget = available - count * self.cell_padding * 2
        if self.kind == "weekly":
            widths = [budget // count + (i < budget % count) for i in range(count)]
            self.show_header = any(self.source.columns)
        while sum(widths) > budget:
            widest = max(range(count), key=widths.__getitem__)
            widths[widest] -= 1
        selected = self.cursor_row
        self.clear(columns=True)
        header_height = 1
        for name, width in zip(self.source.columns, widths):
            lines = Text(name).wrap(self.app.console, max(1, width - (self.kind == "weekly")), overflow="fold")
            if self.kind == "weekly":
                label = name
                if len(lines) > 1 and name in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"):
                    label = name[:3]
                heading = Text(label)
                heading.truncate(max(1, width - 3), overflow="ellipsis")
                lines = [Text("  ") + heading]
            header_height = max(header_height, len(lines))
            self.add_column(Text("\n").join(lines), width=width)
        self.header_height = header_height
        total_height = header_height if self.show_header else 0
        row_count = len(self.source.rows)
        body_height = max(row_count, self.size.height - total_height)
        for row_index, row in enumerate(self.source.rows):
            weekly = self.kind == "weekly"
            wrapped = [Text(cell).wrap(self.app.console, max(1, width - 3 * weekly), overflow="fold")
                       for cell, width in zip(row, widths)]
            height = max(4 if weekly else 1, *(len(lines) for lines in wrapped))
            if weekly:
                height = body_height // row_count + (row_index < body_height % row_count)
                separator = height > 1 and row_index < row_count - 1
                content_height = height - int(separator)
                cells = []
                for index, (lines, width) in enumerate(zip(wrapped, widths)):
                    if len(lines) > content_height:
                        lines = lines[:content_height]
                        lines[-1] = lines[-1].copy()
                        lines[-1].truncate(max(0, width - 4), overflow="crop")
                        lines[-1].append("…")
                    content = Text()
                    top = (content_height - len(lines)) // 2
                    for y in range(height):
                        if y:
                            content.append("\n")
                        content.append("┃", style="#596675 on #46505c")
                        if separator and y == height - 1:
                            content.append(" " + "─" * (width - 2), style="#414a55 on #222225")
                            continue
                        line = lines[y - top].copy() if top <= y < top + len(lines) else Text()
                        line = Text(" ") + line
                        line.pad_right(max(0, width - 1 - line.cell_len))
                        value = row[index].strip()
                        occupied = bool(value) and value not in ("(S)", "S", "-", "—") and value.casefold() != "lunch break"
                        line.stylize("#c5ced8 on #2c343e" if occupied else "#dedee3 on #222225")
                        content.append(line)
                    cells.append(content)
            else:
                cells = [Text("\n").join(lines) for lines in wrapped]
            self.add_row(*cells, height=height)
            total_height += height
        self.styles.height = "1fr" if self.kind == "weekly" else max(5, min(30, total_height + 1))
        if self.source.rows:
            self.move_cursor(row=min(selected, len(self.source.rows) - 1), animate=False, scroll=False)

    def _render_cell(self, row_index, column_index, base_style, width, cursor=False, hover=False):
        if self.kind != "weekly" or row_index < 0 or column_index < 0:
            return super()._render_cell(row_index, column_index, base_style, width, cursor, hover)
        lines = super()._render_cell(row_index, column_index, base_style, width, False, False)
        rendered = []
        for line_index, line in enumerate(lines):
            if len(lines) > 1 and row_index < len(self.source.rows) - 1 and line_index == len(lines) - 1:
                rendered.append(list(Segment.apply_style(line, post_style=Style(meta={"out_of_bounds": True}))))
                continue
            border, interior = list(Segment.divide(line, [1, width]))
            border = list(Segment.apply_style(border, post_style=Style(meta={"out_of_bounds": True})))
            if cursor or hover:
                highlight = Style(bgcolor="#405061" if cursor else "#354250")
                interior = list(Segment.apply_style(interior, post_style=highlight))
            rendered.append(border + interior)
        return rendered

    def on_data_table_cell_highlighted(self, event):
        if self.kind == "weekly" and self.source.rows:
            self.tooltip = Text(self.source.rows[event.coordinate.row][event.coordinate.column])
