import unittest

from shelf.links import kind_of


class KindOf(unittest.TestCase):
    def test_a_watch_page_is_a_video(self):
        self.assertEqual(kind_of("https://www.clips.example/watch?v=Qk3vT9aLm2x"), "video")

    def test_a_shorts_page_is_a_short(self):
        self.assertEqual(kind_of("https://clips.example/shorts/Zp8dW1nRb4c"), "short")

    def test_a_live_page_is_a_live_stream(self):
        self.assertEqual(kind_of("https://www.clips.example/live/Mn4bC8vKt1z"), "live")


if __name__ == "__main__":
    unittest.main()
