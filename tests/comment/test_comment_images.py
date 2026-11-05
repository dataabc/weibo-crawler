'util/comment_images.py 单元测试。'

import os
import sys
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.comment import comment_images  # noqa: E402


class CommentImagesTest(unittest.TestCase):
    def test_iter_comments_flattens_replies(self):
        root = SimpleNamespace(id="r1", replies=[SimpleNamespace(id="x1")])
        comments = comment_images.iter_comments([root])
        self.assertEqual([c.id for c in comments], ["r1", "x1"])

    def test_image_target_naming(self):
        comment = SimpleNamespace(
            id="999", pic_url="https://x/a.jpg",
            created_at="2024-05-03 10:00:00",
        )
        file_name, path = comment_images.image_target("/img", comment)
        self.assertEqual(file_name, "2024-05-03_10-00-00.jpg")
        self.assertEqual(path, os.path.join("/img", file_name))

    def test_image_target_without_pic(self):
        comment = SimpleNamespace(id="1", pic_url="", created_at="")
        self.assertIsNone(comment_images.image_target("/img", comment))
