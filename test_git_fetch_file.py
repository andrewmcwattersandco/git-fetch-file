import unittest
import subprocess
import difflib


class TestGitFetchFile(unittest.TestCase):
    def test_help(self):
        """
        Test `git fetch-file` and `git fetch-file -h`.

        NOTE: If installed as an alias, `--help` will ONLY output
        `'fetch-file' is aliased to '!python3 .../andrewmcwattersandco/git-fetch-file/git-fetch-file.py'`
        so we only test the command with `git fetch-file -h` here.
        """
        expected_help_start = "usage: git fetch-file [-h] "
        for args in (["git", "fetch-file"], ["git", "fetch-file", "-h"]):
            result = subprocess.run(args, capture_output=True)
            output = result.stdout.decode()
            if not output.startswith(expected_help_start):
                diff = "\n".join(
                    difflib.unified_diff(
                        [expected_help_start],
                        [output],
                        fromfile="expected",
                        tofile="output",
                        lineterm=""
                    )
                )
                self.fail(f"expected help message not found in stdout\nDiff:\n{diff}")

    def test_add_usage(self):
        """Test `git fetch-file add` usage."""
        expected_usage_start = "usage: git fetch-file add [-h] "
        for args in (["git", "fetch-file", "add"], ["git", "fetch-file", "add", "-h"]):
            result = subprocess.run(args, capture_output=True)
            output = (result.stdout + result.stderr).decode()
            if not output.startswith(expected_usage_start):
                diff = "\n".join(
                    difflib.unified_diff(
                        [expected_usage_start],
                        [output],
                        fromfile="expected",
                        tofile="output",
                        lineterm=""
                    )
                )
                self.fail(f"expected usage message not found in output\nDiff:\n{diff}")

if __name__ == "__main__":
    unittest.main()
