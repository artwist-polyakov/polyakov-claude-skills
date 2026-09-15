"""В вывод и журнал попадает отпечаток файла, исходный запрос сохраняется."""

import base64
import copy
import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from payload import compact_payload


class PayloadTests(unittest.TestCase):
    def test_feed_request_and_changes_hide_data_without_mutation(self):
        data = b'<yml_catalog><shop/></yml_catalog>'
        encoded = base64.b64encode(data).decode()
        original = {"Feeds": [{"FileFeed": {"Data": encoded, "Filename": "feed.xml"}}],
                    "FileFeed.Data": encoded, "ImageData": encoded,
                    "Data": "ordinary text"}
        snapshot = copy.deepcopy(original)
        result = compact_payload(original)
        expected = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.assertEqual(result["Feeds"][0]["FileFeed"]["Data"], expected)
        self.assertEqual(result["FileFeed.Data"], expected)
        self.assertEqual(result["ImageData"], expected)
        self.assertNotIn(encoded, str(result))
        self.assertEqual(result["Data"], "ordinary text")
        self.assertEqual(original, snapshot)

    def test_invalid_feed_data_is_not_echoed(self):
        result = compact_payload({"FileFeed": {"Data": "not base64!"}})
        self.assertIn("error", result["FileFeed"]["Data"])
        self.assertNotIn("not base64!", str(result))


if __name__ == "__main__":
    unittest.main()
