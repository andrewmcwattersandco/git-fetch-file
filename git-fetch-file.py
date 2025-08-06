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
import shutil
from pathlib import Path
import glob as glob_module
import hashlib

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
    from io import StringIO
    output = StringIO()
    config.write(output)
    content = output.getvalue().rstrip() + '\n'
    with open(REMOTE_FILE_MANIFEST, "w") as f:
        f.write(content)


def hash_file(path):
    """Return the SHA-1 hash of a file, or None if it doesn't exist."""
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha1(f.read()).hexdigest()


def add_file(repo, path, commit=None, glob=None, comment=""):
    """
    Add a file or glob from a remote repository to .git-remote-files.

    Args:
        repo (str): Remote repository URL.
        path (str): File path or glob pattern.
        commit (str, optional): Commit, branch, or tag. Defaults to HEAD.
        glob (bool, optional): Whether path is a glob pattern. Auto-detected if None.
        comment (str): Optional comment describing the file.
    """
    # Normalize path by removing leading slash
    path = path.lstrip('/')
    
    config = load_remote_files()
    section = f'file "{path}"'
    if section not in config.sections():
        config.add_section(section)
    config[section]["repo"] = repo
    config[section]["commit"] = commit if commit else "HEAD"
    
    # Only set glob property if explicitly specified
    if glob is not None:
        config[section]["glob"] = str(glob).lower()
    else:
        # Auto-detect for display purposes only
        glob = is_glob_pattern(path)
    
    if comment:
        config[section]["comment"] = comment
    save_remote_files(config)
    
    pattern_type = "glob pattern" if glob else "file"
    print(f"Added {pattern_type} {path} from {repo} (commit: {config[section]['commit']})")


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
            # Get list of files from remote repository
            result = subprocess.run(
                ["git", "ls-remote", "--heads", "--tags", repo],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError(f"Failed to connect to repository: {result.stderr}")
            
            # Clone the repository to a temporary location to get file listing
            clone_dir = Path(TEMP_DIR) / "clone"
            clone_dir.mkdir(parents=True, exist_ok=True)
            
            try:
                clone_cmd = ["git", "clone", "--depth", "1"]
                if commit != "HEAD":
                    clone_cmd.extend(["--branch", commit])
                clone_cmd.extend([repo, str(clone_dir)])
                
                subprocess.run(
                    clone_cmd,
                    capture_output=True,
                    check=True
                )
                
                result = subprocess.run(
                    ["git", "ls-tree", "-r", "--name-only", "HEAD"],
                    cwd=clone_dir,
                    capture_output=True,
                    text=True
                )
                if result.returncode != 0:
                    raise RuntimeError(result.stderr)
                files = [f for f in result.stdout.splitlines() if glob_module.fnmatch.fnmatch(f, path)]
            finally:
                # Clean up clone directory
                if clone_dir.exists():
                    shutil.rmtree(clone_dir)

        for f in files:
            # Ensure we're working with relative paths to avoid system directory conflicts
            relative_path = f.lstrip('/')
            target_path = Path(relative_path)
            cache_file = Path(CACHE_DIR) / relative_path.replace("/", "_")
            local_hash = hash_file(target_path)
            last_hash = None
            if cache_file.exists():
                with open(cache_file) as cf:
                    last_hash = cf.read().strip()

            if local_hash and local_hash != last_hash and not force:
                print(f"Skipping {relative_path}: local changes detected. Use --force to overwrite.")
                continue

            # Clone the repository to fetch the file
            clone_dir = Path(TEMP_DIR) / "fetch_clone"
            clone_dir.mkdir(parents=True, exist_ok=True)
            
            try:
                # Clone the specific commit
                clone_cmd = ["git", "clone", "--depth", "1"]
                if commit != "HEAD":
                    clone_cmd.extend(["--branch", commit])
                clone_cmd.extend([repo, str(clone_dir)])
                
                subprocess.run(
                    clone_cmd,
                    capture_output=True,
                    check=True
                )
                
                # Get the actual commit hash
                result = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=clone_dir,
                    capture_output=True,
                    text=True,
                    check=True
                )
                fetched_commit = result.stdout.strip()
                
                # Copy the file from clone to target location
                source_file = clone_dir / f
                if source_file.exists():
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_file, target_path)
                    
                    new_hash = hash_file(target_path)
                    cache_file.parent.mkdir(parents=True, exist_ok=True)
                    with open(cache_file, "w") as cf:
                        cf.write(new_hash)

                    print(f"Fetched {relative_path} at {commit}")
                else:
                    print(f"Warning: File {f} not found in repository")
                    
            except subprocess.CalledProcessError as e:
                print(f"Warning: Failed to clone repository for {f}: {e}")
                continue
            finally:
                # Clean up clone directory
                if clone_dir.exists():
                    shutil.rmtree(clone_dir)

    finally:
        # Clean up temp directory contents
        if temp_dir.exists():
            for item in temp_dir.iterdir():
                if item.is_file():
                    item.unlink()
                elif item.is_dir():
                    shutil.rmtree(item)
            # Only remove temp_dir if it's empty
            try:
                temp_dir.rmdir()
            except OSError:
                pass  # Directory not empty, that's okay

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
        # Check if glob was explicitly set, otherwise auto-detect
        if "glob" in config[section]:
            is_glob = config[section].getboolean("glob", False)
        else:
            is_glob = is_glob_pattern(path)
        fetched_commit = fetch_file(repo, path, commit, is_glob, force=force)

        if save and commit != "HEAD" and not commit.startswith(fetched_commit[:7]):
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
        # Check if glob was explicitly set, otherwise auto-detect
        if "glob" in config[section]:
            is_glob = config[section].getboolean("glob", False)
        else:
            is_glob = is_glob_pattern(path)
        
        # Show if it's a glob pattern
        pattern_indicator = " (glob)" if is_glob else ""
        print(f"{path}{pattern_indicator} (repo: {repo}, commit: {commit})")


def is_glob_pattern(path):
    """Check if a path contains glob pattern characters."""
    glob_chars = ['*', '?', '[', ']', '{', '}']
    return any(char in path for char in glob_chars)


def main():
    """Command-line interface for git-fetch-file."""
    if len(sys.argv) < 2:
        print("Usage: git fetch-file <command> [args...]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "add":
        if len(sys.argv) < 4:
            print("Usage: git fetch-file add <repo> <path> [--commit <commit>] [--glob] [--no-glob] [--comment <text>]")
            sys.exit(1)
        repo = sys.argv[2]
        path = sys.argv[3]
        commit = None
        glob_flag = None  # None means auto-detect
        comment = ""
        args = sys.argv[4:]
        i = 0
        while i < len(args):
            if args[i] == "--commit":
                i += 1
                commit = args[i]
            elif args[i] == "--glob":
                glob_flag = True
            elif args[i] == "--no-glob":
                glob_flag = False
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
