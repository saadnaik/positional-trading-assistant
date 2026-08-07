from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from automation.export_screen import (
    BUILD_YOUR_SCREEN,
    MINERVINI_1_MONTH,
    MINERVINI_5_MONTHS,
    SCREENS,
    ScreenConfig,
    _parse_args,
    configured_output_directory,
    dismiss_screen_information_modal,
    export_locator_candidates,
    timestamped_destination,
)


class ScreenRegistryTests(unittest.TestCase):
    def test_required_screens_are_registered(self) -> None:
        self.assertIn("build-your-screen", SCREENS)
        self.assertIn("minervini", SCREENS)
        self.assertIn("minervini-5-months", SCREENS)

    def test_build_your_screen_configuration(self) -> None:
        self.assertEqual(
            BUILD_YOUR_SCREEN,
            ScreenConfig(
                name="Build Your Screen",
                cli_name="build-your-screen",
                url=(
                    "https://marketsmithindia.com/mstool/list/build-your-screen/"
                    "filter-india-stocks/idealists.jsp#/"
                ),
                output_directory=Path("data/incoming/build_your_screen"),
                expected_filename_hint="Filter_India_Stocks",
                role="primary",
            ),
        )

    def test_minervini_configuration(self) -> None:
        self.assertEqual(
            MINERVINI_1_MONTH,
            ScreenConfig(
                name="Mark Minervini 1-Month",
                cli_name="minervini",
                url=(
                    "https://marketsmithindia.com/mstool/list/marketsmith-stock-screens/"
                    "minervini-trend-template-1-month/idealists.jsp#/"
                ),
                output_directory=Path("data/incoming/minervini_1_month"),
                expected_filename_hint="Minervini_Trend_Template-1_Month",
                role="signal",
            ),
        )

    def test_five_month_minervini_configuration(self) -> None:
        self.assertEqual(
            MINERVINI_5_MONTHS,
            ScreenConfig(
                name="Mark Minervini 5-Month",
                cli_name="minervini-5-months",
                url=(
                    "https://marketsmithindia.com/mstool/list/marketsmith-stock-screens/"
                    "minervini-trend-template-5-months/idealists.jsp#/"
                ),
                output_directory=Path("data/incoming/minervini_5_months"),
                expected_filename_hint="Minervini_Trend_Template-5_Months",
                role="signal",
            ),
        )

    def test_registry_keys_and_cli_names_are_unique_and_consistent(self) -> None:
        keys = list(SCREENS)
        cli_names = [config.cli_name for config in SCREENS.values()]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(len(cli_names), len(set(cli_names)))
        self.assertTrue(
            all(key == config.cli_name for key, config in SCREENS.items())
        )

    def test_every_registered_screen_has_a_supported_role(self) -> None:
        self.assertTrue(
            all(config.role in {"primary", "signal"} for config in SCREENS.values())
        )

    def test_unknown_cli_screen_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            _parse_args(["unknown-screen"])
        self.assertEqual(raised.exception.code, 2)

    def test_missing_cli_screen_is_rejected(self) -> None:
        with self.assertRaises(SystemExit) as raised:
            _parse_args([])
        self.assertEqual(raised.exception.code, 2)


class ExportLocatorTests(unittest.TestCase):
    def test_preferred_locator_is_idea_download_list(self) -> None:
        page = Mock()
        candidates = export_locator_candidates(page)
        self.assertEqual(candidates[0][0], "#ideaDownloadList")
        page.locator.assert_any_call("#ideaDownloadList")

    def test_safe_fallback_locators_remain_available(self) -> None:
        page = Mock()
        descriptions = [
            description for description, _ in export_locator_candidates(page)
        ]
        self.assertEqual(
            descriptions[1:],
            [
                "button role",
                "link role",
                'exact text "Export"',
                'title containing "export"',
                'aria-label containing "export"',
            ],
        )
        _, kwargs = page.get_by_text.call_args
        self.assertTrue(kwargs["exact"])

    def test_visible_screen_information_modal_is_dismissed(self) -> None:
        page = Mock()
        modal = page.locator.return_value.first
        modal.is_visible.return_value = True
        close_control = modal.locator.return_value.first

        dismiss_screen_information_modal(page)

        page.locator.assert_called_once_with(".modalWithArrow.in:visible")
        modal.locator.assert_called_once_with(
            '[data-dismiss="modal"]', has_text="Close"
        )
        close_control.click.assert_called_once_with()
        modal.wait_for.assert_called_once_with(state="hidden", timeout=10_000)

    def test_absent_screen_information_modal_is_ignored(self) -> None:
        page = Mock()
        page.locator.return_value.first.is_visible.return_value = False

        dismiss_screen_information_modal(page)

        page.locator.return_value.first.locator.assert_not_called()


class DestinationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.project_root = Path(self.temporary_directory.name)
        self.config = ScreenConfig(
            name="Test Screen",
            cli_name="test-screen",
            url="https://marketsmithindia.com/mstool/list/test/idealists.jsp#/",
            output_directory=Path("data/incoming/test_screen"),
            expected_filename_hint="Expected_Screen",
            role="signal",
        )

    def destination(self) -> Path:
        fixed_time = datetime(2026, 8, 5, 12, 34, 56, tzinfo=timezone.utc)
        with patch("automation.export_screen.PROJECT_ROOT", self.project_root):
            return timestamped_destination(
                self.config,
                "Expected_Screen.csv",
                now=fixed_time,
            )

    def test_timestamped_destination_preserves_suggested_filename(self) -> None:
        self.assertEqual(
            self.destination().name,
            "20260805T123456Z_Expected_Screen.csv",
        )

    def test_output_directory_is_created(self) -> None:
        destination = self.destination()
        self.assertTrue(destination.parent.is_dir())

    def test_existing_destination_is_not_overwritten(self) -> None:
        destination = self.destination()
        destination.touch()
        with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
            self.destination()

    def test_shared_incoming_root_is_rejected(self) -> None:
        unsafe_config = ScreenConfig(
            name="Unsafe",
            cli_name="unsafe",
            url=self.config.url,
            output_directory=Path("data/incoming"),
            expected_filename_hint="Unsafe",
            role="signal",
        )
        with patch("automation.export_screen.PROJECT_ROOT", self.project_root):
            with self.assertRaisesRegex(ValueError, "dedicated directory"):
                configured_output_directory(unsafe_config)

    def test_configured_destinations_are_not_the_shared_root(self) -> None:
        with patch("automation.export_screen.PROJECT_ROOT", self.project_root):
            shared_root = (self.project_root / "data/incoming").resolve()
            for config in SCREENS.values():
                self.assertNotEqual(configured_output_directory(config), shared_root)


if __name__ == "__main__":
    unittest.main()
