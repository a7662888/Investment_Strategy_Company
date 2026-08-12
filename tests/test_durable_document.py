# -*- coding: utf-8 -*-
import base64
import json
import unittest
from unittest.mock import patch

from company.model import durable_document


class _Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class DurableDocumentTests(unittest.TestCase):
    def test_reads_small_contents_api_document(self):
        doc = {"stocks": {"1216": {"complete": True}}}
        encoded = base64.b64encode(json.dumps(doc).encode()).decode()
        payload = {"sha": "small-sha", "encoding": "base64", "content": encoded}
        with patch.object(durable_document.urllib.request, "urlopen", return_value=_Response(payload)) as opened:
            actual, sha, error = durable_document._remote_get("value/test.json", ("token", "o/r", "main"))
        self.assertEqual(actual, doc)
        self.assertEqual(sha, "small-sha")
        self.assertIsNone(error)
        self.assertEqual(opened.call_count, 1)

    def test_large_contents_api_document_falls_back_to_blob(self):
        doc = {"stocks": {"1216": {"complete": True}}, "size": "over-1MiB"}
        encoded = base64.b64encode(json.dumps(doc).encode()).decode()
        metadata = {
            "sha": "large-sha", "encoding": "none", "content": "",
            "git_url": "https://api.github.com/repos/o/r/git/blobs/large-sha",
        }
        blob = {"sha": "large-sha", "encoding": "base64", "content": encoded}
        with patch.object(
            durable_document.urllib.request, "urlopen",
            side_effect=[_Response(metadata), _Response(blob)],
        ) as opened:
            actual, sha, error = durable_document._remote_get("value/test.json", ("token", "o/r", "main"))
        self.assertEqual(actual, doc)
        self.assertEqual(sha, "large-sha")
        self.assertIsNone(error)
        self.assertEqual(opened.call_count, 2)
        self.assertIn("/git/blobs/large-sha", opened.call_args_list[1].args[0].full_url)


if __name__ == "__main__":
    unittest.main()
