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
from concurrent.futures import ThreadPoolExecutor, as_completed
import tempfile

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

    # Clone the repository once for all files
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
        
        # Determine which files to process
        files = [path]
        if is_glob:
            # Get list of files from the cloned repository
            result = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", "HEAD"],
                cwd=clone_dir,
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr)
            files = [f for f in result.stdout.splitlines() if glob_module.fnmatch.fnmatch(f, path)]
            
            if files:
                print(f"Found {len(files)} files matching '{path}' in {repo}")
            else:
                print(f"No files found matching '{path}' in {repo}")
                return fetched_commit
        
        # Process all files from the same clone
        for f in files:
            # Ensure we're working with relative paths to avoid system directory conflicts
            relative_path = f.lstrip('/')
            
            # Determine target path based on target_dir
            if target_dir:
                if is_glob:
                    # For glob patterns, preserve directory structure within target directory
                    target_path = Path(target_dir) / relative_path
                    cache_key = f"{target_dir}_{relative_path}".replace("/", "_")
                else:
                    # For single files, place file in target directory, preserving filename
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
        print(f"Error: Failed to clone repository: {e}")
        raise RuntimeError(f"Failed to clone repository: {e}")
    finally:
        # Clean up clone directory
        if clone_dir.exists():
            shutil.rmtree(clone_dir)
        
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


def pull_files(force=False, save=False, dry_run=False, jobs=None, commit_message=None, edit=False, no_commit=False, auto_commit=False):
    """
    Pull all tracked files from .git-remote-files.

    Args:
        force (bool): Overwrite local changes if True.
        save (bool): Update .git-remote-files commit entries for branch-based files.
        dry_run (bool): If True, only show what would be done without executing.
        jobs (int): Number of concurrent jobs for fetching files.
        commit_message (str, optional): Custom commit message for auto-commit.
        edit (bool): Whether to open editor for commit message.
        no_commit (bool): If True, don't auto-commit changes.
        auto_commit (bool): If True, auto-commit with default message.
    """
    config = load_remote_files()
    
    if not config.sections():
        if dry_run:
            print("No remote files tracked.")
        return
    
    # Collect file entries to process
    file_entries = []
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
        
        file_entries.append({
            'section': section,
            'path': path,
            'repo': repo,
            'commit': commit,
            'target_dir': target_dir,
            'is_glob': is_glob
        })
    
    # Collect results for organized dry-run output
    if dry_run:
        would_fetch = []
        would_skip = []
        up_to_date = []
        errors = []
        
        for entry in file_entries:
            # Check if file would be updated by examining local state
            try:
                # Determine target path based on target_dir
                if entry['target_dir']:
                    if entry['is_glob']:
                        # For glob patterns, preserve directory structure within target directory
                        target_path = Path(entry['target_dir']) / entry['path']
                        cache_key = f"{entry['target_dir']}_{entry['path']}".replace("/", "_")
                    else:
                        # For single files, place file in target directory, preserving filename
                        filename = Path(entry['path']).name
                        target_path = Path(entry['target_dir']) / filename
                        cache_key = f"{entry['target_dir']}_{filename}".replace("/", "_")
                else:
                    target_path = Path(entry['path'])
                    cache_key = entry['path'].replace("/", "_")
                
                cache_file = Path(CACHE_DIR) / cache_key
                local_hash = hash_file(target_path)
                last_hash = None
                if cache_file.exists():
                    with open(cache_file) as cf:
                        last_hash = cf.read().strip()
                
                # Simulate what would happen
                if local_hash and local_hash != last_hash and not force:
                    would_skip.append(f"{entry['path']} from {entry['repo']}")
                elif local_hash == last_hash and entry['commit'] != "HEAD":
                    # File exists and hash matches - up to date
                    commit_display = entry['commit'][:7] if len(entry['commit']) > 7 else entry['commit']
                    up_to_date.append(f"{entry['path']} from {entry['repo']} ({commit_display})")
                else:
                    # Would fetch - show commit change if applicable
                    commit_info = entry['commit']
                    if save and entry['commit'] != "HEAD":
                        commit_info = f"{entry['commit'][:7] if len(entry['commit']) > 7 else entry['commit']} -> [new commit]"
                    elif entry['commit'] == "HEAD":
                        commit_info = "HEAD -> [latest]"
                    would_fetch.append(f"{entry['path']} from {entry['repo']} ({commit_info})")
                        
            except Exception as e:
                errors.append(f"{entry['path']} from {entry['repo']}: {str(e)}")
        
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
        
        return
    
    # Execute concurrent fetching
    def fetch_entry(entry):
        """Fetch a single file entry and return results."""
        try:
            fetched_commit = fetch_file(
                entry['repo'], 
                entry['path'], 
                entry['commit'], 
                entry['is_glob'], 
                force=force, 
                target_dir=entry['target_dir'], 
                dry_run=False
            )
            return {
                'section': entry['section'],
                'path': entry['path'],
                'commit': entry['commit'],
                'fetched_commit': fetched_commit,
                'success': True,
                'error': None
            }
        except Exception as e:
            return {
                'section': entry['section'],
                'path': entry['path'],
                'commit': entry['commit'],
                'fetched_commit': None,
                'success': False,
                'error': str(e)
            }
    
    # Determine default jobs if not set
    if jobs is None:
        try:
            jobs = os.cpu_count() or 1
        except Exception:
            jobs = 1
    results = []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        # Submit all tasks
        future_to_entry = {executor.submit(fetch_entry, entry): entry for entry in file_entries}
        # Collect results as they complete
        for future in as_completed(future_to_entry):
            result = future.result()
            results.append(result)
            # Print errors immediately for better user feedback
            if not result['success']:
                print(f"Error fetching {result['path']}: {result['error']}")
    
    # Update config with new commits if save is enabled
    if save:
        updated = False
        for result in results:
            if result['success'] and result['commit'] != "HEAD":
                if not result['commit'].startswith(result['fetched_commit'][:7]):
                    config[result['section']]["commit"] = result['fetched_commit']
                    updated = True
        
        if updated:
            save_remote_files(config)
    
    # Auto-commit changes if requested (and not in dry-run mode)
    if not dry_run:
        # Check if we should commit
        should_commit = not no_commit and (commit_message is not None or edit or auto_commit)
        
        # If no explicit commit flags were used, default to no commit (like git merge)
        if not should_commit and commit_message is None and not edit and not auto_commit and not no_commit:
            should_commit = False
        
        # Commit if requested
        if should_commit:
            # Pass full results for informative commit message generation
            commit_changes(commit_message=commit_message, edit=edit, no_commit=no_commit, file_results=results)


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


