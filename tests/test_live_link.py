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


class Moves(unittest.TestCase):
    def setUp(self):
        live.reset()

    def test_only_a_poll_that_says_so_takes_moves(self):
        live.note_poll("/latest-model-info?live=1", now=100.0)
        self.assertFalse(live.takes_moves())
        live.note_poll("/latest-model-info?live=1&moves=1", now=100.0)
        self.assertTrue(live.takes_moves())
        live.note_poll("/latest-model-info?live=1", now=100.0)
        self.assertFalse(live.takes_moves())

    def test_the_number_moves_only_when_the_table_does(self):
        start = live.moves_seq()
        live.publish_moves({"Cube": [1.0] * 16})
        moved = live.moves_seq()
        self.assertEqual(moved, start + 1)
        live.publish_moves({"Cube": [1.0] * 16})
        self.assertEqual(live.moves_seq(), moved)
        live.publish_moves({"Cube": [2.0] * 16})
        self.assertEqual(live.moves_seq(), moved + 1)

    def test_a_reader_gets_the_table_with_its_number(self):
        live.publish_moves({"Cube": [1.0] * 16})
        self.assertEqual(live.moves(), {"seq": live.moves_seq(), "objects": {"Cube": [1.0] * 16}})


class Pacing(unittest.TestCase):
    def setUp(self):
        self.pacer = live.SendPacer()

    def test_nothing_is_due_without_a_change(self):
        self.assertFalse(self.pacer.due(100.0))

    def test_a_change_waits_for_quiet(self):
        self.pacer.note_change(100.0)
        self.assertFalse(self.pacer.due(100.0 + live.QUIET_S - 0.01))
        self.assertTrue(self.pacer.due(100.0 + live.QUIET_S))

    def test_a_burst_is_one_send(self):
        for t in (100.0, 100.05, 100.1):
            self.pacer.note_change(t)
        self.assertFalse(self.pacer.due(100.1 + live.QUIET_S - 0.01))
        self.assertTrue(self.pacer.due(100.1 + live.QUIET_S))
        self.pacer.note_sent(100.3, 0.0)
        self.assertFalse(self.pacer.pending)
        self.assertFalse(self.pacer.due(101.0))

    def test_a_stream_that_never_goes_quiet_still_sends(self):
        t = 100.0
        while t < 100.0 + live.MAX_HOLD_S:
            self.pacer.note_change(t)
            self.assertFalse(self.pacer.due(t))
            t += 0.05
        self.pacer.note_change(t)
        self.assertTrue(self.pacer.due(t))

    def test_a_slow_export_leaves_blender_as_much_time_again(self):
        self.pacer.note_sent(100.0, 2.0)
        self.pacer.note_change(100.1)
        self.assertFalse(self.pacer.due(101.9))
        self.assertTrue(self.pacer.due(102.0))

    def test_a_change_after_a_send_is_pending_again(self):
        self.pacer.note_change(100.0)
        self.pacer.note_sent(100.2, 0.0)
        self.pacer.note_change(100.3)
        self.assertTrue(self.pacer.pending)


if __name__ == "__main__":
    unittest.main()
