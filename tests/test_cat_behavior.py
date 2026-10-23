from __future__ import annotations

import unittest
from unittest.mock import patch

from pet_behavior import PetBehaviorManager
from pet_dialogue import DialogueManager


class CatBehaviorTests(unittest.TestCase):
    def test_sleep_click_wakes_even_during_click_cooldown(self) -> None:
        manager = PetBehaviorManager()
        manager.state = "sleep"
        manager.state_until = 1000
        manager.click_cooldown_until = 500
        action = manager.click(now=100)

        self.assertIsNotNone(action)
        self.assertEqual((action.state, manager.state), ("idle", "idle"))
        self.assertEqual(action.dialogue, "wake")
        self.assertEqual(manager.last_activity, 100)
        line, _ = DialogueManager().choose(action.dialogue, now=100)
        self.assertIn(line, {"喵。", "呼噜……"})

    def test_ambient_activity_does_not_prevent_sleep(self) -> None:
        manager = PetBehaviorManager()
        manager.last_activity = 0
        manager.next_autonomous_check = 0
        manager.sleep_after = 180
        with patch("pet_behavior.random.random", return_value=0.1):
            walking = manager.autonomous_action(now=50)
        self.assertEqual(walking.state, "walk")
        manager.update(now=60)
        self.assertEqual(manager.last_activity, 0)
        manager.context_action("confused", "cpu", now=70)
        manager.update(now=75)
        self.assertEqual(manager.last_activity, 0)
        with patch("pet_behavior.random.random", return_value=0.0):
            sleeping = manager.autonomous_action(now=181)
        self.assertEqual(sleeping.state, "sleep")
        self.assertEqual(sleeping.dialogue, "")
        self.assertEqual(sleeping.duration, float("inf"))
        self.assertIsNone(manager.state_until)
        self.assertFalse(manager.update(now=100000))
        self.assertEqual(manager.state, "sleep")

    def test_manual_sleep_waits_for_explicit_wake_and_activity_does_not_wake(self) -> None:
        manager = PetBehaviorManager()
        manager.manual_action("sleep", now=100)
        manager.mark_activity(now=1000)
        self.assertFalse(manager.update(now=100000))
        self.assertEqual(manager.state, "sleep")
        self.assertIsNone(manager.context_action("idle", "weather", now=100001))
        action = manager.manual_action("wake", now=100002)
        self.assertEqual((action.state, action.dialogue), ("idle", "wake"))

    def test_manual_walk_uses_one_loop_deadline_and_groom_uses_full_clip(self) -> None:
        manager = PetBehaviorManager({"walk": (5, 8), "groom": 9.375},
                                     manual_walk_duration=5.2)
        action = manager.manual_action("walk", now=100)
        self.assertEqual(action.duration, 5.2)
        self.assertFalse(manager.update(now=105))
        self.assertTrue(manager.update(now=105.2))
        action = manager.manual_action("groom", now=110)
        self.assertEqual(action.duration, 9.375)
        self.assertTrue(manager.update(now=119.375))

    def test_manual_action_rejects_unknown_animation(self) -> None:
        with self.assertRaises(ValueError):
            PetBehaviorManager().manual_action("jump", now=100)

    def test_groom_is_one_complete_silent_action_with_configurable_duration(self) -> None:
        manager = PetBehaviorManager(state_duration={"groom": 9.25})
        manager.next_autonomous_check = 0
        now = manager.last_activity + 5
        with patch("pet_behavior.random.random", return_value=0.04):
            action = manager.autonomous_action(now=now)
        self.assertEqual((action.state, action.dialogue, action.duration), ("groom", "", 9.25))
        self.assertIsNone(manager.autonomous_action(now=now + 2))
        self.assertFalse(manager.update(now=now + 9))
        self.assertTrue(manager.update(now=now + 9.25))
        self.assertEqual(manager.state, "idle")
        self.assertGreaterEqual(manager.next_autonomous_check, now + 9.25 + 30)

    def test_click_burst_enters_silent_cooldown(self) -> None:
        manager = PetBehaviorManager()
        with patch("pet_behavior.random.random", return_value=0.0):
            actions = [manager.click(now=100 + index * 0.3) for index in range(5)]
        self.assertEqual(actions[0].dialogue, "clicked")
        self.assertIsNone(actions[1])
        self.assertIsNone(actions[2])
        self.assertIsNone(actions[3])
        self.assertEqual((actions[4].state, actions[4].dialogue), ("idle", ""))
        self.assertIsNone(manager.click(now=102))
        self.assertEqual(manager.last_activity, 102)
        self.assertGreater(manager.click_cooldown_until, 102)

    def test_idle_and_most_ambient_checks_are_silent(self) -> None:
        manager = PetBehaviorManager()
        manager.next_autonomous_check = 0
        now = manager.last_activity + 1
        with patch("pet_behavior.random.random", return_value=0.5):
            self.assertIsNone(manager.autonomous_action(now=now))
            self.assertIsNone(manager.click(now=now + 1))
        self.assertEqual(manager.state, "idle")
        self.assertIsNone(DialogueManager().choose("idle", now=100))
        self.assertIsNone(DialogueManager().choose("groom", now=100))

    def test_dialogue_remains_short_and_honors_click_cooldown(self) -> None:
        manager = DialogueManager()
        first = manager.choose("clicked", now=100)
        self.assertIn(first[0], {"喵。", "呼噜……"})
        self.assertIsNone(manager.choose("clicked", now=101))
        second = manager.choose("clicked", now=113)
        self.assertNotEqual(first[0], second[0])
        self.assertLessEqual(second[1], 2.3)


if __name__ == "__main__":
    unittest.main()
