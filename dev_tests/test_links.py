"""Help > Community & feedback: only the app's own links can be opened."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import fischui  # noqa: E402


class LinkTests(unittest.TestCase):
    def test_known_links_only(self):
        api = fischui.Api.__new__(fischui.Api)
        with patch("webbrowser.open") as op:
            self.assertTrue(api.open_link("discord")["ok"])
            op.assert_called_with(fischui.DISCORD_URL)
            self.assertTrue(api.open_link("issues")["ok"])
            op.assert_called_with(fischui.REPO_URL + "/issues")
            self.assertFalse(api.open_link("https://example.com")["ok"])
            self.assertEqual(op.call_count, 2)
        self.assertTrue(fischui.DISCORD_URL.startswith("https://discord.gg/"))


if __name__ == "__main__":
    unittest.main()
