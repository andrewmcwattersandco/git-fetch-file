#!/usr/bin/env python3

"""
git-fetch-file

A tool to fetch individual files or globs from other Git repositories,
tracking their source commit in a .git-remote-files manifest.
"""

import argparse
import configparser
import subprocess
import sys
import os
import shutil
from pathlib import Path
import glob as glob_module
try:
    from glob import has_magic as glob_has_magic
except ImportError:
    def glob_has_magic(path):
        # Fallback for older Python
        glob_chars = ['*', '?', '[', ']', '{', '}']
        return any(char in path for char in glob_chars)
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


def get_short_commit(commit_hash):
    """Get shortened commit hash (7 chars) for display."""
    return commit_hash[:7] if len(commit_hash) > 7 else commit_hash


def get_files_from_glob(clone_dir, path, repository):
    """Get list of files matching glob pattern from repository."""
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
        print(f"Found {len(files)} files matching '{path}' in {repository}")
    else:
        print(f"No files found matching '{path}' in {repository}")
    return files


def process_file_copy(source_file, target_path, cache_file, force, file_path, commit):
    """Handle the actual file copying, caching, and conflict detection."""
    local_hash = hash_file(target_path)
    last_hash = None
    if cache_file.exists():
        with open(cache_file) as cf:
            last_hash = cf.read().strip()
    
    if local_hash and local_hash != last_hash and not force:
        print(f"Skipping {file_path.lstrip('/')}: local changes detected. Use --force to overwrite.")
        return False
    
    if source_file.exists():
        source_hash = hash_file(source_file)
        # Check if file is already up to date
        if local_hash == source_hash:
            # File is already up to date, but update cache to track it
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w") as cf:
                cf.write(source_hash)
            return "up_to_date"
        
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target_path)
        new_hash = hash_file(target_path)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_file, "w") as cf:
            cf.write(new_hash)
        print(f"Fetched {file_path.lstrip('/')} -> {target_path} at {commit}")
        return True
    else:
        print(f"warning: file {file_path} not found in repository")
        return False


