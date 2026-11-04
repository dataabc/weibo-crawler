"""媒体文件名规则测试：下载侧与 Markdown 链接侧必须同名。"""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tests import _support  # noqa: E402

_support.bootstrap()

import weibo  # noqa: E402
from util.media import media_jobs  # noqa: E402


def _new_wb():
    """构造只带下载替身的 wb（不跑 __init__、不联网）。"""
    wb = weibo.Weibo.__new__(weibo.Weibo)
    wb.downloaded = []

    def _download_one_file(url, file_path, file_type, weibo_id, created_at):
        wb.downloaded.append((url, os.path.basename(file_path)))
        return file_path

    wb.download_one_file = _download_one_file
    return wb


def _weibo(**overrides):
    data = {
        "id": "5000000000000001",
        "created_at": "2024-01-02 03:04:05",
        "pics": "",
    }
    data.update(overrides)
    return data


class MediaNamingTest(unittest.TestCase):
    """下载落盘名与 markdown 链接名必须由同一规则产生。"""

    def setUp(self):
        # handle_download 在 video/live_photo 多文件间会 sleep(1,3)：测试屏蔽
        self._sleep_patch = mock.patch("util.media.media_jobs.sleep")
        self._sleep_patch.start()
        self.addCleanup(self._sleep_patch.stop)

    def _assert_download_matches_links(self, wb, file_type, urls, w):
        media_jobs.handle_download(wb, file_type, "/tmp", urls, w)
        links = media_jobs.get_download_file_names(wb, file_type, urls, w)
        saved = [name for _url, name in wb.downloaded]
        self.assertEqual(saved, links)
        return links

    def test_video_mixed_extensions_download_and_link_match(self):
        ''
        wb = _new_wb()
        w = _weibo(video_url="https://x/a.mov;https://x/b.mp4")

        links = self._assert_download_matches_links(wb, "video", w["video_url"], w)

        self.assertEqual(len(links), 2)
        self.assertTrue(links[0].endswith("_1.mov"))
        self.assertTrue(links[1].endswith("_2.mp4"))

    def test_video_single_url(self):
        for url, suffix in (("https://x/a.mov", ".mov"), ("https://x/a.mp4", ".mp4")):
            wb = _new_wb()
            w = _weibo(video_url=url)
            links = self._assert_download_matches_links(wb, "video", url, w)
            self.assertEqual(len(links), 1)
            self.assertTrue(links[0].endswith(suffix))

    def test_live_photo_multi_urls(self):
        wb = _new_wb()
        w = _weibo(
            live_photo_url="https://x/a.mov;https://x/b.mp4",
            pics="p1,p2",
            live_photo_indices=[1, 2],
        )

        links = self._assert_download_matches_links(
            wb, "live_photo", w["live_photo_url"], w
        )

        self.assertEqual(len(links), 2)
        self.assertTrue(links[0].endswith("_1.mov"))
        self.assertTrue(links[1].endswith("_2.mp4"))

    def test_live_photo_single_url_uses_index_with_multiple_pics(self):
        wb = _new_wb()
        w = _weibo(
            live_photo_url="https://x/a.mov",
            pics="p1,p2",
            live_photo_indices=[3],
        )
        links = self._assert_download_matches_links(
            wb, "live_photo", w["live_photo_url"], w
        )
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].endswith("_3.mov"))

    def test_image_single_jpeg_uses_jpg_suffix_on_both_sides(self):
        """单图 .jpeg：旧下载侧用 ``>5`` 落盘 .jpeg、链接侧 .jpg；现统一 .jpg。"""
        wb = _new_wb()
        w = _weibo(pics="https://x/a.jpeg")
        links = self._assert_download_matches_links(wb, "img", w["pics"], w)
        self.assertEqual(len(links), 1)
        self.assertTrue(links[0].endswith(".jpg"))

    def test_image_multiple_urls_numbered(self):
        wb = _new_wb()
        w = _weibo(pics="https://x/a.jpg,https://x/b.png")
        links = self._assert_download_matches_links(wb, "img", w["pics"], w)
        self.assertEqual(len(links), 2)
        self.assertTrue(links[0].endswith("_1.jpg"))
        self.assertTrue(links[1].endswith("_2.png"))

    def test_empty_urls_produce_no_download(self):
        wb = _new_wb()
        w = _weibo()
        self.assertEqual(media_jobs.get_download_file_names(wb, "img", "", w), [])
        media_jobs.handle_download(wb, "img", "/tmp", "", w)
        self.assertEqual(wb.downloaded, [])


if __name__ == "__main__":
    unittest.main()
