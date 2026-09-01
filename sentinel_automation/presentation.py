from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Column:
    heading: str
    minimum: int
    maximum: int


def render_table(
    title: str,
    summary: str,
    columns: tuple[Column, ...],
    rows: list[tuple[Any, ...]],
) -> str:
    """Render a width-aware table, falling back to readable cards on narrow terminals."""
    if not columns:
        raise ValueError("At least one output column is required")
    if any(len(row) != len(columns) for row in rows):
        raise ValueError("Every output row must match the configured columns")

    text_rows = [tuple(_text(value) for value in row) for row in rows]
    lines = [title, summary, ""]
    if not text_rows:
        lines.append("No results.")
        return "\n".join(lines)

    terminal_width = shutil.get_terminal_size((140, 24)).columns
    available = min(max(terminal_width, 40), 180)
    minimum_table_width = 4 + sum(column.minimum for column in columns) + 2 * len(columns)
    if available < minimum_table_width:
        for index, row in enumerate(text_rows, start=1):
            lines.append(f"[{index:02}] {row[0]}")
            for column, value in zip(columns[1:], row[1:]):
                lines.append(f"     {column.heading.title() + ':':<17}{value}")
            lines.append("")
        return "\n".join(lines).rstrip()

    content_width = available - 4 - 2 * len(columns)
    widths = _column_widths(columns, text_rows, content_width)
    lines.append(_row(("#", *(column.heading for column in columns)), (4, *widths)))
    lines.append("-" * available)
    for index, row in enumerate(text_rows, start=1):
        lines.append(_row((str(index), *row), (4, *widths)))
    return "\n".join(lines)


def status_text(value: Any) -> str:
    return str(value).replace("_", " ").upper()


def _column_widths(
    columns: tuple[Column, ...], rows: list[tuple[str, ...]], available: int
) -> tuple[int, ...]:
    natural = [
        min(
            column.maximum,
            max(len(column.heading), *(len(row[index]) for row in rows)),
        )
        for index, column in enumerate(columns)
    ]
    widths = [min(target, column.minimum) for target, column in zip(natural, columns)]
    remaining = max(0, available - sum(widths))
    while remaining and any(width < target for width, target in zip(widths, natural)):
        index = max(range(len(widths)), key=lambda item: natural[item] - widths[item])
        if widths[index] >= natural[index]:
            break
        widths[index] += 1
        remaining -= 1
    return tuple(widths)


def _row(values: tuple[str, ...], widths: tuple[int, ...]) -> str:
    return "  ".join(
        _truncate(value, width).ljust(width) for value, width in zip(values, widths)
    ).rstrip()


def _truncate(value: str, width: int) -> str:
    if len(value) <= width:
        return value
    return value[: max(1, width - 3)] + "..."


def _text(value: Any) -> str:
    if value is None or value == "":
        return "-"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value)