def resolve_commit_ref(repository, commit_ref):
    """
    Resolve a commit reference to an actual commit hash.
    
    Args:
        repository (str): Remote repository URL.
        commit_ref (str): Commit reference (commit hash, branch, tag, or "HEAD").
        
    Returns:
        str: The resolved commit hash.
        
    Raises:
        subprocess.CalledProcessError: If the commit reference cannot be resolved.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", repository, commit_ref],
            capture_output=True,
            text=True,
            check=True
        )
        if result.stdout.strip():
            # Extract the commit hash from ls-remote output
            return result.stdout.strip().split('\t')[0]
        else:
            # If ls-remote didn't find the ref, try HEAD
            result = subprocess.run(
                ["git", "ls-remote", repository, "HEAD"],
                capture_output=True,
                text=True,
                check=True
            )
            return result.stdout.strip().split('\t')[0]
    except subprocess.CalledProcessError:
        raise


def add_file(repository, path, commit=None, branch=None, glob=None, comment="", target_dir=None, dry_run=False):
    """
    Add a file or glob from a remote repository to .git-remote-files.

    Args:
        repository (str): Remote repository URL.
        path (str): File path or glob pattern.
        commit (str, optional): Specific commit hash to detach at (legacy parameter, use --detach).
        branch (str, optional): Branch or tag name to track. Defaults to HEAD.
        glob (bool, optional): Whether path is a glob pattern. Auto-detected if None.
        comment (str): Optional comment describing the file.
        target_dir (str, optional): Target directory to place the file. Defaults to same path.
        dry_run (bool): If True, only show what would be done without executing.
    """
    # Normalize path by removing leading slash
    path = path.lstrip('/')
    
    # Determine the commit reference and branch tracking behavior
    # Priority: explicit commit > explicit branch > default to HEAD
    commit_ref = commit or branch or "HEAD"
    is_tracking_branch = branch is not None and not commit
    
    if dry_run:
        print(f"Would validate repository access: {repository}")
        # In dry-run mode, try to validate the repository exists
        try:
            result = subprocess.run(
                ["git", "ls-remote", "--heads", "--tags", repository],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode != 0:
                print(f"error: cannot access repository: {result.stderr.strip()}")
                return
            else:
                print("repository access confirmed")
        except subprocess.TimeoutExpired:
            print("warning: repository validation timed out")
        except Exception as e:
            print(f"warning: could not validate repository: {e}")
    
    config = load_remote_files()
    section = f'file "{path}"'
    
    if dry_run:
        # Resolve the commit reference to show accurate dry-run information
        try:
            actual_commit = resolve_commit_ref(repository, commit_ref)
        except subprocess.CalledProcessError as e:
            print(f"error: failed to resolve commit reference '{commit_ref}' in repository {repository}: {e}")
            return
        
        action = "update" if section in config.sections() else "add"
        pattern_type = "glob pattern" if (glob if glob is not None else is_glob_pattern(path)) else "file"
        target_info = f" -> {target_dir}" if target_dir else ""
        
        # Create git status-like message for dry run
        # Show the resolved commit hash and whether we're tracking a branch
        if is_tracking_branch:
            short_commit = get_short_commit(actual_commit)
            status_msg = f"On branch {branch} at {short_commit}"
        else:
            short_commit = get_short_commit(actual_commit)
            status_msg = f"HEAD detached at {short_commit}"
        
        print(f"Would {action} {pattern_type} {path}{target_info} from {repository} ({status_msg})")
        if comment:
            print(f"With comment: {comment}")
        return
    
    # Resolve the commit reference to an actual commit hash
    try:
        actual_commit = resolve_commit_ref(repository, commit_ref)
    except subprocess.CalledProcessError as e:
        print(f"error: failed to resolve commit reference '{commit_ref}' in repository {repository}: {e}")
        return
    
    if section not in config.sections():
        config.add_section(section)
    config[section]["repository"] = repository
    
    # Always store the resolved commit hash, never a branch name
    config[section]["commit"] = actual_commit
    
    # Defensive check: ensure we're not storing a branch name as commit
    if not (len(actual_commit) == 40 and all(c in '0123456789abcdef' for c in actual_commit.lower())):
        print(f"warning: commit value '{actual_commit}' does not look like a valid hash")
    
    # Set branch tracking information
    if is_tracking_branch:
        config[section]["branch"] = branch
    elif "branch" in config[section]:
        # Remove branch key if we're now detaching to a specific commit
        del config[section]["branch"]
    
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
    
    # Create git status-like message using the actual commit hash
    if is_tracking_branch:
        status_msg = f"On branch {branch}"
        # Show current commit if it differs from branch name
        short_commit = get_short_commit(actual_commit)
        status_msg += f" at {short_commit}"
    else:
        # Show the actual commit hash
        short_commit = get_short_commit(actual_commit)
        status_msg = f"HEAD detached at {short_commit}"
    
    print(f"Added {pattern_type} {path}{target_info} from {repository} ({status_msg})")


def fetch_file(repository, path, commit, is_glob=False, force=False, target_dir=None, dry_run=False):
    """
    Fetch a single file or glob from a remote repository at a specific commit.

    Args:
        repository (str): Remote repository URL.
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

    # Ensure TEMP_DIR exists before using it for TemporaryDirectory
    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as temp_clone_dir:
        clone_dir = Path(temp_clone_dir)
        try:
            # Clone the repository and get the actual commit hash
            fetched_commit = clone_repository_at_commit(repository, commit, clone_dir)

            files = [path]
            if is_glob:
                files = get_files_from_glob(clone_dir, path, repository)

            for f in files:
                target_path, cache_key = get_target_path_and_cache_key(f, target_dir, is_glob)
                cache_file = Path(CACHE_DIR) / cache_key
                source_file = clone_dir / f
                # Use helper function to handle file copying and caching
                process_file_copy(source_file, target_path, cache_file, force, f, commit)
        except subprocess.CalledProcessError as e:
            print(f"fatal: failed to clone repository: {e}")
            raise RuntimeError(f"failed to clone repository: {e}")
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
    config_migrated = False
    for section in config.sections():
        path = section.split('"')[1]
        repository = get_repository_from_config(config, section)
        
        # Migrate section if needed
        if migrate_config_section(config, section):
            config_migrated = True
            
        commit = config[section]["commit"]  # Should always exist now
        branch = config[section].get("branch", None)
        target_dir = config[section].get("target", None)
        # Check if glob was explicitly set, otherwise auto-detect
        if "glob" in config[section]:
            is_glob = config[section].getboolean("glob", False)
        else:
            is_glob = is_glob_pattern(path)
        
        file_entries.append({
            'section': section,
            'path': path,
            'repository': repository,
            'commit': commit,
            'branch': branch,
            'target_dir': target_dir,
            'is_glob': is_glob
        })
    
    # Group files by repository and commit to avoid concurrent cloning of the same repo
    repository_groups = {}
    for entry in file_entries:
        repository_key = (entry['repository'], entry['commit'])
        if repository_key not in repository_groups:
            repository_groups[repository_key] = []
        repository_groups[repository_key].append(entry)
    
    # Collect results for organized dry-run output
    if dry_run:
        would_fetch = []
        would_skip = []
        up_to_date = []
        errors = []
        
        for entry in file_entries:
            try:
                target_path, cache_key = get_target_path_and_cache_key(entry['path'], entry['target_dir'], entry['is_glob'])
                cache_file = Path(CACHE_DIR) / cache_key
                local_hash = hash_file(target_path)
                last_hash = None
                if cache_file.exists():
                    with open(cache_file) as cf:
                        last_hash = cf.read().strip()
                if local_hash and local_hash != last_hash and not force:
                    would_skip.append(f"{entry['path']} from {entry['repository']}")
                elif local_hash == last_hash and not entry.get('branch'):
                    short_commit = get_short_commit(entry['commit'])
                    status_display = f"HEAD detached at {short_commit}"
                    up_to_date.append(f"{entry['path']} from {entry['repository']} ({status_display})")
                else:
                    if entry.get('branch'):
                        status_info = f"On branch {entry['branch']}"
                        if save:
                            status_info += " -> [update to latest]"
                    else:
                        short_commit = get_short_commit(entry['commit'])
                        status_info = f"HEAD detached at {short_commit}"
                    would_fetch.append(f"{entry['path']} from {entry['repository']} ({status_info})")
            except Exception as e:
                errors.append(f"{entry['path']} from {entry['repository']}: {str(e)}")
        
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
            print("Already up to date.")
        
        return
    
    # Execute concurrent fetching by repository groups
    def fetch_repository_group(repository_key, entries):
        """Fetch all files from a single repository group as a batch."""
        repository, commit = repository_key
        results = []

        with tempfile.TemporaryDirectory(dir=TEMP_DIR) as temp_clone_dir:
            clone_dir = Path(temp_clone_dir)
            try:
                fetched_commit = clone_repository_at_commit(repository, commit, clone_dir)

                for entry in entries:
                    try:
                        path = entry['path']
                        is_glob = entry['is_glob']
                        target_dir = entry['target_dir']
                        files = [path]
                        if is_glob:
                            files = get_files_from_glob(clone_dir, path, repository)

                        files_processed = 0
                        files_updated = 0
                        files_up_to_date = 0
                        files_skipped = 0
                        
                        for f in files:
                            target_path, cache_key = get_target_path_and_cache_key(f, target_dir, is_glob)
                            cache_file = Path(CACHE_DIR) / cache_key
                            source_file = clone_dir / f
                            # Use helper function to handle file copying and caching
                            result = process_file_copy(source_file, target_path, cache_file, force, f, commit)
                            files_processed += 1
                            if result is True:
                                files_updated += 1
                            elif result == "up_to_date":
                                files_up_to_date += 1
                            else:  # False
                                files_skipped += 1
                        results.append({
                            'section': entry['section'],
                            'path': entry['path'],
                            'repository': entry['repository'],
                            'commit': entry['commit'],
                            'fetched_commit': fetched_commit,
                            'files_processed': files_processed,
                            'files_updated': files_updated,
                            'files_up_to_date': files_up_to_date,
                            'files_skipped': files_skipped,
                            'success': True,
                            'error': None
                        })
                    except Exception as e:
                        results.append({
                            'section': entry['section'],
                            'path': entry['path'],
                            'repository': entry['repository'],
                            'commit': entry['commit'],
                            'fetched_commit': None,
                            'files_processed': 0,
                            'files_updated': 0,
                            'files_up_to_date': 0,
                            'files_skipped': 0,
                            'success': False,
                            'error': str(e)
                        })
            except Exception as e:
                for entry in entries:
                    results.append({
                        'section': entry['section'],
                        'path': entry['path'],
                        'repository': entry['repository'],
                        'commit': entry['commit'],
                        'fetched_commit': None,
                        'files_processed': 0,
                        'files_updated': 0,
                        'files_up_to_date': 0,
                        'files_skipped': 0,
                        'success': False,
                        'error': str(e)
                    })
        return results
    
    # Determine default jobs if not set (limit to number of repo groups for efficiency)
    if jobs is None:
        try:
            jobs = min(os.cpu_count() or 1, len(repository_groups))
        except Exception:
            jobs = 1
    else:
        jobs = min(jobs, len(repository_groups))
    
    all_results = []
    with ThreadPoolExecutor(max_workers=jobs) as executor:
        # Submit tasks for each repository group
        future_to_repository = {executor.submit(fetch_repository_group, repository_key, entries): repository_key 
                         for repository_key, entries in repository_groups.items()}
        
        # Collect results as they complete
        for future in as_completed(future_to_repository):
            repository_results = future.result()
            all_results.extend(repository_results)
            
            # Print errors immediately for better user feedback
            for result in repository_results:
                if not result['success']:
                    print(f"error: fetching {result['path']}: {result['error']}")
    
    # Update config with new commits if save is enabled
    config_needs_save = config_migrated  # Save if we migrated any sections
    if save:
        updated = False
        for result in all_results:
            if result['success'] and result['commit'] != "HEAD":
                if not result['commit'].startswith(result['fetched_commit'][:7]):
                    config[result['section']]["commit"] = result['fetched_commit']
                    updated = True
        
        if updated:
            config_needs_save = True
    
    # Save config if needed (migration or updates)
    if config_needs_save:
        save_remote_files(config)
    
    # Check overall status and provide feedback (non-dry-run only)
    if not dry_run:
        total_updated = sum(result.get('files_updated', 0) for result in all_results if result['success'])
        total_up_to_date = sum(result.get('files_up_to_date', 0) for result in all_results if result['success'])
        total_skipped = sum(result.get('files_skipped', 0) for result in all_results if result['success'])
        total_errors = len([result for result in all_results if not result['success']])
        
        # Show "Already up to date." if no files were updated and no errors occurred
        if total_updated == 0 and total_errors == 0 and not config_migrated:
            if total_up_to_date > 0 or total_skipped == 0:
                print("Already up to date.")
    
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
            commit_changes(commit_message=commit_message, edit=edit, no_commit=no_commit, file_results=all_results)


