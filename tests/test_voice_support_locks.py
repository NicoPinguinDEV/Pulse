"""Per-case action locks of the voice support stay bounded and never drop a lock that is in use."""
import asyncio
import unittest

from cogs import voice_support


class CaseActionLockTests(unittest.TestCase):
    def setUp(self):
        self.cog = object.__new__(voice_support.VoiceSupport)
        self.cog._case_action_locks = {}

    def test_same_case_gets_same_lock(self):
        self.assertIs(self.cog._case_action_lock("a"), self.cog._case_action_lock("a"))

    def test_free_locks_are_pruned_held_locks_kept(self):
        async def run():
            held = self.cog._case_action_lock("held")
            await held.acquire()
            for i in range(voice_support.MAX_CASE_ACTION_LOCKS + 50):
                self.cog._case_action_lock(f"case{i}")
            self.assertLessEqual(len(self.cog._case_action_locks), voice_support.MAX_CASE_ACTION_LOCKS + 1)
            self.assertIs(self.cog._case_action_locks.get("held"), held)
            held.release()
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
