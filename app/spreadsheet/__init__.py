from __future__ import annotations

from app import config
from app.spreadsheet.base import SheetRow, SpreadsheetProvider


def get_provider() -> SpreadsheetProvider | None:
    if config.EXCEL_FILE_URL and config.EXCEL_CLIENT_ID:
        from app.spreadsheet.graph import GraphSpreadsheetProvider
        return GraphSpreadsheetProvider(
            file_url=config.EXCEL_FILE_URL,
            client_id=config.EXCEL_CLIENT_ID,
            tenant_id=config.EXCEL_TENANT_ID,
            cache_path=config.DATA_DIR / ".msal_cache.bin",
        )
    return None


__all__ = ["get_provider", "SpreadsheetProvider", "SheetRow"]