def status_files():
    """Print all files tracked in .git-remote-files."""
    config = load_remote_files()
    
    if not config.sections():
        print("No remote files tracked.")
        return
    
    config_migrated = False
    for section in config.sections():
        path = section.split('"')[1]
        repository = get_repository_from_config(config, section)
        
        # Migrate section if needed
        if migrate_config_section(config, section):
            config_migrated = True
            
        commit = config[section]["commit"]  # Should always exist now
        target_dir = config[section].get("target", None)
        comment = config[section].get("comment", "")
        branch = config[section].get("branch", None)
        
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
        
        # Determine tracking status and display format like git status
        if branch:
            # This is a branch-tracking entry - like "On branch main"
            status_display = f"On branch {branch}"
            # Always show the current commit hash since commit is always a hash now
            short_commit = get_short_commit(commit)
            status_display += f" at {short_commit}"
        else:
            # This is a non-branch-tracked entry
            # Commit should always be a hash now, never "HEAD"
            short_commit = get_short_commit(commit)
            status_display = f"HEAD detached at {short_commit}"
        
        # Format like: path[glob_indicator] repository (status_display)
        line = f"{path_display}{glob_indicator}\t{repository} ({status_display})"
        
        # Add comment if present
        if comment:
            line += f" # {comment}"
        
        print(line)
    
    # Save config if any sections were migrated
    if config_migrated:
        save_remote_files(config)


