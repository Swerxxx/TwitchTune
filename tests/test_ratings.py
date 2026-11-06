import itertools
import sqlite3
import sys
import tempfile
import unittest
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import QueueStore
from ratings import CRITERIA, calculate, details, score_label, summary


class RatingsTests(unittest.TestCase):
    def test_common_average_counts_only_rated_requests_and_recalculates_after_deletion(self):
        store=QueueStore(':memory:')
        try:
            first=store.add('https://youtu.be/one','viewer')
            second=store.add('https://youtu.be/two','viewer')
            store.add('https://youtu.be/unrated','viewer')
            store.rate_criteria(first,dict(text=1,beat=1,flow=1,vibe=1,mix=1))
            store.rate_criteria(second,dict(text=6,beat=6,flow=6,vibe=6,mix=6))
            self.assertEqual((store.profiles()[0]['rated'],store.profiles()[0]['average66']),(2,38.5))
            store.delete(first)
            self.assertEqual(store.profiles()[0]['average66'],66)
            store.rate(second,1)
            self.assertEqual(store.profiles()[0]['average66'],11)
            store.rate(second,10)
            self.assertEqual(store.profiles()[0]['average66'],66)
            store.delete(second)
            self.assertIsNone(store.profiles()[0]['average66'])
        finally:store.db.close()

    def test_all_combinations_use_equal_weights_and_half_up_rounding(self):
        for enabled in (False, True):
            keys = [key for key, _ in CRITERIA if key != 'text' or enabled]
            for values in itertools.product(range(1, 7), repeat=len(keys)):
                exact = Fraction(66 * sum(values), 6 * len(keys))
                expected = int(exact + Fraction(1, 2))
                self.assertEqual(calculate(dict(zip(keys, values)), enabled), expected)
            for value, expected in ((1, 11), (4, 44), (6, 66)):
                self.assertEqual(calculate(dict.fromkeys(keys, value), enabled), expected)
        self.assertEqual(calculate(dict(text=6, beat=6, flow=4, vibe=1, mix=1), False), 33)
        self.assertEqual(calculate(dict(beat=6, flow=6, vibe=3, mix=3), False), 50)

    def test_missing_invalid_and_excluded_scores(self):
        scores = dict.fromkeys(dict(CRITERIA), 6)
        for bad in (None, True, 0, 7, 2.5, '6'):
            with self.assertRaises(ValueError):
                calculate(dict(scores, beat=bad))
        with self.assertRaises(ValueError):
            calculate(dict(beat=6, flow=6, vibe=6, mix=6))
        self.assertEqual(calculate(dict(beat=6, flow=6, vibe=6, mix=6), False), 66)

    def test_migration_preserves_legacy_and_new_reviews_survive_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'queue.sqlite3'
            store = QueueStore(path)
            old = store.add('https://youtu.be/old', 'viewer')
            new = store.add('https://youtu.be/new', 'viewer')
            store.rate(old, 9, 'старый отзыв')
            store.db.close()
            # Simulate a database from before this update.
            db = sqlite3.connect(path)
            db.execute('ALTER TABLE queue DROP COLUMN rating_scale')
            db.execute('ALTER TABLE queue DROP COLUMN rating_details')
            db.commit()
            db.close()
            store = QueueStore(path)
            self.assertEqual(score_label(store.get(old)), '9/10')
            scores = dict(text=2, beat=6, flow=6, vibe=6, mix=6)
            store.rate_criteria(new, scores, False, 'новый отзыв')
            store.update(new, status='Прослушано')
            store.db.close()
            store = QueueStore(path)
            row = store.get(new)
            self.assertEqual(score_label(row), '66/66')
            self.assertEqual(row['comment'], 'новый отзыв')
            self.assertEqual(details(row)['scores'], scores)
            self.assertFalse(details(row)['text_enabled'])
            self.assertIn('Текст: не оценивается', summary(row))
            profile = store.profiles()[0]
            self.assertEqual((profile['average'], profile['rated']), (9, 2))
            self.assertAlmostEqual(profile['average66'], ((11+8*55/9)+66)/2)
            store.rate_criteria(new, dict.fromkeys(scores, 4), True)
            self.assertAlmostEqual(store.profiles()[0]['average66'], ((11+8*55/9)+44)/2)
            with self.assertRaises(ValueError):
                store.rate_criteria(new, scores, False, 'x' * 2001)
            with self.assertRaises(ValueError):
                store.rate_criteria('deleted-id', scores, False)
            store.rate(new, 7)
            self.assertIsNone(details(store.get(new)))
            self.assertAlmostEqual(store.profiles()[0]['average66'], ((11+8*55/9)+(11+6*55/9))/2)
            store.db.close()
