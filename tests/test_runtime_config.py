from __future__ import annotations

from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from automation.runtime_config import (
    DEFAULT_STOCK_READER,
    browser_headless,
    launch_falcon_chromium,
    stock_reader_path,
)
from automation.login import main as login_main


class RuntimeConfigTests(unittest.TestCase):
    def test_browser_defaults_to_headful(self) -> None:
        self.assertFalse(browser_headless({}))

    def test_accepted_true_and_false_spellings(self) -> None:
        for raw in ("1", "true", "TRUE", " yes ", "On"):
            with self.subTest(raw=raw):
                self.assertTrue(browser_headless({"FALCON_BROWSER_HEADLESS": raw}))
        for raw in ("0", "false", "FALSE", " no ", "Off"):
            with self.subTest(raw=raw):
                self.assertFalse(browser_headless({"FALCON_BROWSER_HEADLESS": raw}))

    def test_invalid_browser_mode_fails_clearly(self) -> None:
        with self.assertRaisesRegex(ValueError, "FALCON_BROWSER_HEADLESS"):
            browser_headless({"FALCON_BROWSER_HEADLESS": "sometimes"})

    def test_launch_uses_central_browser_mode(self) -> None:
        playwright = Mock()
        with patch.dict("os.environ", {"FALCON_BROWSER_HEADLESS": "true"}, clear=True):
            result = launch_falcon_chromium(playwright)
        playwright.chromium.launch.assert_called_once_with(headless=True)
        self.assertIs(result, playwright.chromium.launch.return_value)

    def test_stock_reader_default_and_configured_path(self) -> None:
        self.assertEqual(stock_reader_path({}), DEFAULT_STOCK_READER)
        self.assertEqual(
            stock_reader_path({"FALCON_STOCK_READER": "/app/cpp/build/stock_reader"}),
            Path("/app/cpp/build/stock_reader"),
        )

    def test_blank_stock_reader_path_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be blank"):
            stock_reader_path({"FALCON_STOCK_READER": "  "})

    def test_manual_login_remains_headful_when_falcon_is_headless(self) -> None:
        playwright = Mock()
        manager = Mock()
        manager.__enter__ = Mock(return_value=playwright)
        manager.__exit__ = Mock(return_value=False)
        with patch.dict(
            "os.environ", {"FALCON_BROWSER_HEADLESS": "true"}, clear=False
        ), patch("automation.login.sync_playwright", return_value=manager), patch(
            "automation.login.AUTH_STATE"
        ), patch("builtins.input", return_value=""):
            self.assertEqual(login_main(), 0)
        playwright.chromium.launch.assert_called_once_with(headless=False)


if __name__ == "__main__":
    unittest.main()