def is_glob_pattern(path):
    """Check if a path contains glob pattern characters using glob.has_magic."""
    return glob_has_magic(path)


def get_target_path_and_cache_key(path, target_dir, is_glob):
    """
    Helper to determine the target path and cache key for a file or glob.
    Returns (target_path, cache_key)
    """
    relative_path = path.lstrip('/')
    if target_dir:
        if is_glob:
            target_path = Path(target_dir) / relative_path
            cache_key = f"{target_dir}_{relative_path}".replace("/", "_")
        else:
            target_path = Path(target_dir)
            if target_path.suffix:
                cache_key = str(target_path).replace("/", "_")
            else:
                filename = Path(relative_path).name
                target_path = target_path / filename
                cache_key = f"{target_dir}_{filename}".replace("/", "_")
    else:
        target_path = Path(relative_path)
        cache_key = relative_path.replace("/", "_")
    return target_path, cache_key


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
        print("warning: not in a git repository, skipping commit")
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
        print(f"warning: failed to commit changes: {e}")
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
    
    # Count files and analyze repositories
    file_count = len(successful_results)
    
    # Group by repository to create more informative messages
    repo_groups = {}
    for result in successful_results:
        # Extract repository name from URL (similar to how git displays remotes)
        repo_url = result.get('repository', '')
        if repo_url:
            # Extract repo name from URL (e.g., "owner/repo" from github URLs)
            if 'github.com' in repo_url or 'gitlab.com' in repo_url or 'bitbucket.org' in repo_url:
                # Handle git@github.com:owner/repo.git or https://github.com/owner/repo.git
                repo_name = repo_url.split('/')[-2:] if '/' in repo_url else [repo_url]
                if len(repo_name) == 2:
                    repo_name = f"{repo_name[0]}/{repo_name[1].replace('.git', '')}"
                else:
                    repo_name = repo_name[0].replace('.git', '')
            else:
                # For other URLs, use the last component
                repo_name = repo_url.split('/')[-1].replace('.git', '')
        else:
            repo_name = 'unknown'
        
        if repo_name not in repo_groups:
            repo_groups[repo_name] = []
        repo_groups[repo_name].append(result)
    
    # Generate message based on complexity
    if file_count == 1:
        result = successful_results[0]
        file_path = result['path']
        commit_hash = result.get('fetched_commit', '')
        repo_name = list(repo_groups.keys())[0]
        
        # Extract just the filename for cleaner display
        if '/' in file_path:
            file_name = file_path.split('/')[-1]
        else:
            file_name = file_path
        
        # Include commit info if available - always show it for single files
        if commit_hash and len(commit_hash) >= 7:
            short_hash = commit_hash[:7]
            return f"Update {file_name} from {repo_name}@{short_hash}"
        else:
            return f"Update {file_name} from {repo_name}"
    
    elif len(repo_groups) == 1:
        # All files from same repository
        repo_name = list(repo_groups.keys())[0]
        files = repo_groups[repo_name]
        
        if file_count <= 3:
            # List individual files for small counts
            file_names = []
            for result in files:
                file_path = result['path']
                if '/' in file_path:
                    file_name = file_path.split('/')[-1]
                else:
                    file_name = file_path
                file_names.append(file_name)
            
            # Get commit info from first file (assuming same commit for same repo)
            commit_hash = files[0].get('fetched_commit', '')
            commit_suffix = f"@{commit_hash[:7]}" if commit_hash and len(commit_hash) >= 7 else ""
            
            if file_count == 2:
                return f"Update {file_names[0]} and {file_names[1]} from {repo_name}{commit_suffix}"
            else:  # file_count == 3
                return f"Update {', '.join(file_names[:-1])}, and {file_names[-1]} from {repo_name}{commit_suffix}"
        else:
            # Use directory-based grouping for larger counts from same repo
            dirs = set()
            for result in files:
                file_path = result['path']
                if '/' in file_path:
                    dir_path = '/'.join(file_path.split('/')[:-1])
                    dirs.add(dir_path)
            
            commit_hash = files[0].get('fetched_commit', '')
            commit_suffix = f"@{commit_hash[:7]}" if commit_hash and len(commit_hash) >= 7 else ""
            
            if len(dirs) == 1 and dirs != {''}:
                dir_name = dirs.pop()
                return f"Update {file_count} files in {dir_name}/ from {repo_name}{commit_suffix}"
            else:
                return f"Update {file_count} files from {repo_name}{commit_suffix}"
    
    else:
        # Multiple repositories
        repo_count = len(repo_groups)
        if repo_count <= 3:
            # List repositories if not too many
            repo_names = list(repo_groups.keys())
            if repo_count == 2:
                return f"Update {file_count} files from {repo_names[0]} and {repo_names[1]}"
            else:  # repo_count == 3
                return f"Update {file_count} files from {', '.join(repo_names[:-1])}, and {repo_names[-1]}"
        else:
            return f"Update {file_count} files from {repo_count} repositories"


