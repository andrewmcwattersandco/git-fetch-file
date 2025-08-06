#!/usr/bin/env python3

"""
git-fetch-file

A tool to fetch individual files or globs from other Git repositories,
tracking their source commit in a .git-remote-files manifest.
"""

import configparser
import subprocess
import sys
import os
from pathlib import Path
import glob as glob_module
import hashlib
import shlex

REMOTE_FILE_MANIFEST = ".git-remote-files"
CACHE_DIR = ".git/fetch-file-cache"
TEMP_DIR = ".git/fetch-file-temp"


def load_remote_files():
    """Load the .git-remote-files manifest."""
    config = configparser.ConfigParser()
    if os.path.exists(REMOTE_FILE_MANIFEST):
        config.read(REMOTE_FILE_MANIFEST)
    return config


def save_remote_files(config):
    """Write the .git-remote-files manifest to disk."""
    with open(REMOTE_FILE_MANIFEST, "w") as f:
        config.write(f)


def hash_file(path):
    """Return the SHA-1 hash of a file, or None if it doesn't exist."""
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def add_file(repo, path, commit=None, glob=False, comment=""):
    """
    Add a file or glob from a remote repository to .git-remote-files.

    Args:
        repo (str): Remote repository URL.
        path (str): File path or glob pattern.
        commit (str, optional): Commit, branch, or tag. Defaults to HEAD.
        glob (bool): Whether path is a glob pattern.
        comment (str): Optional comment describing the file.
    """
    config = load_remote_files()
    section = f'file "{path}"'
    if section not in config.sections():
        config.add_section(section)
    config[section]["repo"] = repo
    config[section]["commit"] = commit if commit else "HEAD"
    config[section]["glob"] = str(glob).lower()
    config[section]["comment"] = comment
    save_remote_files(config)
    print(f"Added {path} from {repo} (commit: {config[section]['commit']})")


def fetch_file(repo, path, commit, is_glob=False, force=False):
    """
    Fetch a single file or glob from a remote repository at a specific commit.

    Args:
        repo (str): Remote repository URL.
        path (str): File path or glob.
        commit (str): Commit, branch, or tag.
        is_glob (bool): Whether path is a glob pattern.
        force (bool): Whether to overwrite local changes.

    Returns:
        str: The commit fetched.
    """
    temp_dir = Path(TEMP_DIR)
    temp_dir.mkdir(exist_ok=True)
    Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)

    fetched_commit = commit

    try:
        files = [path]
        if is_glob:
            result = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", commit],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr)
            files = [f for f in result.stdout.splitlines() if glob_module.fnmatch.fnmatch(f, path)]

        for f in files:
            target_path = Path(f)
            cache_file = Path(CACHE_DIR) / f.replace("/", "_")
            local_hash = hash_file(target_path)
            last_hash = None
            if cache_file.exists():
                with open(cache_file) as cf:
                    last_hash = cf.read().strip()

            if local_hash and local_hash != last_hash and not force:
                print(f"Skipping {f}: local changes detected. Use --force to overwrite.")
                continue

            cmd = f'git archive --remote={shlex.quote(repo)} {shlex.quote(commit)} {shlex.quote(f)} | tar -x -C {shlex.quote(TEMP_DIR)}'
            subprocess.run(cmd, shell=True, check=True)

            target_path.parent.mkdir(parents=True, exist_ok=True)
            for file_in_temp in temp_dir.glob("*"):
                file_in_temp.rename(target_path)

            new_hash = hash_file(target_path)
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w") as cf:
                cf.write(new_hash)

            print(f"Fetched {f} at {commit}")

    finally:
        for f in temp_dir.glob("*"):
            f.unlink()
        temp_dir.rmdir()

    return fetched_commit


def pull_files(force=False, save=False):
    """
    Pull all tracked files from .git-remote-files.

    Args:
        force (bool): Overwrite local changes if True.
        save (bool): Update .git-remote-files commit entries for branch-based files.
    """
    config = load_remote_files()
    for section in config.sections():
        path = section.split('"')[1]
        repo = config[section]["repo"]
        commit = config[section].get("commit", "HEAD")
        is_glob = config[section].getboolean("glob", False)
        fetched_commit = fetch_file(repo, path, commit, is_glob, force=force)

        if save and commit not in ("HEAD", commit[:7]):
            config[section]["commit"] = fetched_commit
    if save:
        save_remote_files(config)


def list_files():
    """Print all files tracked in .git-remote-files."""
    config = load_remote_files()
    for section in config.sections():
        path = section.split('"')[1]
        repo = config[section]["repo"]
        commit = config[section].get("commit", "HEAD")
        is_glob = config[section].getboolean("glob", False)
        print(f"{path} (repo: {repo}, commit: {commit}, glob: {is_glob})")


def main():
    """Command-line interface for git-fetch-file."""
    if len(sys.argv) < 2:
        print("Usage: git fetch-file <command> [args...]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "add":
        if len(sys.argv) < 4:
            print("Usage: git fetch-file add <repo> <path> [--commit <commit>] [--glob] [--comment <text>]")
            sys.exit(1)
        repo = sys.argv[2]
        path = sys.argv[3]
        commit = None
        glob_flag = False
        comment = ""
        args = sys.argv[4:]
        i = 0
        while i < len(args):
            if args[i] == "--commit":
                i += 1
                commit = args[i]
            elif args[i] == "--glob":
                glob_flag = True
            elif args[i] == "--comment":
                i += 1
                comment = args[i]
            i += 1
        add_file(repo, path, commit, glob_flag, comment)

    elif cmd == "pull":
        force_flag = "--force" in sys.argv
        save_flag = "--save" in sys.argv
        pull_files(force=force_flag, save=save_flag)

    elif cmd == "list":
        list_files()

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
