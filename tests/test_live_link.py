"""When a send is exported for the live editor: services/live_link.py.

Run with plain Python, no Blender:

    python3 -m unittest discover -s tests -p 'test_*.py'
"""

import unittest

from _load import load_module

live = load_module("services/live_link.py")


class LivePoll(unittest.TestCase):
    def setUp(self):
        live.reset()

    def test_only_live_polls_count(self):
        for path in ("/latest-model-info", "/latest-model-info?live=0", "/latest-model-info?live=true",
                     "/latest-model-info?x=1", "/latest-model"):
            with self.subTest(path):
                self.assertFalse(live.is_live_poll(path))
        self.assertTrue(live.is_live_poll("/latest-model-info?live=1"))
        self.assertTrue(live.is_live_poll("/latest-model-info?a=b&live=1"))

    def test_not_live_until_polled(self):
        self.assertFalse(live.is_live(now=100.0))

    def test_live_within_the_window(self):
        live.note_poll("/latest-model-info?live=1", now=100.0)
        self.assertTrue(live.is_live(now=100.0))
        self.assertTrue(live.is_live(now=100.0 + live.LIVE_WINDOW_S))
        self.assertFalse(live.is_live(now=100.0 + live.LIVE_WINDOW_S + 0.01))

    def test_upload_window_polls_do_not_make_it_live(self):
        live.note_poll("/latest-model-info", now=100.0)
        self.assertFalse(live.is_live(now=100.0))

    def test_reset_forgets(self):
        live.note_poll("/latest-model-info?live=1", now=100.0)
        live.reset()
        self.assertFalse(live.is_live(now=100.0))


if __name__ == "__main__":
    unittest.main()