def get_repository_from_config(config, section):
    """
    Get repository URL from config section with backward compatibility.
    
    Args:
        config: ConfigParser instance
        section: Section name
    
    Returns:
        str: Repository URL
    """
    # Try new 'repository' key first, fall back to legacy 'repo' key
    if "repository" in config[section]:
        return config[section]["repository"]
    elif "repo" in config[section]:
        return config[section]["repo"]
    else:
        raise KeyError(f"No repository URL found in section {section}")


def migrate_config_section(config, section):
    """
    Migrate a config section from legacy format to current format.
    This includes:
    1. Migrating 'repo' key to 'repository' key
    2. Moving branch names from 'commit' field to 'branch' key, resolving commit to hash
    
    Args:
        config: ConfigParser instance
        section: Section name
    
    Returns:
        bool: True if migration occurred, False if already using new format
    """
    migrated = False
    
    # Migrate repo -> repository
    if "repo" in config[section] and "repository" not in config[section]:
        config[section]["repository"] = config[section]["repo"]
        del config[section]["repo"]
        migrated = True
    # Check if commit field contains a branch name instead of a commit hash
    if "commit" in config[section] and "branch" not in config[section]:
        commit_value = config[section]["commit"]
        repository = get_repository_from_config(config, section)
        is_likely_hash = (len(commit_value) == 40 and 
                         all(c in '0123456789abcdef' for c in commit_value.lower()))
        if not is_likely_hash or commit_value == "HEAD":
            try:
                actual_commit = resolve_commit_ref(repository, commit_value)
                if actual_commit != commit_value:
                    config[section]["branch"] = commit_value
                    config[section]["commit"] = actual_commit
                    migrated = True
                    print(f"Migrated '{commit_value}' from commit to branch tracking with hash '{actual_commit[:7]}' for {section}")
            except subprocess.CalledProcessError as e:
                print(f"warning: could not resolve commit reference '{commit_value}' for {section}: {e}")
    
    return migrated