def is_git_repository():
    """Check if we're in a git repository."""
    try:
        subprocess.run(
            ["git", "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            check=True
        )
        return True
    except subprocess.CalledProcessError:
        return False


def has_git_changes():
    """Check if there are any changes staged or unstaged in the git repository."""
    try:
        # Check for staged changes
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=True
        )
        if result.stdout.strip():
            return True
        
        # Check for unstaged changes to tracked files
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True,
            text=True,
            check=True
        )
        if result.stdout.strip():
            return True
        
        # Check for untracked files that might have been fetched
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            capture_output=True,
            text=True,
            check=True
        )
        untracked = result.stdout.strip().split('\n') if result.stdout.strip() else []
        
        # Filter to only check files that might be from git-fetch-file
        # (this is a heuristic - we assume newly fetched files are untracked)
        return len(untracked) > 0
        
    except subprocess.CalledProcessError:
        return False


def commit_changes(commit_message=None, edit=False, no_commit=False, file_results=None):
    """
    Commit changes to git if there are any.
    
    Args:
        commit_message (str, optional): Custom commit message. If None, uses default.
        edit (bool): Whether to open editor for commit message.
        no_commit (bool): If True, don't commit (useful for overriding default behavior).
        file_results (list, optional): List of file fetch results for informative default message.
    
    Returns:
        bool: True if commit was made, False otherwise.
    """
    if no_commit:
        return False
    
    if not is_git_repository():
        print("warning: Not in a git repository, skipping commit")
        return False
    
    if not has_git_changes():
        return False
    
    try:
        # Stage all changes (including new files)
        subprocess.run(["git", "add", "."], check=True)
        
        # Prepare commit command
        commit_cmd = ["git", "commit"]
        
        if edit:
            # Use editor for commit message
            if commit_message:
                # Pre-populate editor with provided message
                with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
                    f.write(commit_message)
                    temp_file = f.name
                try:
                    commit_cmd.extend(["-t", temp_file])
                    subprocess.run(commit_cmd, check=True)
                finally:
                    os.unlink(temp_file)
            else:
                # Just open editor
                subprocess.run(commit_cmd, check=True)
        else:
            # Use provided message or generate git-style informative default
            if not commit_message:
                commit_message = generate_default_commit_message(file_results)
            commit_cmd.extend(["-m", commit_message])
            subprocess.run(commit_cmd, check=True)
        
        print(f"Committed changes: {commit_message if not edit else '[via editor]'}")
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"warning: Failed to commit changes: {e}")
        return False


