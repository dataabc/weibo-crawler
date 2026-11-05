"""缺失媒体在线归因探测测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

from util.media import media_probe  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code, content_type="video/quicktime"):
        self.status_code = status_code
        self.headers = {"content-type": content_type}
        self.closed = False

    def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, response=None, exc=None):
        self.response = response
        self.exc = exc

    def get(self, *args, **kwargs):
        if self.exc is not None:
            raise self.exc
        return self.response


def _probe(status_code, content_type="video/quicktime", exc=None, sources=None):
    session = _FakeSession(_FakeResponse(status_code, content_type), exc=exc)
    source_map = sources if sources is not None else {
        "/tmp/media/a.mov": ("1000000004", "https://example.com/a.mov")
    }
    return media_probe.probe_missing_sources(session, {}, ["/tmp/media/a.mov"], source_map)


class MediaProbeTest(unittest.TestCase):
    def test_platform_deleted_statuses(self):
        for status in (404, 410):
            with self.subTest(status=status):
                result = _probe(status)
                self.assertEqual(
                    result["/tmp/media/a.mov"],
                    media_probe.ATTRIBUTION_PLATFORM_DELETED,
                )

    def test_recoverable_statuses(self):
        for status in (200, 206, 302):
            with self.subTest(status=status):
                result = _probe(status)
                self.assertEqual(
                    result["/tmp/media/a.mov"], media_probe.ATTRIBUTION_RECOVERABLE
                )

    def test_restricted_statuses(self):
        for status in (401, 403):
            with self.subTest(status=status):
                result = _probe(status)
                self.assertEqual(
                    result["/tmp/media/a.mov"], media_probe.ATTRIBUTION_RESTRICTED
                )

    def test_html_error_page_is_restricted(self):
        result = _probe(200, content_type="text/html; charset=utf-8")
        self.assertEqual(
            result["/tmp/media/a.mov"], media_probe.ATTRIBUTION_RESTRICTED
        )

    def test_network_failure_is_unknown(self):
        result = _probe(0, exc=OSError("timeout"))
        self.assertEqual(result["/tmp/media/a.mov"], media_probe.ATTRIBUTION_UNKNOWN)

    def test_no_source_record(self):
        result = _probe(404, sources={})
        self.assertEqual(
            result["/tmp/media/a.mov"], media_probe.ATTRIBUTION_NO_SOURCE
        )

    def test_source_lookup_by_absolute_path(self):
        local = os.path.join("relative", "b.mov")
        sources = {os.path.abspath(local): ("123", "https://example.com/b.mov")}
        session = _FakeSession(_FakeResponse(404))
        result = media_probe.probe_missing_sources(session, {}, [local], sources)
        self.assertEqual(
            result[local], media_probe.ATTRIBUTION_PLATFORM_DELETED
        )

    def test_response_is_closed(self):
        response = _FakeResponse(404)
        session = _FakeSession(response)
        media_probe.probe_missing_sources(
            session, {}, ["/tmp/media/a.mov"],
            {"/tmp/media/a.mov": ("1", "https://example.com/a.mov")},
        )
        self.assertTrue(response.closed)


if __name__ == "__main__":
    unittest.main()