def clone_repository_at_commit(repository, commit, clone_dir):
    """
    Clone a repository at a specific commit, branch, or tag.
    
    Args:
        repository (str): Remote repository URL.
        commit (str): Commit hash, branch, tag, or "HEAD".
        clone_dir (Path): Directory to clone into.
    
    Returns:
        str: The actual commit hash that was checked out.
    
    Raises:
        subprocess.CalledProcessError: If cloning fails.
    """
    if commit == "HEAD" or not commit:
        clone_cmd = ["git", "clone", "--depth", "1", repository, str(clone_dir)]
        subprocess.run(clone_cmd, capture_output=True, check=True)
    else:
        is_commit_hash = len(commit) == 40 and all(c in '0123456789abcdef' for c in commit.lower())
        if is_commit_hash:
            clone_cmd = ["git", "clone", repository, str(clone_dir)]
            subprocess.run(clone_cmd, capture_output=True, check=True)
            subprocess.run(
                ["git", "checkout", commit],
                cwd=clone_dir,
                capture_output=True,
                check=True
            )
        else:
            clone_cmd = ["git", "clone", "--depth", "1", "--branch", commit, repository, str(clone_dir)]
            subprocess.run(clone_cmd, capture_output=True, check=True)
    
    # Get the actual commit hash
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=clone_dir,
        capture_output=True,
        text=True,
        check=True
    )
    return result.stdout.strip()


