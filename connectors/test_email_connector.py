import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from connectors.base import Settings, SourceConfig
from connectors.email import EmailConnector, _html_to_text
from storage.metadata_store import MetadataStore


class _Request:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return self.response


class _GmailService:
    def __init__(self, message_ids):
        self.message_ids = message_ids
        self.requested_ids = []

    def users(self):
        return self

    def messages(self):
        return self

    def list(self, **kwargs):
        return _Request(
            {"messages": [{"id": message_id} for message_id in self.message_ids]}
        )

    def get(self, id, **kwargs):
        self.requested_ids.append(id)
        body = base64.urlsafe_b64encode(
            f"Body for {id}".encode("utf-8")
        ).decode("ascii").rstrip("=")
        return _Request(
            {
                "id": id,
                "threadId": f"thread-{id}",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "Subject", "value": f"Subject {id}"},
                        {"name": "From", "value": "sender@example.com"},
                    ],
                    "body": {"data": body},
                },
            }
        )


class EmailConnectorTests(unittest.TestCase):
    @patch("connectors.email.BeautifulSoup", None)
    def test_html_body_fallback_strips_non_content_tags(self):
        text = _html_to_text(
            "<script>hidden()</script><style>.hidden {}</style>"
            "<noscript>disabled</noscript><p>Visible text</p>"
        )

        self.assertEqual(text, "Visible text")

    @patch.dict(os.environ, {}, clear=True)
    def test_fetch_respects_configured_total_limit(self):
        settings = Settings(
            sources={
                "email": SourceConfig(
                    enabled=True,
                    query="newer_than:1y",
                    max_results=2,
                )
            }
        )
        connector = EmailConnector(settings)
        service = _GmailService(["first", "second", "third"])
        connector._service = service

        documents = list(connector.fetch())

        self.assertEqual(connector.query, "newer_than:1y")
        self.assertEqual(service.requested_ids, ["first", "second"])
        self.assertEqual(len(documents), 2)
        self.assertIn("Subject: Subject first", documents[0].content)
        self.assertIn("sender@example.com", documents[0].content)
        self.assertIn("Body for first", documents[0].content)
        self.assertEqual(
            documents[0].source_path,
            "gmail://message/first",
        )

    def test_metadata_tracks_remote_document_by_exact_uri_and_content_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            settings = Settings(
                metadata_store={"path": str(Path(temp_dir) / "metadata.db")}
            )
            store = MetadataStore(settings)
            uri = "gmail://message/message-123"

            self.assertTrue(store.needs_document_indexing(uri, "first body"))
            store.record_document(uri, "doc-1", "email", "first body")
            self.assertFalse(store.needs_document_indexing(uri, "first body"))
            self.assertTrue(store.needs_document_indexing(uri, "updated body"))
            self.assertEqual(store.get_doc_id(uri), "doc-1")

            store.close()


if __name__ == "__main__":
    unittest.main()
