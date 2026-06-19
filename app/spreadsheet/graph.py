from __future__ import annotations

import base64
import re
import threading
import traceback
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import msal
from PySide6.QtCore import QMetaObject, QObject, Qt, Slot
from PySide6.QtWidgets import QApplication, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from app.spreadsheet.base import SheetRow, SpreadsheetProvider


class _DeviceFlowHelper(QObject):
    """Owns the device-code dialog. Must be created on the main thread."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._dlg: QDialog | None = None
        self._user_code: str = ""

    @Slot()
    def _show(self) -> None:
        user_code = self._user_code
        app = QApplication.instance()
        dlg = QDialog()
        dlg.setWindowTitle("Microsoft Sign-In")
        layout = QVBoxLayout(dlg)
        layout.addWidget(QLabel("A browser window has been opened.\nEnter this code to sign in:"))
        lbl = QLabel(user_code)
        lbl.setStyleSheet("font-size: 24px; font-weight: bold; letter-spacing: 4px;")
        layout.addWidget(lbl)
        row = QHBoxLayout()
        btn = QPushButton("Copy Code")
        btn.clicked.connect(lambda: app.clipboard().setText(user_code))  # type: ignore[union-attr]
        row.addWidget(btn)
        layout.addLayout(row)
        dlg.show()
        self._dlg = dlg

    @Slot()
    def _close(self) -> None:
        if self._dlg is not None:
            self._dlg.accept()
            self._dlg = None

_GRAPH = "https://graph.microsoft.com/v1.0"
_SCOPES = ["https://graph.microsoft.com/Files.Read.All"]

_PERSONAL_HOSTS = ("onedrive.live.com", "1drv.ms", "d.docs.live.net")


def _detect_tenant(file_url: str, configured: str) -> str:
    """Infer the correct MSAL authority tenant from the file URL when not explicitly set."""
    if configured and configured not in ("common", "consumers"):
        return configured  # explicit tenant ID always wins

    host = urlparse(file_url).netloc.lower()
    print(f"[Graph] detecting tenant from host: {host!r}")

    if any(h in host for h in _PERSONAL_HOSTS):
        print("[Graph] detected: personal OneDrive → consumers")
        return "consumers"

    m = re.match(r"^([a-z0-9-]+)(?:-my)?\.sharepoint\.com$", host)
    if m:
        tenant = f"{m.group(1)}.onmicrosoft.com"
        print(f"[Graph] detected: SharePoint → {tenant}")
        return tenant

    print(f"[Graph] could not detect tenant, using: {configured or 'consumers'}")
    return configured or "consumers"


_ONEDRIVE_VIEWER_RE = re.compile(
    r"^https?://onedrive\.live\.com/:[a-z]+:/g/personal/([0-9a-fA-F]+)/([^?#]+)",
    re.IGNORECASE,
)


def _normalize_sharing_url(url: str) -> str:
    """Convert full OneDrive viewer URL to 1drv.ms sharing link.

    Graph's /shares API only accepts 1drv.ms links, not the
    onedrive.live.com/:x:/g/personal/ viewer URLs.
    """
    m = _ONEDRIVE_VIEWER_RE.match(url)
    if m:
        normalized = f"https://1drv.ms/x/c/{m.group(1)}/{m.group(2)}"
        print(f"[Graph] normalized viewer URL → {normalized!r}")
        return normalized
    return url


def _encode_sharing_url(url: str) -> str:
    url = url.strip()
    # Strip query params / fragment, then normalize viewer URLs to 1drv.ms form
    parsed = urlparse(url)
    url = parsed._replace(query="", fragment="").geturl()
    url = _normalize_sharing_url(url)
    encoded = base64.urlsafe_b64encode(url.encode("utf-8")).rstrip(b"=").decode()
    token = f"u!{encoded}"
    print(f"[Graph] sharing URL: {url!r}")
    print(f"[Graph] sharing token: {token}")
    return token


class GraphSpreadsheetProvider(SpreadsheetProvider):
    def __init__(
        self,
        file_url: str,
        client_id: str,
        tenant_id: str,
        cache_path: Path,
    ) -> None:
        self._file_url = file_url.strip()
        self._cache_path = cache_path

        self._token_cache = msal.SerializableTokenCache()
        if cache_path.exists():
            self._token_cache.deserialize(cache_path.read_text(encoding="utf-8"))

        tenant = _detect_tenant(file_url, tenant_id)
        authority = f"https://login.microsoftonline.com/{tenant}"
        print(f"[Graph] using authority: {authority}")
        self._app = msal.PublicClientApplication(
            client_id,
            authority=authority,
            token_cache=self._token_cache,
        )

        # Must be created on main thread (dialog owner)
        self._flow_helper = _DeviceFlowHelper()

        # Lazily resolved
        self._drive_id: str | None = None
        self._item_id: str | None = None
        # Per-sheet row cache: sheet_name -> (headers, rows_2d)
        self._sheet_cache: dict[str, tuple[list[str], list[list[str]]]] = {}

    # ── Auth ─────────────────────────────────────────────────────────────────

    def _get_token(self) -> str:
        accounts = self._app.get_accounts()
        print(f"[Graph] accounts in cache: {[a.get('username') for a in accounts]}")
        result = None
        if accounts:
            result = self._app.acquire_token_silent(_SCOPES, account=accounts[0])
            print(f"[Graph] silent token result keys: {list(result.keys()) if result else None}")
        if not result:
            result = self._do_device_flow()
        self._save_cache()
        if "access_token" not in result:
            print(f"[Graph] auth failed — full result: {result}")
            raise RuntimeError(f"Auth failed: {result.get('error_description', result)}")
        print("[Graph] access token acquired successfully")
        return result["access_token"]

    def _do_device_flow(self) -> dict:
        print(f"[Graph] initiating device flow (scopes={_SCOPES})")
        flow = self._app.initiate_device_flow(_SCOPES)
        print(f"[Graph] device flow response keys: {list(flow.keys())}")
        if "user_code" not in flow:
            print(f"[Graph] device flow failed — full response: {flow}")
            raise RuntimeError(f"Failed to start device flow: {flow.get('error_description', flow)}")

        webbrowser.open(flow["verification_uri"])

        # Schedule dialog on the main thread (safe from any thread via QueuedConnection)
        self._flow_helper._user_code = flow["user_code"]
        QMetaObject.invokeMethod(self._flow_helper, "_show", Qt.ConnectionType.QueuedConnection)

        # Poll in a separate thread; signal main thread to close dialog when done
        result_holder: list[dict] = []
        done = threading.Event()

        def _poll() -> None:
            result_holder.append(self._app.acquire_token_by_device_flow(flow))
            done.set()
            QMetaObject.invokeMethod(self._flow_helper, "_close", Qt.ConnectionType.QueuedConnection)

        t = threading.Thread(target=_poll, daemon=True)
        t.start()
        done.wait()  # blocks worker thread only; main thread event loop stays alive

        result = result_holder[0] if result_holder else {}
        print(f"[Graph] device flow token result keys: {list(result.keys())}")
        if "access_token" not in result:
            print(f"[Graph] device flow token failed — full result: {result}")
        return result

    def _save_cache(self) -> None:
        if self._token_cache.has_state_changed:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(self._token_cache.serialize(), encoding="utf-8")

    # ── HTTP ─────────────────────────────────────────────────────────────────

    def _get(self, path: str) -> Any:
        token = self._get_token()
        url = f"{_GRAPH}{path}"
        print(f"[Graph] GET {url}")
        headers = {"Authorization": f"Bearer {token}"}
        with httpx.Client(timeout=30) as c:
            r = c.get(url, headers=headers)
            print(f"[Graph] response {r.status_code}")
            if r.is_error:
                print(f"[Graph] error body: {r.text}")
            r.raise_for_status()
            return r.json()

    # ── Drive item resolution ─────────────────────────────────────────────────

    def _resolve_file(self) -> tuple[str, str]:
        if self._drive_id and self._item_id:
            return self._drive_id, self._item_id
        encoded = _encode_sharing_url(self._file_url)
        data = self._get(f"/shares/{encoded}/driveItem")
        self._item_id = data["id"]
        self._drive_id = data["parentReference"]["driveId"]
        return self._drive_id, self._item_id

    # ── Sheet data ────────────────────────────────────────────────────────────

    def _fetch_sheet(self, sheet: str) -> tuple[list[str], list[list[str]]]:
        if sheet in self._sheet_cache:
            return self._sheet_cache[sheet]
        drive_id, item_id = self._resolve_file()
        data = self._get(
            f"/drives/{drive_id}/items/{item_id}"
            f"/workbook/worksheets/{sheet}/usedRange"
        )
        values: list[list[Any]] = data.get("values", [])
        if not values:
            self._sheet_cache[sheet] = ([], [])
            return [], []
        headers = [str(h) for h in values[0]]
        rows = [[str(cell) for cell in row] for row in values[1:]]
        self._sheet_cache[sheet] = (headers, rows)
        return headers, rows

    def invalidate_sheet(self, sheet: str) -> None:
        self._sheet_cache.pop(sheet, None)

    # ── SpreadsheetProvider interface ─────────────────────────────────────────

    def get_worksheets(self) -> list[str]:
        drive_id, item_id = self._resolve_file()
        data = self._get(f"/drives/{drive_id}/items/{item_id}/workbook/worksheets")
        return [ws["name"] for ws in data.get("value", [])]

    def get_columns(self, sheet: str) -> list[str]:
        headers, _ = self._fetch_sheet(sheet)
        return headers

    def get_unique_values(self, sheet: str, column: str) -> list[str]:
        headers, rows = self._fetch_sheet(sheet)
        if column not in headers:
            return []
        col_idx = headers.index(column)
        seen: dict[str, int] = {}  # value -> first row number (1-based data row)
        for row_num, row in enumerate(rows, start=1):
            val = row[col_idx] if col_idx < len(row) else ""
            if val and val not in seen:
                seen[val] = row_num
        return [v for v, _ in sorted(seen.items(), key=lambda kv: kv[1], reverse=True)]

    def get_rows_by_value(self, sheet: str, column: str, value: str) -> list[SheetRow]:
        headers, rows = self._fetch_sheet(sheet)
        if column not in headers:
            return []
        col_idx = headers.index(column)
        result: list[SheetRow] = []
        for row_num, row in enumerate(rows, start=1):
            cell = row[col_idx] if col_idx < len(row) else ""
            if cell == value:
                values: dict[str, str] = {}
                for i, h in enumerate(headers):
                    val = row[i] if i < len(row) else ""
                    if h not in values or (val and not values[h]):
                        values[h] = val
                result.append(SheetRow(row_number=row_num, values=values))
        return result
