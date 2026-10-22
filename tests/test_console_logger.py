import ctypes
import io
import unittest
from unittest.mock import Mock, patch

from whisperer.console_logger import ConsoleLogger, _disable_quick_edit


class ConsoleLoggerTests(unittest.TestCase):
    def test_quick_edit_is_disabled_without_changing_other_console_flags(self):
        kernel = Mock()
        kernel.GetStdHandle.return_value = 123

        def get_mode(handle, pointer):
            pointer._obj.value = 0x0040 | 0x0001 | 0x0002
            return True

        kernel.GetConsoleMode.side_effect = get_mode
        with patch("whisperer.console_logger.sys.platform", "win32"), patch.object(
            ctypes, "WinDLL", return_value=kernel, create=True
        ):
            _disable_quick_edit()
        kernel.SetConsoleMode.assert_called_once_with(123, 0x0080 | 0x0001 | 0x0002)

    def test_redirected_output_does_not_change_console_mode(self):
        with patch("whisperer.console_logger._disable_quick_edit") as disable:
            logger = ConsoleLogger(io.StringIO())
            logger.info("timing", "total=3.0s")
        disable.assert_not_called()


if __name__ == "__main__":
    unittest.main()
