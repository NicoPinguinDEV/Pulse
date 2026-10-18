import unittest

from pulse_social_utils import (
    announcement_text,
    best_thumbnail,
    detect_platform,
    is_recent,
    profile_url,
    youtube_video_id,
)


class DetectPlatformTests(unittest.TestCase):
    def test_profile_links(self):
        self.assertEqual(detect_platform("https://www.youtube.com/@marvin")[0], "youtube")
        self.assertEqual(detect_platform("youtube.com/channel/UCabcdefghijklmnopqrstuv")[0], "youtube")
        self.assertEqual(detect_platform("UCabcdefghijklmnopqrstuv")[0], "youtube")
        self.assertEqual(detect_platform("https://www.tiktok.com/@name")[0], "tiktok")
        self.assertEqual(detect_platform("instagram.com/name")[0], "instagram")
        self.assertEqual(detect_platform("https://www.twitch.tv/name")[0], "twitch")
        self.assertEqual(detect_platform("https://x.com/name")[0], "x")
        self.assertEqual(detect_platform("https://m.youtube.com/@marvin")[0], "youtube")
        self.assertEqual(detect_platform("https://example.org/feed.xml")[0], "rss")

    def test_video_links_are_rejected_with_hint(self):
        for link in ("https://youtu.be/dQw4w9WgXcQ", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "https://youtube.com/shorts/dQw4w9WgXcQ"):
            with self.assertRaises(ValueError) as ctx:
                detect_platform(link)
            self.assertIn("Kanal", str(ctx.exception))

    def test_empty_input(self):
        with self.assertRaises(ValueError):
            detect_platform("  ")


class AnnouncementTests(unittest.TestCase):
    def test_youtube_ids_and_thumbnails(self):
        self.assertEqual(youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertEqual(youtube_video_id("https://youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ")
        self.assertIsNone(youtube_video_id("https://www.tiktok.com/@a/video/1"))
        item = {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}
        self.assertEqual(best_thumbnail(item, "youtube"), "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg")
        self.assertEqual(best_thumbnail({"url": "x", "thumbnail": "https://cdn/t.jpg"}, "tiktok"), "https://cdn/t.jpg")
        self.assertEqual(best_thumbnail({"url": "x", "raw_description": '<p><img src="https://cdn/a.png"></p>'}, "x"), "https://cdn/a.png")
        self.assertEqual(best_thumbnail({"url": "x", "thumbnail": "http://insecure/t.jpg"}, "tiktok"), "")

    def test_text_mentions_creator_and_shorts(self):
        text = announcement_text("Marvin", "youtube", "https://youtube.com/shorts/abc", "<@1>")
        self.assertIn("**Marvin** (<@1>)", text)
        self.assertIn("einen neuen Short", text)
        self.assertIn("ein neues TikTok", announcement_text("Finja", "tiktok", "https://tiktok.com/@f/video/1"))

    def test_recency_window(self):
        now = 1_000_000.0
        self.assertTrue(is_recent(0, now))
        self.assertTrue(is_recent(now - 3600, now))
        self.assertFalse(is_recent(now - 4 * 86400, now))

    def test_profile_urls(self):
        self.assertEqual(profile_url("tiktok", "@name"), "https://www.tiktok.com/@name")
        self.assertEqual(
            profile_url("youtube", "x", "https://www.youtube.com/feeds/videos.xml?channel_id=UCabcdefghijklmnopqrstuv"),
            "https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv",
        )


if __name__ == "__main__":
    unittest.main()
