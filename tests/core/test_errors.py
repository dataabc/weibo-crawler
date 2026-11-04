"""util/core/errors.py 单元测试。"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from util.core import errors  # noqa: E402


class ErrorsTest(unittest.TestCase):
    def test_programming_errors_are_classified(self):
        for exc in (
            NameError("x"),
            UnboundLocalError("x"),
            AttributeError("x"),
            TypeError("x"),
            ImportError("x"),
            SyntaxError("x"),
            RecursionError("x"),
        ):
            self.assertTrue(errors.is_programming_error(exc), type(exc).__name__)

    def test_business_errors_are_not_classified(self):
        for exc in (
            OSError("disk"),
            ValueError("bad json"),
            RuntimeError("network down"),
            KeyError("field"),
        ):
            self.assertFalse(errors.is_programming_error(exc), type(exc).__name__)

    def test_describe_exception(self):
        self.assertEqual(
            errors.describe_exception(AttributeError("no attr")),
            "AttributeError: no attr",
        )


if __name__ == "__main__":
    unittest.main()
