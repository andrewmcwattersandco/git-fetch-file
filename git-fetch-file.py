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


def add_file(repo, path, commit=None, glob=None, comment="", target_dir=None, dry_run=False):
    """
    Add a file or glob from a remote repository to .git-remote-files.

    Args:
        repo (str): Remote repository URL.
        path (str): File path or glob pattern.
        commit (str, optional): Commit, branch, or tag. Defaults to HEAD.
        glob (bool, optional): Whether path is a glob pattern. Auto-detected if None.
        comment (str): Optional comment describing the file.
        target_dir (str, optional): Target directory to place the file. Defaults to same path.
        dry_run (bool): If True, only show what would be done without executing.
    """
    # Normalize path by removing leading slash
    path = path.lstrip('/')
    
    if dry_run:
        print(f"Would validate repository access: {repo}")
        # In dry-run mode, try to validate the repository exists
        try:
            result = subprocess.run(
                ["git", "ls-remote", "--heads", "--tags", repo],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                print(f"error: Cannot access repository: {result.stderr.strip()}")
                return
            else:
                print("Repository access confirmed")
        except subprocess.TimeoutExpired:
            print("warning: Repository validation timed out")
        except Exception as e:
            print(f"warning: Could not validate repository: {e}")
    
    config = load_remote_files()
    section = f'file "{path}"'
    
    if dry_run:
        action = "update" if section in config.sections() else "add"
        pattern_type = "glob pattern" if (glob if glob is not None else is_glob_pattern(path)) else "file"
        target_info = f" -> {target_dir}" if target_dir else ""
        commit_info = commit if commit else "HEAD"
        print(f"Would {action} {pattern_type} {path}{target_info} from {repo} (commit: {commit_info})")
        if comment:
            print(f"With comment: {comment}")
        return
    
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
    
    if target_dir:
        # Normalize target directory
        target_dir = target_dir.rstrip('/')
        config[section]["target"] = target_dir
    
    if comment:
        config[section]["comment"] = comment
    save_remote_files(config)
    
    pattern_type = "glob pattern" if glob else "file"
    target_info = f" -> {target_dir}" if target_dir else ""
    print(f"Added {pattern_type} {path}{target_info} from {repo} (commit: {config[section]['commit']})")


def fetch_file(repo, path, commit, is_glob=False, force=False, target_dir=None, dry_run=False):
    """
    Fetch a single file or glob from a remote repository at a specific commit.

    Args:
        repo (str): Remote repository URL.
        path (str): File path or glob.
        commit (str): Commit, branch, or tag.
        is_glob (bool): Whether path is a glob pattern.
        force (bool): Whether to overwrite local changes.
        target_dir (str, optional): Target directory to place the file.
        dry_run (bool): If True, only show what would be done without executing.

    Returns:
        str: The commit fetched (or would be fetched in dry-run mode).
    """
    temp_dir = Path(TEMP_DIR)
    temp_dir.mkdir(exist_ok=True)
    Path(CACHE_DIR).mkdir(parents=True, exist_ok=True)

    fetched_commit = commit

    # In dry-run mode, we skip most of the actual work since pull_files handles the summary
    if dry_run:
        return fetched_commit

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
                error_msg = f"Failed to connect to repository: {result.stderr}"
                raise RuntimeError(error_msg)
            
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
            
            # Determine target path based on target_dir
            if target_dir:
                # Place file in target directory, preserving filename
                filename = Path(relative_path).name
                target_path = Path(target_dir) / filename
                cache_key = f"{target_dir}_{filename}".replace("/", "_")
            else:
                # Use original path structure
                target_path = Path(relative_path)
                cache_key = relative_path.replace("/", "_")
            
            cache_file = Path(CACHE_DIR) / cache_key
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

                    print(f"Fetched {relative_path} -> {target_path} at {commit}")
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


def pull_files(force=False, save=False, dry_run=False):
    """
    Pull all tracked files from .git-remote-files.

    Args:
        force (bool): Overwrite local changes if True.
        save (bool): Update .git-remote-files commit entries for branch-based files.
        dry_run (bool): If True, only show what would be done without executing.
    """
    config = load_remote_files()
    
    if not config.sections():
        if dry_run:
            print("No remote files tracked.")
        return
    
    # Collect results for organized dry-run output
    if dry_run:
        would_fetch = []
        would_skip = []
        up_to_date = []
        errors = []
    
    for section in config.sections():
        path = section.split('"')[1]
        repo = config[section]["repo"]
        commit = config[section].get("commit", "HEAD")
        target_dir = config[section].get("target", None)
        # Check if glob was explicitly set, otherwise auto-detect
        if "glob" in config[section]:
            is_glob = config[section].getboolean("glob", False)
        else:
            is_glob = is_glob_pattern(path)
            
        if dry_run:
            # Check if file would be updated by examining local state
            try:
                # Determine target path based on target_dir
                if target_dir:
                    filename = Path(path).name
                    target_path = Path(target_dir) / filename
                    cache_key = f"{target_dir}_{filename}".replace("/", "_")
                else:
                    target_path = Path(path)
                    cache_key = path.replace("/", "_")
                
                cache_file = Path(CACHE_DIR) / cache_key
                local_hash = hash_file(target_path)
                last_hash = None
                if cache_file.exists():
                    with open(cache_file) as cf:
                        last_hash = cf.read().strip()
                
                # Simulate what would happen
                if local_hash and local_hash != last_hash and not force:
                    would_skip.append(f"{path} from {repo}")
                elif local_hash == last_hash and commit != "HEAD":
                    # File exists and hash matches - up to date
                    up_to_date.append(f"{path} from {repo} ({commit[:7] if len(commit) > 7 else commit})")
                else:
                    # Would fetch - show commit change if applicable
                    commit_info = commit
                    if save and commit != "HEAD":
                        commit_info = f"{commit[:7] if len(commit) > 7 else commit} -> [new commit]"
                    elif commit == "HEAD":
                        commit_info = "HEAD -> [latest]"
                    would_fetch.append(f"{path} from {repo} ({commit_info})")
                    
            except Exception as e:
                errors.append(f"{path} from {repo}: {str(e)}")
        else:
            fetched_commit = fetch_file(repo, path, commit, is_glob, force=force, target_dir=target_dir, dry_run=dry_run)

            if save and commit != "HEAD" and not commit.startswith(fetched_commit[:7]):
                config[section]["commit"] = fetched_commit
    
    if dry_run:
        # Print organized output
        if would_fetch:
            print("Would fetch:")
            for item in would_fetch:
                print(f"  {item}")
            print()
        
        if would_skip:
            print("Would skip (local changes):")
            for item in would_skip:
                print(f"  {item} (use --force to overwrite)")
            print()
        
        if up_to_date:
            print("Up to date:")
            for item in up_to_date:
                print(f"  {item}")
            print()
        
        if errors:
            print("Errors:")
            for item in errors:
                print(f"  {item}")
            print()
        
        if not (would_fetch or would_skip or up_to_date or errors):
            print("No changes needed.")
    
    if save and not dry_run:
        save_remote_files(config)


def status_files():
    """Print all files tracked in .git-remote-files."""
    config = load_remote_files()
    
    if not config.sections():
        print("No remote files tracked.")
        return
    
    for section in config.sections():
        path = section.split('"')[1]
        repo = config[section]["repo"]
        commit = config[section].get("commit", "HEAD")
        target_dir = config[section].get("target", None)
        comment = config[section].get("comment", "")
        
        # Check if glob was explicitly set, otherwise auto-detect
        if "glob" in config[section]:
            is_glob = config[section].getboolean("glob", False)
        else:
            is_glob = is_glob_pattern(path)
        
        # Format the path display like git remote -v
        path_display = path
        if target_dir:
            path_display += f" -> {target_dir}"
        
        # Add glob indicator
        glob_indicator = " (glob)" if is_glob else ""
        
        # Truncate commit hash for display
        commit_display = commit
        if len(commit) > 7 and commit != "HEAD":
            commit_display = commit[:7]
        
        # Format like: path[glob_indicator] repo (commit)
        line = f"{path_display}{glob_indicator}\t{repo} ({commit_display})"
        
        # Add comment if present
        if comment:
            line += f" # {comment}"
        
        print(line)


def is_glob_pattern(path):
    """Check if a path contains glob pattern characters."""
    glob_chars = ['*', '?', '[', ']', '{', '}']
    return any(char in path for char in glob_chars)


def main():
    """Command-line interface for git-fetch-file."""
    if len(sys.argv) < 2:
        print("Usage: git fetch-file <command> [args...]")
        print("Commands:")
        print("  add <repo> <path> [target_dir] [options]  Add a file or glob to track")
        print("  pull [options]                           Pull all tracked files")
        print("  list                                     List all tracked files")
        print("  status                                   Alias for list")
        print("")
        print("Options:")
        print("  --dry-run                               Show what would be done without executing")
        print("  --force                                 Overwrite local changes (pull only)")
        print("  --save                                  Update commit hashes (pull only)")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "add":
        if len(sys.argv) < 4:
            print("Usage: git fetch-file add <repo> <path> [target_dir] [--commit <commit>] [--glob] [--no-glob] [--comment <text>] [--dry-run]")
            sys.exit(1)
        repo = sys.argv[2]
        path = sys.argv[3]
        
        # Check if the 4th argument is a target directory (doesn't start with --)
        target_dir = None
        args_start = 4
        if len(sys.argv) > 4 and not sys.argv[4].startswith("--"):
            target_dir = sys.argv[4]
            args_start = 5
        
        commit = None
        glob_flag = None  # None means auto-detect
        comment = ""
        dry_run = False
        args = sys.argv[args_start:]
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
            elif args[i] == "--dry-run":
                dry_run = True
            i += 1
        add_file(repo, path, commit, glob_flag, comment, target_dir, dry_run)

    elif cmd == "pull":
        force_flag = "--force" in sys.argv
        save_flag = "--save" in sys.argv
        dry_run_flag = "--dry-run" in sys.argv
        pull_files(force=force_flag, save=save_flag, dry_run=dry_run_flag)

    elif cmd == "status" or cmd == "list":
        status_files()

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
