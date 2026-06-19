from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class SheetRow:
    row_number: int          # 1-based, preserves original spreadsheet order
    values: dict[str, str]   # column header -> cell value


class SpreadsheetProvider(ABC):
    @abstractmethod
    def get_worksheets(self) -> list[str]:
        """Return names of all worksheets in the workbook."""

    @abstractmethod
    def get_columns(self, sheet: str) -> list[str]:
        """Return column header names for the given worksheet."""

    @abstractmethod
    def get_unique_values(self, sheet: str, column: str) -> list[str]:
        """
        Return deduplicated values from the column, ordered by first occurrence
        row number descending (newest series first).
        """

    @abstractmethod
    def get_rows_by_value(self, sheet: str, column: str, value: str) -> list[SheetRow]:
        """Return all rows where the given column equals value."""
