import re
import unittest
import subprocess
import tempfile
import os
import shutil
import configparser


class TestArgumentParser(unittest.TestCase):
    def test_argparse_argumentparser(self):
        """
        Test for `argparse.ArgumentParser`.
        """
        script_path = subprocess.check_output(
            ["git", "config", "alias.fetch-file"], text=True
        ).strip()
        # remove any leading "!something " prefix
        script_path = re.sub(r"^!\S+\s+", "", script_path)
        # when this is actually git-bash.exe, the path may need to be translated
        if os.name == 'nt' and script_path.startswith('/'):
            script_path = script_path[1] + ':' + script_path[2:]
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
        os.chdir(self.oldpwd)
        shutil.rmtree(self.tmpdir)


class TestAdd(TestGitRepository):
    def test_add(self):
        """Test `git fetch-file add <repository> <path>`."""
        subprocess.run(["git", "fetch-file", "add", "https://github.com/octocat/Hello-World.git", "README"], check=True)
        config = configparser.ConfigParser()
        config.read(".git-remote-files")
        section = 'file "README" from "https://github.com/octocat/Hello-World.git"'
        self.assertIn(section, config.sections(), "section not found in .git-remote-files")


class TestPull(TestGitRepository):
    def test_pull(self):
        """Test `git fetch-file pull`."""
        subprocess.run(["git", "fetch-file", "add", "https://github.com/octocat/Hello-World.git", "README"], check=True)
        subprocess.run(["git", "fetch-file", "pull"], check=True)
        self.assertTrue(os.path.exists("README"), "README not found after pull")

    def test_pull_from_subdirectory(self):
        """Test `git fetch-file pull` from a subdirectory with target directory."""
        # Add a file with a target directory
        subprocess.run(["git", "fetch-file", "add", "https://github.com/octocat/Hello-World.git", "README", ".local/bin"], check=True)
        
        # Create subdirectory and change into it
        os.makedirs(".local/bin", exist_ok=True)
        os.chdir(".local/bin")
        
        # Pull from subdirectory
        subprocess.run(["git", "fetch-file", "pull"], check=True)
        
        # Change back to repo root
        os.chdir(self.tmpdir)
        
        # Verify file is in correct location (relative to repo root)
        expected_path = os.path.join(self.tmpdir, ".local/bin/README")
        self.assertTrue(os.path.exists(expected_path), f"README not found at {expected_path}")
        
        # Verify file is NOT in the wrong location (double-nested path)
        wrong_path = os.path.join(self.tmpdir, ".local/bin/.local/bin/README")
        self.assertFalse(os.path.exists(wrong_path), f"README incorrectly created at {wrong_path}")


if __name__ == "__main__":
    unittest.main()
