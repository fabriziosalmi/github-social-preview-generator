"""Determinism of the seeded random stream."""

from __future__ import annotations

import unittest

from gspg.seed import Rng, digest


class Determinism(unittest.TestCase):
    def test_the_same_seed_gives_the_same_stream(self):
        first = [Rng("repo", 1).random() for _ in range(5)]
        second = [Rng("repo", 1).random() for _ in range(5)]
        self.assertEqual(first, second)

    def test_a_stream_is_reproducible_across_instances(self):
        one = Rng("repo")
        two = Rng("repo")
        self.assertEqual([one.random() for _ in range(20)], [two.random() for _ in range(20)])

    def test_different_seeds_diverge(self):
        self.assertNotEqual(Rng("a").random(), Rng("b").random())

    def test_seed_parts_cannot_collide_by_concatenation(self):
        self.assertNotEqual(digest("ab", "c"), digest("a", "bc"))

    def test_fork_is_independent_but_reproducible(self):
        parent = Rng("root")
        self.assertEqual(parent.fork("x").random(), Rng("root").fork("x").random())
        self.assertNotEqual(Rng("root").fork("x").random(), Rng("root").fork("y").random())


class Distributions(unittest.TestCase):
    def test_random_stays_in_the_unit_interval(self):
        rng = Rng("range")
        for _ in range(2000):
            value = rng.random()
            self.assertGreaterEqual(value, 0.0)
            self.assertLess(value, 1.0)

    def test_randint_covers_its_inclusive_range(self):
        rng = Rng("ints")
        seen = {rng.randint(0, 4) for _ in range(500)}
        self.assertEqual(seen, {0, 1, 2, 3, 4})

    def test_randint_of_one_value(self):
        self.assertEqual(Rng("one").randint(7, 7), 7)

    def test_randint_rejects_an_empty_range(self):
        with self.assertRaises(ValueError):
            Rng("bad").randint(5, 4)

    def test_shuffled_is_a_permutation(self):
        items = list(range(30))
        shuffled = Rng("shuffle").shuffled(items)
        self.assertEqual(sorted(shuffled), items)
        self.assertNotEqual(shuffled, items)

    def test_choice_rejects_an_empty_sequence(self):
        with self.assertRaises(ValueError):
            Rng("empty").choice([])

    def test_weighted_choice_never_picks_a_zero_weight(self):
        rng = Rng("weights")
        picks = {rng.weighted_choice(["a", "b", "c"], [1.0, 0.0, 1.0]) for _ in range(400)}
        self.assertNotIn("b", picks)

    def test_weighted_choice_validates_its_inputs(self):
        with self.assertRaises(ValueError):
            Rng("w").weighted_choice(["a"], [1.0, 2.0])
        with self.assertRaises(ValueError):
            Rng("w").weighted_choice(["a", "b"], [0.0, 0.0])
