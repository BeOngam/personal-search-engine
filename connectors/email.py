"""
connectors/email.py

Gmail API connector for Personal Search.

Authentication:
    1. Put Google's OAuth Desktop App credentials at:
       ./credentials.json
       or set GMAIL_CREDENTIALS_FILE.

    2. On first run, a browser OAuth flow creates:
       ./token.json
       or GMAIL_TOKEN_FILE.

    3. Later runs reuse the saved refresh token.

Environment variables:
    GMAIL_CREDENTIALS_FILE
    GMAIL_TOKEN_FILE
    GMAIL_QUERY
    GMAIL_MAX_RESULTS

The connector is read-only.
"""

from __future__ import annotations

import base64
import os
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Generator, Optional

from bs4 import BeautifulSoup
from loguru import logger

from connectors.base import (
    BaseConnector,
    Document,
    Settings,
    SourceConfig,
    SourceType,
)

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
except ImportError:
    Request = None
    Credentials = None
    InstalledAppFlow = None
    build = None


SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly"
]


class EmailConnector(BaseConnector):
    """Fetch Gmail messages and convert them to project Documents."""

    SOURCE_TYPE = SourceType.EMAIL

    def __init__(self, settings: Settings):
        super().__init__(settings)

        self.credentials_file = Path(
            os.getenv(
                "GMAIL_CREDENTIALS_FILE",
                "./credentials.json"
            )
        ).expanduser()

        self.token_file = Path(
            os.getenv(
                "GMAIL_TOKEN_FILE",
                "./token.json"
            )
        ).expanduser()

        self.query = os.getenv("GMAIL_QUERY", "")

        self.max_results = _env_int(
            "GMAIL_MAX_RESULTS",
            100,
            minimum=1,
        )

        self._service = None

    def can_handle(self, path: Path) -> bool:
        """
        Gmail is an API source, not a filesystem source.
        """
        return False

    def fetch(
        self,
        source_cfg: Optional[SourceConfig] = None,
    ) -> Generator[Document, None, None]:
        """
        Fetch Gmail messages matching GMAIL_QUERY.
        """

        service = self._get_service()

        page_token: Optional[str] = None
        total = 0

        while True:
            response = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=self.query,
                    maxResults=self.max_results,
                    pageToken=page_token,
                )
                .execute()
            )

            for item in response.get("messages", []):
                message_id = item.get("id")

                if not message_id:
                    continue

                try:
                    message = (
                        service.users()
                        .messages()
                        .get(
                            userId="me",
                            id=message_id,
                            format="full",
                        )
                        .execute()
                    )

                    document = self._message_to_document(
                        message
                    )

                    if document is not None:
                        total += 1
                        yield document

                except Exception as exc:
                    logger.error(
                        f"Failed to fetch Gmail message "
                        f"{message_id}: {exc}"
                    )

            page_token = response.get("nextPageToken")

            if not page_token:
                break

        logger.info(
            f"Gmail fetch finished: {total} messages."
        )

    def _get_service(self):
        """Authenticate and create Gmail API client."""

        if self._service is not None:
            return self._service

        if build is None:
            raise RuntimeError(
                "Gmail dependencies are missing.\n"
                "Install:\n"
                "pip install google-api-python-client "
                "google-auth-httplib2 "
                "google-auth-oauthlib"
            )

        if not self.credentials_file.exists():
            raise FileNotFoundError(
                f"Gmail OAuth credentials not found: "
                f"{self.credentials_file}\n\n"
                "Download your Google OAuth Desktop App "
                "credentials and save them there, or set "
                "GMAIL_CREDENTIALS_FILE."
            )

        creds = None

        # Reuse existing OAuth token.
        if self.token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(
                    str(self.token_file),
                    SCOPES,
                )
            except Exception as exc:
                logger.warning(
                    f"Could not read Gmail token: {exc}"
                )

        # Refresh expired token.
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                logger.warning(
                    f"Could not refresh Gmail token: {exc}"
                )
                creds = None

        # First login.
        if not creds or not creds.valid:
            logger.info(
                "Starting Gmail OAuth authentication..."
            )

            flow = (
                InstalledAppFlow
                .from_client_secrets_file(
                    str(self.credentials_file),
                    SCOPES,
                )
            )

            creds = flow.run_local_server(
                port=0,
                access_type="offline",
                prompt="consent",
            )

            self.token_file.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            self.token_file.write_text(
                creds.to_json(),
                encoding="utf-8",
            )

            logger.info(
                f"Gmail token saved to {self.token_file}"
            )

        self._service = build(
            "gmail",
            "v1",
            credentials=creds,
            cache_discovery=False,
        )

        return self._service

    def _message_to_document(
        self,
        message: dict,
    ) -> Optional[Document]:
        """
        Convert Gmail API message to project's Document.
        """

        payload = message.get("payload") or {}

        headers = _headers(
            payload.get("headers", [])
        )

        subject = headers.get(
            "subject",
            "(no subject)",
        )

        sender = headers.get(
            "from",
            "",
        )

        recipients = headers.get(
            "to",
            "",
        )

        cc = headers.get(
            "cc",
            "",
        )

        date_raw = headers.get(
            "date",
            "",
        )

        content = _extract_body(payload)

        content = _clean_email_text(content)

        if not content:
            logger.debug(
                f"Skipping Gmail message "
                f"{message.get('id')}: empty body."
            )
            return None

        created_at = _parse_email_date(
            date_raw
        )

        message_id = message.get(
            "id",
            "",
        )

        thread_id = message.get(
            "threadId",
            "",
        )

        extra = {
            "provider": "gmail",
            "message_id": message_id,
            "thread_id": thread_id,
            "from": sender,
            "to": recipients,
            "cc": cc,
            "date": date_raw,
            "labels": message.get(
                "labelIds",
                [],
            ),
            "history_id": message.get(
                "historyId"
            ),
            "internal_date": message.get(
                "internalDate"
            ),
        }

        return Document(
            content=content,
            source_type=SourceType.EMAIL,
            source_path=(
                f"gmail://message/{message_id}"
            ),
            title=subject,
            author=sender,
            created_at=created_at,
            modified_at=created_at,
            extra=extra,
        )


