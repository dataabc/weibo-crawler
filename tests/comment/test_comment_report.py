'util/comment_report.py 单元测试。'

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.comment import comment_report  # noqa: E402


class CommentReportTest(unittest.TestCase):
    def test_render_statistics(self):
        results = [
            {
                "weibo_id": "100",
                "status": "已写入",
                "roots": 2,
                "replies": 1,
                "total_number": 5,
                "requests": 1,
                "errors": 0,
                "note": "",
            },
            {
                "weibo_id": "200",
                "status": "失败",
                "roots": 0,
                "replies": 0,
                "total_number": 3,
                "requests": 2,
                "errors": 1,
                "note": "抓取失败: timeout",
                "retried": True,
            },
        ]
        text = comment_report.render_comment_summary(results, "测试用户", "123")
        self.assertIn("评论下载情况报告", text)
        self.assertIn("已写入评论块的微博数: 1；未变化: 0", text)
        self.assertIn("已存档评论: 2 条一级 + 1 条二级回复", text)
        self.assertIn("失败微博数: 1", text)
        self.assertIn("微博id: 100", text)
        self.assertIn("微博id: 200", text)

    def test_render_empty_results(self):
        text = comment_report.render_comment_summary([], "测试用户", "123")
        self.assertIn("本轮处理微博数: 0", text)
