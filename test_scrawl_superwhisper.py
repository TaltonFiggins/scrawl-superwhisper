import unittest

import scrawl_superwhisper as sw


def words(*items):
    return [{"t0": t0, "t1": t1, "text": text} for t0, t1, text in items]


def manifest(apple, said):
    return {
        "transcript": {"source": "speech-analyzer", "model": "SpeechTranscriber en_US",
                       "text": " ".join(w["text"] for w in apple), "segments": [], "words": apple},
        "keyframes": [{"index": i, "t": 0, **({"said": s} if s is not None else {})}
                      for i, s in enumerate(said)],
        "strokes": [], "other": {"kept": True},
    }


APPLE = words((500, 700, "Look"), (700, 900, "at"), (900, 1100, "the"), (1100, 1400, "tail"),
              (1400, 1600, "wind"), (1600, 2000, "class."), (3000, 3300, "Um,"), (3300, 3500, "now"),
              (3500, 3900, "click"), (3900, 4300, "deploy."))
SAID = ["Look at the tail wind class.", "Um, now click deploy."]


class Align(unittest.TestCase):
    def test_lines_up_words_ignoring_case_and_punctuation(self):
        self.assertEqual(sw.align(["Look,", "AT", "Tailwind", "class"], ["look", "at", "tail", "wind", "class."]),
                         [0, 1, None, 4])

    def test_retime_spreads_unmatched_words_between_neighbours(self):
        ours = [{"text": t} for t in ["So", "look", "Tailwind", "now", "please"]]
        apple = words((1000, 1200, "look"), (1200, 1400, "tail"), (1400, 1600, "wind"), (1600, 1800, "now"))
        timed = sw.retime(ours, apple, sw.align([w["text"] for w in ours], [w["text"] for w in apple]))
        self.assertEqual([(w["t0"], w["t1"]) for w in timed],
                         [(750, 1000), (1000, 1200), (1200, 1600), (1600, 1800), (1800, 2050)])


class Owners(unittest.TestCase):
    def test_keyframe_text_maps_back_to_apple_words(self):
        self.assertEqual(sw.owners_of_apple_words(manifest(APPLE, SAID)["keyframes"], APPLE),
                         [0, 0, 0, 0, 0, 0, 1, 1, 1, 1])

    def test_keyframes_without_words_own_nothing(self):
        frames = manifest(APPLE, [SAID[0], None, SAID[1]])["keyframes"]
        self.assertEqual(sw.owners_of_apple_words(frames, APPLE), [0] * 6 + [2] * 4)

    def test_text_that_does_not_match_is_refused(self):
        self.assertIsNone(sw.owners_of_apple_words(manifest(APPLE, ["Look at the", "now click"])["keyframes"], APPLE))
        self.assertIsNone(sw.owners_of_apple_words(manifest(APPLE, ["Something else entirely."])["keyframes"], APPLE))


class Rewrite(unittest.TestCase):
    def test_superwhisper_words_go_to_the_keyframe_of_their_apple_word(self):
        heard = [{"text": t} for t in "Look at the Tailwind class. Now click deploy.".split()]
        out, reason = sw.rewrite(manifest(APPLE, SAID), heard, "sv-1", "S1-Voice (Cloud)")
        self.assertIsNotNone(out, reason)
        self.assertEqual([f.get("said") for f in out["keyframes"]],
                         ["Look at the Tailwind class.", "Now click deploy."])
        t = out["transcript"]
        self.assertEqual(t["source"], "superwhisper:sv-1+speech-analyzer")
        self.assertEqual(t["text"], "Look at the Tailwind class. Now click deploy.")
        self.assertEqual([(w["t0"], w["text"]) for w in t["words"]][:4],
                         [(500, "Look"), (700, "at"), (900, "the"), (1100, "Tailwind")])
        self.assertEqual([s["text"] for s in t["segments"]], ["Look at the Tailwind class.", "Now click deploy."])
        self.assertEqual(out["other"], {"kept": True}, "unknown fields survive")
        self.assertNotIn("estimated", t)

    def test_mostly_different_words_leave_the_capture_alone(self):
        heard = [{"text": t} for t in "completely different words were heard here".split()]
        out, reason = sw.rewrite(manifest(APPLE, SAID), heard, "sv-1", "")
        self.assertIsNone(out)
        self.assertIn("lined up", reason)

    def test_no_apple_words_or_odd_keyframes_leave_it_alone(self):
        heard = [{"text": "Look"}]
        self.assertIsNone(sw.rewrite(manifest([], []), heard, "sv-1", "")[0])
        self.assertIsNone(sw.rewrite(manifest(APPLE, ["nope"]), heard, "sv-1", "")[0])


class Text(unittest.TestCase):
    def test_join_keeps_punctuation_attached(self):
        self.assertEqual(sw.join_words(["Look", ",", "here", "(", "now", ")", "."]), "Look, here (now).")

    def test_sentences_break_at_punctuation_and_long_pauses(self):
        segs = sw.sentences(words((0, 100, "One."), (200, 300, "Two"), (2000, 2100, "three"), (2200, 2300, "four")))
        self.assertEqual([(s["t0"], s["text"]) for s in segs], [(0, "One."), (200, "Two"), (2000, "three four")])


if __name__ == "__main__":
    unittest.main()
