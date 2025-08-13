import unittest
import subprocess


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

if __name__ == "__main__":
    unittest.main()
