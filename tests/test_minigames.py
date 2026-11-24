import os
import unittest

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialog

from minigames import ANIME_QUESTIONS, IQ_QUESTIONS, QuizDialog


class QuizTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def test_iq_shows_result_and_only_exits_below_seventy(self):
        for correct, score, should_exit in ((0, 40, True), (1, 55, True), (2, 70, False), (6, 130, False)):
            with self.subTest(correct=correct):
                dialog = QuizDialog(None, iq_test=True)
                self.assertEqual(len(dialog.questions), 6)
                self.assertEqual(set(dialog.questions), set(IQ_QUESTIONS))
                for index, question in enumerate(dialog.questions):
                    choice = question.answer if index < correct else question.distractors[0]
                    next(button for button in dialog.options if button.text() == choice).click()
                self.assertIn(f'Игровой IQ — {score}', dialog.prompt.text())
                self.assertEqual(dialog.exit_after_quiz, should_exit)
                self.assertEqual(dialog.shutdown_timer.isActive(), should_exit)
                dialog.reject()
                self.assertFalse(dialog.shutdown_timer.isActive())
                dialog.deleteLater()

    def test_canceling_incomplete_test_leaves_app_open(self):
        dialog = QuizDialog(None, iq_test=True)
        dialog.options[0].click()
        dialog.close_button.click()
        self.assertFalse(dialog.exit_after_quiz)
        self.assertFalse(dialog.shutdown_timer.isActive())
        dialog.deleteLater()

    def test_anime_has_three_unique_choices_correct_feedback_and_no_exit(self):
        dialog = QuizDialog(None)
        self.assertEqual(len(dialog.questions), 15)
        self.assertEqual(set(dialog.questions), set(ANIME_QUESTIONS))
        for index, question in enumerate(dialog.questions):
            choices = {button.text() for button in dialog.options}
            self.assertEqual(choices, {question.answer, *question.distractors})
            self.assertEqual(len(choices), 3)
            self.assertFalse(dialog.next_button.isEnabled())
            answer = question.answer if index % 2 == 0 else question.distractors[0]
            next(button for button in dialog.options if button.text() == answer).click()
            self.assertIn(question.answer, dialog.feedback.text())
            self.assertTrue(all(not button.isEnabled() for button in dialog.options))
            dialog.next_button.click()
        self.assertIn('8 из 15', dialog.prompt.text())
        self.assertFalse(dialog.exit_after_quiz)
        dialog.deleteLater()

    def test_low_score_dismisses_dialog_via_timer(self):
        dialog = QuizDialog(None, iq_test=True)
        for question in dialog.questions:
            next(button for button in dialog.options if button.text() == question.distractors[0]).click()
        self.assertTrue(dialog.shutdown_timer.isActive())
        dialog.shutdown_timer.start(0)
        QTimer.singleShot(1000, dialog.reject)
        self.assertEqual(dialog.exec(), QDialog.Accepted)
        dialog.deleteLater()
