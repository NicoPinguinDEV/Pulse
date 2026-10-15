"""Regression tests for career-specific, rulebook-heavy application tests."""
from __future__ import annotations

import re
import unittest

from career_applications import APPLICATION_CAREERS, grade_career_test, render_career_test


EXPECTED = {
    "low_team": {"count": 12, "pass_percent": 75, "minimum_correct": 9},
    "high_team": {"count": 22, "pass_percent": 82, "minimum_correct": 18},
    "leadership": {"count": 30, "pass_percent": 90, "minimum_correct": 27},
}


class CareerApplicationTests(unittest.TestCase):
    def test_exact_question_counts_and_difficulty_thresholds(self):
        self.assertEqual(set(APPLICATION_CAREERS), set(EXPECTED))
        for career_path, expected in EXPECTED.items():
            with self.subTest(career=career_path):
                career = APPLICATION_CAREERS[career_path]
                self.assertEqual(len(career["questions"]), expected["count"])
                self.assertEqual(career["pass_percent"], expected["pass_percent"])
                self.assertTrue(all(len(q["options"]) == 4 for q in career["questions"]))
                self.assertTrue(all(q["correct"] in q["options"] for q in career["questions"]))
                self.assertTrue(all(q.get("topic") and q.get("rule_refs") for q in career["questions"]))

    def test_all_correct_answers_pass(self):
        for career_path, career in APPLICATION_CAREERS.items():
            with self.subTest(career=career_path):
                result = grade_career_test(career_path, [q["correct"] for q in career["questions"]])
                self.assertEqual(result["total"], len(career["questions"]))
                self.assertEqual(result["score"], result["total"])
                self.assertEqual(result["percent"], 100)
                self.assertTrue(result["passed"])

    def test_cutoff_and_one_below_cutoff(self):
        for career_path, expected in EXPECTED.items():
            questions = APPLICATION_CAREERS[career_path]["questions"]
            minimum = expected["minimum_correct"]

            def answers_with_correct_count(correct_count: int) -> list[str]:
                answers = []
                for index, question in enumerate(questions):
                    if index < correct_count:
                        answers.append(question["correct"])
                    else:
                        answers.append(next(key for key in question["options"] if key != question["correct"]))
                return answers

            with self.subTest(career=career_path, score="cutoff"):
                result = grade_career_test(career_path, answers_with_correct_count(minimum))
                self.assertTrue(result["passed"])
                self.assertEqual(result["score"], minimum)
                self.assertGreaterEqual(result["percent"], expected["pass_percent"])
            with self.subTest(career=career_path, score="below_cutoff"):
                result = grade_career_test(career_path, answers_with_correct_count(minimum - 1))
                self.assertFalse(result["passed"])

    def test_every_rendered_test_contains_all_required_fields(self):
        for career_path, expected in EXPECTED.items():
            with self.subTest(career=career_path):
                rendered = render_career_test(career_path)
                self.assertEqual(rendered.count("<fieldset"), expected["count"])
                self.assertEqual(len(re.findall(r'name="answer_\d+"', rendered)), expected["count"] * 4)
                self.assertNotIn("data-correct", rendered)
                self.assertNotIn("correct_answer", rendered)

    def test_invalid_or_incomplete_answers_are_rejected(self):
        with self.assertRaises(ValueError):
            grade_career_test("unknown", [])
        with self.assertRaises(ValueError):
            grade_career_test("low_team", ["a"] * 11)
        with self.assertRaises(ValueError):
            grade_career_test("low_team", ["invalid"] + ["a"] * 11)

    def test_rulebook_scenarios_cover_both_rulebooks(self):
        for career_path, career in APPLICATION_CAREERS.items():
            refs = " ".join(question["rule_refs"] for question in career["questions"])
            topics = " ".join(question["topic"].lower() for question in career["questions"])
            with self.subTest(career=career_path):
                self.assertIn("Discord-Regelwerk", refs)
                self.assertTrue(any(term in topics for term in ("metagaming", "nlr", "safezone", "geiselnahme")))
                self.assertTrue(any(term in topics for term in ("doxing", "datenschutz", "accountschutz", "vertrauliche")))


if __name__ == "__main__":
    unittest.main()
