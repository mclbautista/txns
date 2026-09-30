import unittest

from txns.prng import SplitMix64, Streams


class SplitMix64Test(unittest.TestCase):
    def test_reference_vectors(self):
        # Published SplitMix64 outputs for seed 1234567.
        g = SplitMix64(1234567)
        self.assertEqual(
            [g.next_u64() for _ in range(5)],
            [
                6457827717110365317,
                3203168211198807973,
                9817491932198370423,
                4593380528125082431,
                16408922859458223821,
            ],
        )


class StreamsTest(unittest.TestCase):
    def test_named_streams_depend_only_on_seed_and_name(self):
        a = Streams(7)
        b = Streams(7)
        b.stream("noise")  # creating other streams first changes nothing
        x = [a.stream("item", "coffee", "dates").next_u64() for _ in range(1)]
        y = [b.stream("item", "coffee", "dates").next_u64() for _ in range(1)]
        self.assertEqual(x, y)
        self.assertNotEqual(
            Streams(7).stream("item", "coffee", "dates").next_u64(),
            Streams(7).stream("item", "coffee", "text").next_u64(),
        )
        self.assertNotEqual(
            Streams(7).stream("item", "coffee", "dates").next_u64(),
            Streams(8).stream("item", "coffee", "dates").next_u64(),
        )

    def test_draw_helpers_stay_in_range(self):
        s = Streams(1).stream("t")
        self.assertTrue(all(0 <= s.below(3) < 3 for _ in range(500)))
        self.assertTrue(all(5 <= s.randint(5, 6) <= 6 for _ in range(100)))
        self.assertTrue(all(0.0 <= s.uniform() < 1.0 for _ in range(100)))
        self.assertEqual({s.weighted_index([0, 4, 0]) for _ in range(100)}, {1})
        items = list(range(10))
        s.shuffle(items)
        self.assertEqual(sorted(items), list(range(10)))


if __name__ == "__main__":
    unittest.main()