def create_parser():
    """Create the argument parser for git-fetch-file."""
    parser = argparse.ArgumentParser(
        description='Fetch individual files or globs from other Git repositories',
        prog='git fetch-file'
    )
    subparsers = parser.add_subparsers(dest='command', help='Available commands')
    
    # Add subcommand
    add_parser = subparsers.add_parser('add', help='Add a file or glob to track')
    add_parser.add_argument('repository', help='Remote repository URL')
    add_parser.add_argument('path', help='File path or glob pattern')
    add_parser.add_argument('target_dir', nargs='?', help='Target directory to place the file')
    add_parser.add_argument('--detach', '--commit', dest='commit', 
                           help='Track specific commit/tag (detached)')
    add_parser.add_argument('-b', '--branch', help='Track specific branch')
    add_parser.add_argument('--glob', action='store_true', 
                           help='Force treat path as glob pattern')
    add_parser.add_argument('--no-glob', action='store_true',
                           help='Force treat path as literal file')
    add_parser.add_argument('--comment', help='Add descriptive comment')
    add_parser.add_argument('--dry-run', action='store_true',
                           help='Show what would be done')
    
    # Pull subcommand
    pull_parser = subparsers.add_parser('pull', help='Pull all tracked files')
    pull_parser.add_argument('--dry-run', action='store_true',
                            help='Show what would be done without executing')
    pull_parser.add_argument('--force', action='store_true',
                            help='Overwrite local changes')
    pull_parser.add_argument('--save', action='store_true',
                            help='Update commit hashes for branches')
    pull_parser.add_argument('--jobs', type=int, metavar='N',
                            help='Number of parallel jobs (default: auto)')
    pull_parser.add_argument('--commit', action='store_true',
                            help='Auto-commit with default message')
    pull_parser.add_argument('-m', '--message', dest='commit_message',
                            help='Commit with message')
    pull_parser.add_argument('--edit', action='store_true',
                            help='Edit commit message')
    pull_parser.add_argument('--no-commit', action='store_true',
                            help="Don't auto-commit changes")
    
    # Status/list subcommands
    subparsers.add_parser('status', help='List all tracked files')
    subparsers.add_parser('list', help='Alias for status')
    
    return parser


def main():
    """Command-line interface for git-fetch-file."""
    parser = create_parser()
    
    # Handle no arguments
    if len(sys.argv) < 2:
        parser.print_help()
        sys.exit(1)
    
    args = parser.parse_args()
    
    if args.command == 'add':
        # Handle glob flag logic
        glob_flag = None
        if args.glob and args.no_glob:
            print("error: --glob and --no-glob are mutually exclusive")
            sys.exit(1)
        elif args.glob:
            glob_flag = True
        elif args.no_glob:
            glob_flag = False
        
        add_file(
            args.repository, 
            args.path, 
            commit=args.commit,
            branch=args.branch, 
            glob=glob_flag, 
            comment=args.comment or "", 
            target_dir=args.target_dir, 
            dry_run=args.dry_run
        )
    
    elif args.command == 'pull':
        pull_files(
            force=args.force,
            save=args.save,
            dry_run=args.dry_run,
            jobs=args.jobs,
            commit_message=args.commit_message,
            edit=args.edit,
            no_commit=args.no_commit,
            auto_commit=args.commit
        )
    
    elif args.command in ('status', 'list'):
        status_files()
    
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
