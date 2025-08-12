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
        expected_help = """usage: git fetch-file [-h] {add,pull,status,list,remove} ...

Fetch individual files or globs from other Git repositories

positional arguments:
  {add,pull,status,list,remove}
                        Available commands
    add                 Add a file or glob to track
    pull                Pull all tracked files
    status              List all tracked files
    list                Alias for status
    remove              Remove a tracked file

optional arguments:
  -h, --help            show this help message and exit
"""
        for args in (["git", "fetch-file"], ["git", "fetch-file", "-h"]):
            result = subprocess.run(args, capture_output=True)
            output = result.stdout.decode()
            if expected_help not in output:
                diff = difflib.unified_diff(
                    expected_help.splitlines(keepends=True),
                    output.splitlines(keepends=True),
                    fromfile='expected',
                    tofile='received'
                )
                print("Diff:\n", ''.join(diff))
            self.assertIn(expected_help, output, "expected help message not found in output")

    def test_add_usage(self):
        """Test `git fetch-file add` usage."""
        expected_usage = """usage: git fetch-file add [-h] [--detach COMMIT] [-b BRANCH] [--glob] [--no-glob] [--comment COMMENT] [--force] [--dry-run] repository path [target_dir]
git fetch-file add: error: the following arguments are required: repository, path
"""
        for args in (["git", "fetch-file", "add"], ["git", "fetch-file", "add", "-h"]):
            result = subprocess.run(args, capture_output=True)
            output = result.stderr.decode()
            if expected_usage not in output:
                diff = difflib.unified_diff(
                    expected_usage.splitlines(keepends=True),
                    output.splitlines(keepends=True),
                    fromfile='expected',
                    tofile='received'
                )
                print("Diff:\n", ''.join(diff))
            self.assertIn(expected_usage, output, "expected usage message not found in output")

if __name__ == "__main__":
    unittest.main()