def _headers(headers: list[dict]) -> dict[str, str]:
    """Convert Gmail headers to lowercase dictionary."""

    result: dict[str, str] = {}

    for header in headers:
        name = str(
            header.get("name", "")
        ).strip().lower()

        value = str(
            header.get("value", "")
        ).strip()

        if name:
            result[name] = value

    return result


def _extract_body(payload: dict) -> str:
    """
    Extract best available email body.

    Preference:
        text/plain
        text/html
    """

    mime_type = payload.get(
        "mimeType",
        "",
    )

    body = payload.get("body") or {}

    if mime_type == "text/plain":
        return _decode_body(
            body.get("data")
        )

    if mime_type == "text/html":
        html = _decode_body(
            body.get("data")
        )

        return _html_to_text(html)

    parts = payload.get(
        "parts"
    ) or []

    plain_parts: list[str] = []
    html_parts: list[str] = []

    for part in parts:
        part_type = part.get(
            "mimeType",
            "",
        )

        if part_type == "text/plain":

            text = _decode_body(
                (part.get("body") or {}).get(
                    "data"
                )
            )

            if text:
                plain_parts.append(text)

        elif part_type == "text/html":

            html = _decode_body(
                (part.get("body") or {}).get(
                    "data"
                )
            )

            if html:
                html_parts.append(
                    _html_to_text(html)
                )

        elif part.get("parts"):

            nested = _extract_body(part)

            if nested:
                plain_parts.append(nested)

    if plain_parts:
        return "\n\n".join(
            plain_parts
        )

    return "\n\n".join(
        html_parts
    )


def _decode_body(
    data: Optional[str],
) -> str:
    """Decode Gmail URL-safe base64 body."""

    if not data:
        return ""

    try:
        raw = base64.urlsafe_b64decode(
            data + "=" * (-len(data) % 4)
        )

        return raw.decode(
            "utf-8",
            errors="replace",
        )

    except Exception as exc:
        logger.debug(
            f"Could not decode Gmail body: {exc}"
        )

        return ""


def _html_to_text(
    html: str,
) -> str:
    """Convert HTML email to readable text."""

    if not html:
        return ""

    soup = BeautifulSoup(
        html,
        "html.parser",
    )

    for tag in soup(
        ["script", "style", "noscript"]
    ):
        tag.decompose()

    return soup.get_text(
        "\n",
        strip=True,
    )


def _clean_email_text(
    text: str,
) -> str:
    """Normalize email whitespace."""

    text = text.replace(
        "\r\n",
        "\n",
    )

    text = text.replace(
        "\r",
        "\n",
    )

    text = re.sub(
        r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]",
        "",
        text,
    )

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def _parse_email_date(
    value: str,
) -> Optional[datetime]:
    """Parse RFC 2822 Gmail Date header."""

    if not value:
        return None

    try:
        return parsedate_to_datetime(
            value
        )
    except (
        TypeError,
        ValueError,
        IndexError,
    ):
        return None


def _env_int(
    name: str,
    default: int,
    minimum: int = 0,
) -> int:
    value = os.getenv(name)

    if value is None:
        return default

    try:
        parsed = int(value)

        if parsed < minimum:
            raise ValueError

        return parsed

    except ValueError:
        logger.warning(
            f"Invalid {name}={value!r}; "
            f"using default {default}."
        )

        return default


