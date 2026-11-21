"""P-09: enforce Discord's global application-command capacities before sync."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_command_sync as command_sync


class FakeCommand:
    def __init__(self, name, command_type=1, children=None):
        self.name = name
        self.type = SimpleNamespace(value=command_type)
        self.commands = list(children or [])

    def to_dict(self, _tree=None):
        return {
            "name": self.name,
            "type": self.type.value,
            "options": [child.to_dict() for child in self.commands],
        }


class FakeTree:
    def __init__(self, commands):
        self._commands = list(commands)
        self.sync = AsyncMock(return_value=self._commands)

    def get_commands(self):
        return list(self._commands)


class CommandInventoryTests(unittest.TestCase):
    def test_nested_subcommands_do_not_count_as_global_roots(self):
        group = FakeCommand("team", children=[FakeCommand(f"command-{n}") for n in range(8)])
        roots = [group, *[FakeCommand(f"root-{n}") for n in range(99)]]
        roots.extend([FakeCommand("user-menu", 2) for _ in range(15)])
        roots.extend([FakeCommand("message-menu", 3) for _ in range(15)])
        inventory = command_sync.validate_tree_capacity(FakeTree(roots))

        self.assertEqual(inventory["chat_input_commands"], 100)
        self.assertEqual(inventory["nested_subcommands"], 8)
        self.assertEqual(inventory["user_context_menus"], 15)
        self.assertEqual(inventory["message_context_menus"], 15)

    def test_101st_global_chat_input_command_is_rejected(self):
        tree = FakeTree([FakeCommand(f"command-{n}") for n in range(101)])
        with self.assertRaisesRegex(command_sync.CommandTreeLimitError, "101/100"):
            command_sync.validate_tree_capacity(tree)

    def test_group_with_more_than_25_subcommands_is_rejected(self):
        group = FakeCommand("oversized", children=[FakeCommand(f"sub-{n}") for n in range(26)])
        with self.assertRaisesRegex(command_sync.CommandTreeLimitError, "26/25"):
            command_sync.validate_tree_capacity(FakeTree([group]))

    def test_over_limit_tree_is_never_sent_to_discord(self):
        tree = FakeTree([FakeCommand(f"command-{n}") for n in range(101)])
        with (
            patch("pulse_command_sync.pulse_db.get_setting", return_value=""),
            patch("pulse_command_sync.pulse_db.set_setting") as save,
        ):
            with self.assertRaises(command_sync.CommandTreeLimitError):
                asyncio.run(command_sync.sync_commands(tree))

        tree.sync.assert_not_awaited()
        save.assert_not_called()

    def test_valid_changed_tree_syncs_and_only_then_saves_hash(self):
        tree = FakeTree([FakeCommand("pulse"), FakeCommand("team", children=[FakeCommand("warn")])])
        with (
            patch("pulse_command_sync.tree_hash", return_value="new-tree-hash"),
            patch("pulse_command_sync.pulse_db.get_setting", return_value="old-tree-hash"),
            patch("pulse_command_sync.pulse_db.set_setting") as save,
        ):
            result = asyncio.run(command_sync.sync_commands(tree))

        tree.sync.assert_awaited_once()
        save.assert_called_once_with(command_sync.SETTING_KEY, "new-tree-hash")
        self.assertTrue(result["synced"])
        self.assertEqual(result["chat_input_commands"], 2)
        self.assertEqual(result["nested_subcommands"], 1)


if __name__ == "__main__":
    unittest.main()
