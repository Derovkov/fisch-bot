"""FischBot.pyw: no-console launcher (missing packages -> message box, one copy
at a time) and the app icon. Opens no window and shows no message box."""
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_launcher():
    spec = importlib.util.spec_from_file_location("fischbot_launcher", ROOT / "FischBot.pyw")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.out, self.err = sys.stdout, sys.stderr
        self.addCleanup(self.restore)

    def restore(self):
        sys.stdout, sys.stderr = self.out, self.err

    def test_missing_package_explains_install(self):
        L = load_launcher()
        shown = []
        with patch.object(L, "message", side_effect=lambda t, icon=0x10: shown.append(t)), \
                patch.object(L, "already_running", return_value=False), \
                patch.dict(sys.modules, {"fischui": None}):
            L.main()
        self.restore()
        self.assertEqual(len(shown), 1)
        self.assertIn("Install.bat", shown[0])

    def test_second_copy_raises_the_first(self):
        L = load_launcher()
        with patch("ctypes.windll.kernel32.CreateMutexW", return_value=1), \
                patch("ctypes.windll.kernel32.GetLastError", return_value=183), \
                patch("ctypes.windll.user32.FindWindowW", return_value=0):
            self.assertTrue(L.already_running())
        with patch("ctypes.windll.kernel32.CreateMutexW", return_value=1), \
                patch("ctypes.windll.kernel32.GetLastError", return_value=0):
            self.assertFalse(L.already_running())

    def test_icon_file_has_every_size(self):
        import struct
        data = (ROOT / "ui" / "icons" / "app" / "fischbot.ico").read_bytes()
        n = struct.unpack("<HHH", data[:6])[2]
        sizes = {data[6 + 16 * i] or 256 for i in range(n)}
        self.assertTrue({16, 32, 48, 256} <= sizes, sizes)


if __name__ == "__main__":
    unittest.main()