def generate_default_commit_message(file_results):
    """
    Generate a git-style informative default commit message based on what was fetched.
    
    Args:
        file_results (list): List of file fetch results.
    
    Returns:
        str: Generated commit message.
    """
    if not file_results:
        return "Update remote files"
    
    successful_results = [r for r in file_results if r['success']]
    
    if not successful_results:
        return "Update remote files"
    
    # Count files
    file_count = len(successful_results)
    
    if file_count == 1:
        result = successful_results[0]
        file_path = result['path']
        commit_hash = result.get('fetched_commit', '')
        
        # Extract just the filename for cleaner display
        if '/' in file_path:
            file_name = file_path.split('/')[-1]
        else:
            file_name = file_path
        
        # Include commit info if available and it's not HEAD
        if commit_hash and len(commit_hash) >= 7 and commit_hash != result.get('commit', ''):
            short_hash = commit_hash[:7]
            return f"Update {file_name} to {short_hash}"
        else:
            return f"Update {file_name}"
    
    elif file_count <= 3:
        # List individual files for small counts
        file_names = []
        for result in successful_results:
            file_path = result['path']
            if '/' in file_path:
                file_name = file_path.split('/')[-1]
            else:
                file_name = file_path
            file_names.append(file_name)
        
        if file_count == 2:
            return f"Update {file_names[0]} and {file_names[1]}"
        else:  # file_count == 3
            return f"Update {', '.join(file_names[:-1])}, and {file_names[-1]}"
    
    else:
        # For larger counts, use summary format with directory info if applicable
        # Check if files are from the same directory structure
        dirs = set()
        for result in successful_results:
            file_path = result['path']
            if '/' in file_path:
                dir_path = '/'.join(file_path.split('/')[:-1])
                dirs.add(dir_path)
        
        if len(dirs) == 1 and dirs != {''}:
            dir_name = dirs.pop()
            return f"Update {file_count} files in {dir_name}/"
        else:
            return f"Update {file_count} remote files"


def main():
    """Command-line interface for git-fetch-file."""
    if len(sys.argv) < 2:
        print("Usage: git fetch-file <command> [args...]")
        print("Commands:")
        print("  add <repo> <path> [target_dir] [options]  Add a file or glob to track")
        print("  pull [options]                            Pull all tracked files")
        print("  list                                      List all tracked files")
        print("  status                                    Alias for list")
        print("")
        print("Add options:")
        print("  --commit <commit>                         Track specific commit/tag")
        print("  -b, --branch <branch>                     Track specific branch")
        print("  --glob                                    Force treat path as glob pattern")
        print("  --no-glob                                 Force treat path as literal file")
        print("  --comment <text>                          Add descriptive comment")
        print("  --dry-run                                 Show what would be done")
        print("")
        print("Pull options:")
        print("  --dry-run                                 Show what would be done without executing")
        print("  --force                                   Overwrite local changes")
        print("  --save                                    Update commit hashes for branches")
        print("  --jobs=<n>                                Number of parallel jobs (default: 4)")
        print("  --commit                                  Auto-commit with default message")
        print("  -m <msg>, --message=<msg>                 Commit with message")
        print("  --edit                                    Edit commit message")
        print("  --no-commit                               Don't auto-commit changes")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "add":
        if len(sys.argv) < 4:
            print("Usage: git fetch-file add <repo> <path> [target_dir] [--commit <commit>] [-b|--branch <branch>] [--glob] [--no-glob] [--comment <text>] [--dry-run]")
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
            elif args[i] == "--branch" or args[i] == "-b":
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
        edit_flag = "--edit" in sys.argv
        no_commit_flag = "--no-commit" in sys.argv
        auto_commit_flag = "--commit" in sys.argv
        
        # Parse commit message from -m or --message
        commit_message = None
        for i, arg in enumerate(sys.argv):
            if arg == "-m" and i + 1 < len(sys.argv):
                commit_message = sys.argv[i + 1]
                break
            elif arg.startswith("--message="):
                commit_message = arg.split("=", 1)[1]
                break
        
        # Parse --jobs parameter
        jobs = None  # default: None means auto-detect (os.cpu_count())
        for arg in sys.argv:
            if arg.startswith("--jobs="):
                try:
                    jobs = int(arg.split("=")[1])
                    if jobs < 1:
                        print("error: --jobs must be a positive integer")
                        sys.exit(1)
                except ValueError:
                    print("error: --jobs must be a positive integer")
                    sys.exit(1)
        pull_files(force=force_flag, save=save_flag, dry_run=dry_run_flag, jobs=jobs,
                  commit_message=commit_message, edit=edit_flag, no_commit=no_commit_flag,
                  auto_commit=auto_commit_flag)

    elif cmd == "status" or cmd == "list":
        status_files()

    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)


if __name__ == "__main__":
    main()
