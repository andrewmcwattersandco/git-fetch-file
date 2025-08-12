import unittest
import subprocess


class TestGitFetchFile(unittest.TestCase):
    def test_help(self):
        """
        Test `git fetch-file` and `git fetch-file -h`.

        NOTE: If installed as an alias, `--help` will ONLY output
        `'fetch-file' is aliased to '!python3 .../andrewmcwattersandco/git-fetch-file/git-fetch-file.py'`
        so we only test the command with `git fetch-file -h` here.
        """
        for args in (["git", "fetch-file"], ["git", "fetch-file", "-h"]):
            result = subprocess.run(args, capture_output=True)
            self.assertIn("""usage: git fetch-file [-h] {add,pull,status,list,remove} ...

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
""", result.stdout.decode(), "expected help message not found in output")


if __name__ == "__main__":
    unittest.main()
