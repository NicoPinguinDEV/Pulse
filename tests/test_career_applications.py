"""Tests for the selectable online career application assessments."""

import unittest

from career_applications import (
    APPLICATION_CAREERS,
    grade_career_test,
    render_career_test,
)


class CareerApplicationTests(unittest.TestCase):
    def test_every_career_has_six_questions_and_expected_pass_threshold(self):
        expected = {
            "low_team": 50,
            "high_team": 67,
            "leadership": 83,
        }
        self.assertEqual(set(APPLICATION_CAREERS), set(expected))
        for key, threshold in expected.items():
            self.assertEqual(len(APPLICATION_CAREERS[key]["questions"]), 6)
            self.assertEqual(APPLICATION_CAREERS[key]["pass_percent"], threshold)

    def test_all_correct_answers_receive_full_score(self):
        for path, career in APPLICATION_CAREERS.items():
            answers = [question["correct"] for question in career["questions"]]
            result = grade_career_test(path, answers)
            self.assertEqual(result["score"], 6)
            self.assertEqual(result["total"], 6)
            self.assertEqual(result["percent"], 100)
            self.assertTrue(result["passed"])
            self.assertEqual(result["career_path"], path)
            self.assertEqual(len(result["details"]), 6)

    def test_wrong_answers_are_counted_on_server(self):
        career = APPLICATION_CAREERS["low_team"]
        answers = [question["correct"] for question in career["questions"]]
        first_question = career["questions"][0]
        answers[0] = next(key for key in first_question["options"] if key != first_question["correct"])
        result = grade_career_test("low_team", answers)
        self.assertEqual(result["score"], 5)
        self.assertEqual(result["percent"], 83)
        self.assertTrue(result["details"][0]["correct"] is False)

    def test_missing_or_invalid_answers_are_rejected(self):
        with self.assertRaises(ValueError):
            grade_career_test("low_team", ["a"])
        with self.assertRaises(ValueError):
            grade_career_test("low_team", ["?"] + ["a"] * 5)
        with self.assertRaises(ValueError):
            grade_career_test("unknown", ["a"] * 6)

    def test_rendered_questions_do_not_include_answer_key(self):
        markup = render_career_test("leadership")
        self.assertIn('name="answer_1"', markup)
        self.assertIn('name="answer_6"', markup)
        self.assertNotIn('"correct":', markup)
        self.assertNotIn('"correct"', markup)

    def test_unknown_career_renders_no_questions(self):
        self.assertEqual(render_career_test("not-a-career"), "")


if __name__ == "__main__":
    unittest.main()
