import unittest
import subprocess
import tempfile
import os
import shutil


class TestArgumentParser(unittest.TestCase):
    def test_argparse_argumentparser(self):
        """
        Test for `argparse.ArgumentParser`.
        """
        script_path = subprocess.check_output(
            ["git", "config", "alias.fetch-file"], text=True
        ).strip()
        if script_path.startswith("!python3 "):
            script_path = script_path[len("!python3 "):]
        with open(script_path, "r") as f:
            source = f.read()
        self.assertIn("argparse.ArgumentParser", source, "failed to find argparse.ArgumentParser")


class TestGitRepository(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.oldpwd = os.getcwd()
        os.chdir(self.tmpdir)
        subprocess.run(["git", "init"], check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], check=True)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)
        os.chdir(self.oldpwd)


class TestAdd(TestGitRepository):
    def test_add(self):
        """Test `git fetch-file add`."""
        pass


if __name__ == "__main__":
    unittest.main()
