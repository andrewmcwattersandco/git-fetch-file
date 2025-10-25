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
    """Load the .git-remote-files manifest from git repository root."""
    config = configparser.ConfigParser()
    git_root = get_git_root()
    if git_root:
        manifest_path = git_root / REMOTE_FILE_MANIFEST
    else:
        manifest_path = Path(REMOTE_FILE_MANIFEST)
    
    if manifest_path.exists():
        config.read(manifest_path)
    return config


def save_remote_files(config):
    """Write the .git-remote-files manifest to disk at git repository root."""
    from io import StringIO
    output = StringIO()
    config.write(output)
    content = output.getvalue().rstrip() + '\n'
    
    git_root = get_git_root()
    if git_root:
        manifest_path = git_root / REMOTE_FILE_MANIFEST
    else:
        manifest_path = Path(REMOTE_FILE_MANIFEST)
    
    with open(manifest_path, "w") as f:
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


def extract_path_from_section(section):
    """
    Extract file path from section name, handling multiple formats.
    
    Formats supported:
    - Old format: [file "path/to/file.txt"]
    - New format: [file "path/to/file.txt" from "repository_url"]
    
    Args:
        section (str): Section name from config file
        
    Returns:
        str: The file path
    """
    # Split by quotes and take the second element (first quoted string)
    parts = section.split('"')
    if len(parts) >= 2:
        return parts[1]
    else:
        # Fallback for malformed section names
        return section.replace('file ', '').strip('"')



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


def process_file_copy(source_file, target_path, cache_file, force, file_path, commit, is_branch_update=False):
    """Handle the actual file copying, caching, and conflict detection."""
    local_hash = hash_file(target_path)
    last_hash = None
    if cache_file.exists():
        with open(cache_file) as cf:
            last_hash = cf.read().strip()
    
    # Check if local file has changes compared to what git-fetch-file last fetched
    has_local_changes = local_hash and local_hash != last_hash
    
    if source_file.exists():
        source_hash = hash_file(source_file)
        # Check if file is already up to date with what we're trying to fetch
        if local_hash == source_hash:
            # File is already up to date, but update cache to track it
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w") as cf:
                cf.write(source_hash)
            return "up_to_date"
        
        # If we have local changes and not forcing, skip unless it's a branch update
        # Branch updates are expected and shouldn't be considered "local changes"
        if has_local_changes and not force and not is_branch_update:
            print(f"Skipping {file_path.lstrip('/')}: local changes detected. Use --force to overwrite.")
            return False
        
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


def get_default_branch(repository):
    """
    Get the default branch name of a remote repository.
    
    Args:
        repository (str): Remote repository URL.
        
    Returns:
        str: The default branch name (e.g., "main", "master").
        
    Raises:
        subprocess.CalledProcessError: If the repository cannot be accessed.
    """
    try:
        # Use git ls-remote --symref to get the default branch
        result = subprocess.run(
            ["git", "ls-remote", "--symref", repository, "HEAD"],
            capture_output=True,
            text=True,
            check=True
        )
        
        # Parse the output to find the symbolic reference
        # Output format: "ref: refs/heads/main\tHEAD\n<commit_hash>\tHEAD"
        lines = result.stdout.strip().split('\n')
        for line in lines:
            if line.startswith('ref: refs/heads/'):
                # Extract branch name from "ref: refs/heads/branch_name"
                return line.split('refs/heads/')[-1].split('\t')[0]
        
        # Fallback: if symbolic ref not found, assume "main" or "master"
        # Try to detect which one exists
        result = subprocess.run(
            ["git", "ls-remote", "--heads", repository],
            capture_output=True,
            text=True,
            check=True
        )
        
        heads = result.stdout.strip()
        if 'refs/heads/main' in heads:
            return "main"
        elif 'refs/heads/master' in heads:
            return "master"
        else:
            # Use the first branch found
            for line in heads.split('\n'):
                if 'refs/heads/' in line:
                    return line.split('refs/heads/')[-1]
            
            # Ultimate fallback - use Git's default
            return "master"
            
    except subprocess.CalledProcessError:
        # If we can't determine the default branch, fall back to Git's default
        return "master"


def add_file(repository, path, commit=None, branch=None, glob=None, comment="", target_dir=None, dry_run=False, force=False):
    """
    Add a file or glob from a remote repository to .git-remote-files.

    Args:
        repository (str): Remote repository URL.
        path (str): File path or glob pattern.
        commit (str, optional): Specific commit hash to detach at (legacy parameter, use --detach).
        branch (str, optional): Branch or tag name to track. If neither branch nor commit 
                               is specified, the repository's default branch will be used.
        glob (bool, optional): Whether path is a glob pattern. Auto-detected if None.
        comment (str): Optional comment describing the file.
        target_dir (str, optional): Target directory to place the file. Defaults to same path.
        dry_run (bool): If True, only show what would be done without executing.
        force (bool): If True, overwrite existing entries for the same file path.
    """
    # Normalize path by removing leading slash
    path = path.lstrip('/')
    
    # Determine the commit reference and remote-tracking behavior
    # Priority: explicit commit > explicit branch > default branch of repository
    if commit:
        commit_ref = commit
        is_tracking_branch = False
    elif branch:
        commit_ref = branch
        is_tracking_branch = True
    else:
        # No branch or commit specified - use repository's default branch like git clone
        try:
            default_branch = get_default_branch(repository)
            commit_ref = default_branch
            is_tracking_branch = True
            # Set branch to default_branch so it gets tracked properly
            branch = default_branch
        except subprocess.CalledProcessError:
            print(f"fatal: repository '{repository}' not found or inaccessible")
            sys.exit(1)
    
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
    
    # Calculate the target path that would be stored in the manifest
    current_dir_relative_to_git_root = get_relative_path_from_git_root()
    manifest_target = get_manifest_target_path(target_dir, current_dir_relative_to_git_root)
    
    # Create a unique section name using repository URL for uniqueness
    section = f'file "{path}" from "{repository}"'
    
    # Check for conflicts: same file path with same target location
    conflicting_section = None
    existing_entry_for_same_file = None
    for existing_section in config.sections():
        existing_path = extract_path_from_section(existing_section)
        if existing_path == path:
            existing_repository = get_repository_from_config(config, existing_section)
            existing_target_dir = config[existing_section].get("target", None)
            
            if existing_repository == repository:
                # Same file from same repository
                existing_entry_for_same_file = existing_section
                
                # Check if targets conflict (same target location)
                if existing_target_dir == manifest_target:
                    # Same file, same repo, same target - allow force override
                    if not force:
                        print(f"fatal: '{path}' already tracked from {existing_repository}", file=sys.stderr)
                        print("hint: use --force to overwrite", file=sys.stderr)
                        sys.exit(1)
                    else:
                        # Force flag used - remember this section to remove it
                        conflicting_section = existing_section
                        break
                # If different target, we'll update the existing entry to use the new target
            else:
                # Same file, different repo - check for target conflicts
                if existing_target_dir == manifest_target:
                    # Same file, different repo, same target - this would cause filesystem conflict
                    print(f"fatal: '{path}' would conflict with existing file from {existing_repository}", file=sys.stderr)
                    if manifest_target:
                        print("hint: specify a different target directory", file=sys.stderr)
                    else:
                        print("hint: specify a target directory to avoid conflict", file=sys.stderr)
                    sys.exit(1)
    
    # Remove conflicting section if force is used
    if conflicting_section:
        config.remove_section(conflicting_section)
    
    # If we found an existing entry for the same file/repo but different target,
    # we should update that entry instead of creating a new one
    if existing_entry_for_same_file and not conflicting_section:
        # Update the existing entry to use the new target and commit
        section = existing_entry_for_same_file
    
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
    
    # Always store the resolved commit hash, never a branch name
    config[section]["commit"] = actual_commit
    
    # Defensive check: ensure we're not storing a branch name as commit
    if not (len(actual_commit) == 40 and all(c in '0123456789abcdef' for c in actual_commit.lower())):
        print(f"warning: commit value '{actual_commit}' does not look like a valid hash")
    
    # Set remote-tracking information
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
    
    if manifest_target:
        config[section]["target"] = manifest_target
    
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
    temp_dir = get_temp_dir()
    temp_dir.mkdir(parents=True, exist_ok=True)
    get_cache_dir().mkdir(parents=True, exist_ok=True)

    fetched_commit = commit

    # In dry-run mode, we skip most of the actual work since pull_files handles the summary
    if dry_run:
        return fetched_commit

    # Ensure TEMP_DIR exists before using it for TemporaryDirectory
    with tempfile.TemporaryDirectory(dir=temp_dir) as temp_clone_dir:
        clone_dir = Path(temp_clone_dir)
        try:
            # Clone the repository and get the actual commit hash
            fetched_commit = clone_repository_at_commit(repository, commit, clone_dir)

            files = [path]
            if is_glob:
                files = get_files_from_glob(clone_dir, path, repository)

            for f in files:
                target_path, cache_key = get_target_path_and_cache_key(f, target_dir, is_glob)
                cache_file = get_cache_dir() / cache_key
                source_file = clone_dir / f
                # Use helper function to handle file copying and caching
                # fetch_file is usually called directly, not for branch updates
                process_file_copy(source_file, target_path, cache_file, force, f, commit, is_branch_update=False)
        except subprocess.CalledProcessError as e:
            print(f"fatal: failed to clone repository: {e}")
            raise RuntimeError(f"failed to clone repository: {e}")
    return fetched_commit



def pull_files(force=False, dry_run=False, jobs=None, commit_message=None, edit=False, no_commit=False, auto_commit=False, save=False, repo=None, paths=None):
    """
    Pull all tracked files from .git-remote-files.

    Args:
        force (bool): Overwrite local changes if True.
        dry_run (bool): If True, only show what would be done without executing.
        jobs (int): Number of concurrent jobs for fetching files.
        commit_message (str, optional): Custom commit message for auto-commit.
        edit (bool): Whether to open editor for commit message.
        no_commit (bool): If True, don't auto-commit changes.
        auto_commit (bool): If True, auto-commit with default message.
        save (bool): Deprecated parameter, ignored (remote-tracking files now update automatically).
        repo (str, optional): Only pull files from the given repository.
        paths (list[str], optional): Only files residing under the given path in this repository.
    """
    # Show deprecation warning for --save flag
    if save:
        print("warning: --save is deprecated. Remote-tracking files now update automatically.", file=sys.stderr)

    config = load_remote_files()
    
    if not config.sections():
        if dry_run:
            print("No remote files tracked.")
        return
    
    # Collect file entries to process
    file_entries = []
    config_migrated = False
    
    # First pass: collect sections to migrate (avoid modifying during iteration)
    sections_to_migrate = []
    for section in config.sections():
        sections_to_migrate.append(section)
    
    # Second pass: migrate sections
    for section in sections_to_migrate:
        if section not in config.sections():
            # Section was already migrated and renamed
            continue
            
        if migrate_config_section(config, section):
            config_migrated = True
    
    limit_repo = None
    if repo is not None:
        limit_repo = expand_repo_url(repo)

    # Third pass: collect file entries from (potentially renamed) sections
    for section in config.sections():
        path = extract_path_from_section(section)
        repository = get_repository_from_config(config, section)
            
        commit = config[section]["commit"]  # Should always exist now
        branch = config[section].get("branch", None)
        target_dir = config[section].get("target", None)
        # Check if glob was explicitly set, otherwise auto-detect
        if "glob" in config[section]:
            is_glob = config[section].getboolean("glob", False)
        else:
            is_glob = is_glob_pattern(path)
        
        # Restrict to files imported from a given repository.
        if limit_repo is not None:
            remote_repo = expand_repo_url(repository)
            if limit_repo != remote_repo:
                continue
        # Restrict to files imported under a given path.
        if paths is not None:
            pathlike = Path(target_dir)
            if not any(map(lambda p: pathlike.is_relative_to(p), paths)):
                continue

        file_entries.append({
            'section': section,
            'path': path,
            'repository': repository,
            'commit': commit,
            'branch': branch,
            'target_dir': target_dir,
            'is_glob': is_glob
        })
    
    # Resolve branch commits to latest if remote-tracking files are enabled
    for entry in file_entries:
        if entry['branch']:
            # For remote-tracking files, always resolve to latest commit on branch
            try:
                latest_commit = resolve_commit_ref(entry['repository'], entry['branch'])
                entry['target_commit'] = latest_commit
                entry['commit_updated'] = latest_commit != entry['commit']
            except subprocess.CalledProcessError:
                print(f"warning: failed to resolve branch '{entry['branch']}' for {entry['path']}")
                entry['target_commit'] = entry['commit']
                entry['commit_updated'] = False
        else:
            # Use stored commit for non-branch files (detached HEAD)
            entry['target_commit'] = entry['commit']
            entry['commit_updated'] = False
    
    # Group files by repository and target commit to avoid concurrent cloning of the same repo
    repository_groups = {}
    for entry in file_entries:
        repository_key = (entry['repository'], entry['target_commit'])
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
                cache_file = get_cache_dir() / cache_key
                local_hash = hash_file(target_path)
                last_hash = None
                if cache_file.exists():
                    with open(cache_file) as cf:
                        last_hash = cf.read().strip()
                
                # Check if local file matches what was last fetched from git-fetch-file
                has_local_changes = local_hash and local_hash != last_hash
                
                if has_local_changes and not force:
                    would_skip.append(f"{entry['path']} from {entry['repository']}")
                elif not has_local_changes and not entry.get('commit_updated'):
                    # File is up to date and no new commit to fetch
                    if entry.get('branch'):
                        status_display = f"On branch {entry['branch']} at {get_short_commit(entry['target_commit'])}"
                    else:
                        status_display = f"HEAD detached at {get_short_commit(entry['commit'])}"
                    up_to_date.append(f"{entry['path']} from {entry['repository']} ({status_display})")
                else:
                    # File needs to be fetched (local changes with force, or new commit available)
                    if entry.get('branch'):
                        status_info = f"On branch {entry['branch']}"
                        if entry.get('commit_updated'):
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

        # Ensure TEMP_DIR exists before using it
        temp_dir = get_temp_dir()
        temp_dir.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(dir=temp_dir) as temp_clone_dir:
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
                            cache_file = get_cache_dir() / cache_key
                            source_file = clone_dir / f
                            # Use helper function to handle file copying and caching
                            # Branch updates are expected changes, not "local changes"
                            is_branch_update = entry.get('commit_updated', False)
                            result = process_file_copy(source_file, target_path, cache_file, force, f, commit, is_branch_update)
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
                            'branch': entry.get('branch'),
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
                            'branch': entry.get('branch'),
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
                        'branch': entry.get('branch'),
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
    jobs = max(jobs, 1)
    
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
    
    # Update config with new commits for remote-tracking files
    config_needs_save = config_migrated  # Save if we migrated any sections
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


def remove_file(path, target_dir=None, repository=None, dry_run=False):
    """
    Remove a tracked file from .git-remote-files.

    Args:
        path (str): File path to stop tracking.
        target_dir (str, optional): Target directory to disambiguate entries.
        repository (str, optional): Repository URL to disambiguate entries.
        dry_run (bool): If True, only show what would be done without executing.
    
    Returns:
        bool: True if file was found and removed, False otherwise.
    """
    # Normalize path by removing leading slash
    path = path.lstrip('/')
    
    config = load_remote_files()
    
    # Find all sections that match this path, target, and repository
    matching_sections = []
    for section in config.sections():
        section_path = extract_path_from_section(section)
        if section_path == path:
            section_target_dir = config[section].get("target", None)
            section_repository = get_repository_from_config(config, section)
            
            # Check if target_dir matches (if specified)
            if target_dir is not None and section_target_dir != target_dir:
                continue
                
            # Check if repository matches (if specified)
            if repository is not None and section_repository != repository:
                continue
                
            matching_sections.append(section)
    
    if not matching_sections:
        error_msg = f"error: file '{path}'"
        if target_dir:
            error_msg += f" with target '{target_dir}'"
        if repository:
            error_msg += f" from repository '{repository}'"
        error_msg += " is not currently tracked"
        print(error_msg)
        return False
    
    if len(matching_sections) == 1:
        section = matching_sections[0]
        if dry_run:
            repository = get_repository_from_config(config, section)
            section_target_dir = config[section].get("target", None)
            target_info = f" -> {section_target_dir}" if section_target_dir else ""
            print(f"Would remove tracking for '{path}{target_info}' from {repository}")
            return True
        
        # Remove the section
        repository = get_repository_from_config(config, section)
        section_target_dir = config[section].get("target", None)
        target_info = f" -> {section_target_dir}" if section_target_dir else ""
        config.remove_section(section)
        save_remote_files(config)
        
        print(f"Removed tracking for '{path}{target_info}' from {repository}")
        return True
    else:
        # Multiple sections found - need user to specify which one
        print(f"error: multiple entries found for '{path}'. Please specify which one to remove:")
        for i, section in enumerate(matching_sections, 1):
            repository = get_repository_from_config(config, section)
            section_target_dir = config[section].get("target", None)
            target_info = f" -> {section_target_dir}" if section_target_dir else ""
            print(f"  {i}. {path}{target_info} from {repository}")
        
        # Provide helpful guidance in git style
        has_targets = any(config[section].get("target") for section in matching_sections)
        has_different_repos = len(set(get_repository_from_config(config, section) for section in matching_sections)) > 1
        
        if has_targets:
            print(f"hint: specify target directory: git fetch-file remove '{path}' <target_dir>")
        if has_different_repos:
            print(f"hint: specify repository: git fetch-file remove '{path}' --repository <repository_url>")
        
        return False


def status_files():
    """Print all files tracked in .git-remote-files."""
    config = load_remote_files()
    
    if not config.sections():
        print("No remote files tracked.")
        return
    
    config_migrated = False
    
    # First pass: collect sections to migrate (avoid modifying during iteration)
    sections_to_migrate = []
    for section in config.sections():
        sections_to_migrate.append(section)
    
    # Second pass: migrate sections
    for section in sections_to_migrate:
        if section not in config.sections():
            # Section was already migrated and renamed
            continue
            
        if migrate_config_section(config, section):
            config_migrated = True
    
    # Third pass: display status from (potentially renamed) sections
    for section in config.sections():
        path = extract_path_from_section(section)
        repository = get_repository_from_config(config, section)
            
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
            # This is a remote-tracking entry - like "On branch main"
            status_display = f"On branch {branch}"
            # Always show the current commit hash since commit is always a hash now
            short_commit = get_short_commit(commit)
            status_display += f" at {short_commit}"
        else:
            # This is a detached HEAD entry
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
    Target paths are relative to the current working directory where git fetch-file is run.
    Returns (target_path, cache_key)
    """
    relative_path = path.lstrip('/')
    
    if target_dir:
        # Target directory is specified
        if is_glob:
            # For globs, preserve the full path structure under target directory
            target_path = Path(target_dir) / relative_path
            cache_key = f"{target_dir}_{relative_path}".replace("/", "_")
        else:
            # For single files, handle as either directory or file target
            target_path = Path(target_dir)
            if target_path.suffix:
                # target_dir appears to be a file path, use it directly
                cache_key = str(target_path).replace("/", "_")
            else:
                # target_dir is a directory, place file inside it
                filename = Path(relative_path).name
                target_path = target_path / filename
                cache_key = f"{target_dir}_{filename}".replace("/", "_")
    else:
        # No target directory - place relative to current working directory
        target_path = Path(relative_path)
        cache_key = relative_path.replace("/", "_")
    
    return target_path, cache_key


def get_git_root():
    """Get the root directory of the git repository."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True
        )
        return Path(result.stdout.strip())
    except subprocess.CalledProcessError:
        return None


def expand_repo_url(url):
    """Expand a repository URL with `insteadOf` replacements."""
    try:
        result = subprocess.run(
            ["git", "ls-remote", "--get-url", url],
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return url


def get_cache_dir():
    """Get the cache directory path relative to git repository root."""
    git_root = get_git_root()
    if git_root:
        return git_root / CACHE_DIR
    else:
        return Path(CACHE_DIR)


def get_temp_dir():
    """Get the temporary directory path relative to git repository root."""
    git_root = get_git_root()
    if git_root:
        return git_root / TEMP_DIR
    else:
        return Path(TEMP_DIR)


def get_manifest_target_path(target_dir, current_working_dir_relative_to_git_root):
    """
    Calculate the target path to store in the manifest file.
    
    The target path in the manifest should be relative to the git repository root,
    but account for where the command was run from.
    
    Args:
        target_dir (str): Target directory specified by user (or None)
        current_working_dir_relative_to_git_root (Path): Current directory relative to git root
    
    Returns:
        str or None: Target path to store in manifest, or None if no target specified
    """
    if not target_dir:
        # If no target specified, files go to current working directory relative to git root
        if current_working_dir_relative_to_git_root == Path('.'):
            # Command run from git root, no target path needed
            return None
        else:
            # Command run from subdirectory, target is that subdirectory
            return str(current_working_dir_relative_to_git_root)
    
    # Target directory was specified
    target_path = Path(target_dir)
    
    # If target is absolute, use as-is (though this is unusual)
    if target_path.is_absolute():
        return target_dir
    
    # Target is relative - combine with current working directory
    if current_working_dir_relative_to_git_root == Path('.'):
        # Command run from git root, target is relative to root
        return target_dir
    else:
        # Command run from subdirectory, target is relative to that subdirectory
        combined_target = current_working_dir_relative_to_git_root / target_path
        return str(combined_target)


def get_relative_path_from_git_root():
    """Get the directory where git command was invoked, relative to git repository root."""
    # Git sets GIT_PREFIX to the relative path from repo root to where command was invoked
    git_prefix = os.environ.get('GIT_PREFIX', '')
    
    if git_prefix:
        # Remove trailing slash if present
        git_prefix = git_prefix.rstrip('/')
        return Path(git_prefix)
    else:
        # No GIT_PREFIX means command was run from repository root
        return Path('.')


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
        branch = result.get('branch')
        repo_name = list(repo_groups.keys())[0]
        
        # Extract just the filename for cleaner display
        if '/' in file_path:
            file_name = file_path.split('/')[-1]
        else:
            file_name = file_path
        
        # Prioritize branch name over commit hash for tracking info
        if branch:
            return f"Update {file_name} from {repo_name}#{branch}"
        elif commit_hash and len(commit_hash) >= 7:
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
            
            # Get tracking info from first file (assuming same for same repo)
            first_result = files[0]
            branch = first_result.get('branch')
            commit_hash = first_result.get('fetched_commit', '')
            
            if branch:
                tracking_suffix = f"#{branch}"
            elif commit_hash and len(commit_hash) >= 7:
                tracking_suffix = f"@{commit_hash[:7]}"
            else:
                tracking_suffix = ""
            
            if file_count == 2:
                return f"Update {file_names[0]} and {file_names[1]} from {repo_name}{tracking_suffix}"
            else:  # file_count == 3
                return f"Update {', '.join(file_names[:-1])}, and {file_names[-1]} from {repo_name}{tracking_suffix}"
        else:
            # Use directory-based grouping for larger counts from same repo
            dirs = set()
            for result in files:
                file_path = result['path']
                if '/' in file_path:
                    dir_path = '/'.join(file_path.split('/')[:-1])
                    dirs.add(dir_path)
            
            first_result = files[0]
            branch = first_result.get('branch')
            commit_hash = first_result.get('fetched_commit', '')
            
            if branch:
                tracking_suffix = f"#{branch}"
            elif commit_hash and len(commit_hash) >= 7:
                tracking_suffix = f"@{commit_hash[:7]}"
            else:
                tracking_suffix = ""
            
            if len(dirs) == 1 and dirs != {''}:
                dir_name = dirs.pop()
                return f"Update {file_count} files in {dir_name}/ from {repo_name}{tracking_suffix}"
            else:
                return f"Update {file_count} files from {repo_name}{tracking_suffix}"
    
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
    Get repository URL from section name or config key for backward compatibility.
    
    Args:
        config: ConfigParser instance
        section: Section name
    
    Returns:
        str: Repository URL
    """
    # Extract from section name for new format: [file "path" from "repository_url"]
    if ' from "' in section:
        parts = section.split(' from "')
        if len(parts) == 2 and parts[1].endswith('"'):
            return parts[1][:-1]  # Remove the trailing quote
    
    # Fall back to config keys for backward compatibility
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
    1. Migrating section name from old format to new format
    2. Migrating 'repo' key to 'repository' key  
    3. Moving branch names from 'commit' field to 'branch' key, resolving commit to hash
    
    Args:
        config: ConfigParser instance
        section: Section name
    
    Returns:
        bool: True if migration occurred, False if already using new format
    """
    migrated = False
    section_data = dict(config[section])  # Copy section data
    
    # Check if section name needs migration (old format: [file "path"])
    needs_section_migration = not (' from "' in section and section.endswith('"'))
    
    # Migrate repo -> repository
    if "repo" in section_data and "repository" not in section_data:
        section_data["repository"] = section_data["repo"]
        del section_data["repo"]
        migrated = True
    
    # Get repository URL for further processing
    if "repository" in section_data:
        repository = section_data["repository"]
    elif "repo" in section_data:
        repository = section_data["repo"]
    else:
        # Can't migrate without repository information
        return migrated
    
    # Check if commit field contains a branch name instead of a commit hash
    if "commit" in section_data and "branch" not in section_data:
        commit_value = section_data["commit"]
        is_likely_hash = (len(commit_value) == 40 and 
                         all(c in '0123456789abcdef' for c in commit_value.lower()))
        
        # Don't migrate "HEAD" - it's a valid commit reference, not a branch name
        if not is_likely_hash and commit_value != "HEAD":
            try:
                actual_commit = resolve_commit_ref(repository, commit_value)
                if actual_commit != commit_value:
                    section_data["branch"] = commit_value
                    section_data["commit"] = actual_commit
                    migrated = True
                    print(f"Migrated '{commit_value}' from commit to tracking branch with hash '{actual_commit[:7]}' for {section}")
            except subprocess.CalledProcessError as e:
                print(f"warning: could not resolve commit reference '{commit_value}' for {section}: {e}")
        elif commit_value == "HEAD":
            # For "HEAD", migrate to track the repository's default branch
            try:
                default_branch = get_default_branch(repository)
                actual_commit = resolve_commit_ref(repository, default_branch)
                section_data["branch"] = default_branch
                section_data["commit"] = actual_commit
                migrated = True
                print(f"Migrated '{commit_value}' to track default branch '{default_branch}' with hash '{actual_commit[:7]}' for {section}")
            except subprocess.CalledProcessError as e:
                print(f"warning: could not resolve default branch for '{commit_value}' for {section}: {e}")
    
    # Migrate section name if needed
    if needs_section_migration:
        path = extract_path_from_section(section)
        new_section = f'file "{path}" from "{repository}"'
        
        # Remove old section and add new one
        config.remove_section(section)
        config.add_section(new_section)
        
        # Copy all data to new section, except repository key (now in section name)
        for key, value in section_data.items():
            if key != "repository":  # Don't duplicate repository in new format
                config[new_section][key] = value
        
        migrated = True
        print(f"Migrated section name from '{section}' to '{new_section}'")
    else:
        # For sections that are already in new format, remove any redundant repository key
        if "repository" in section_data:
            del section_data["repository"]
            migrated = True
            print(f"Removed redundant repository key from section '{section}'")
        
        # Update existing section with migrated data
        for key, value in section_data.items():
            config[section][key] = value
    
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
    add_parser.add_argument('-b', '--branch', help='Track specific branch')
    add_parser.add_argument('--comment', help='Add descriptive comment')
    add_parser.add_argument('--detach', '--commit', dest='commit', 
                           help='Track specific commit/tag (detached)')
    add_parser.add_argument('--dry-run', action='store_true',
                           help='Show what would be done')
    add_parser.add_argument('--force', action='store_true',
                           help='Overwrite existing entry for same file path')
    add_parser.add_argument('--glob', action='store_true', 
                           help='Force treat path as glob pattern')
    add_parser.add_argument('--no-glob', action='store_true',
                           help='Force treat path as literal file')
    
    # Pull subcommand
    pull_parser = subparsers.add_parser('pull', help='Pull all tracked files')
    pull_parser.add_argument('--commit', action='store_true',
                            help='Auto-commit with default message')
    pull_parser.add_argument('--dry-run', action='store_true',
                            help='Show what would be done without executing')
    pull_parser.add_argument('--edit', action='store_true',
                            help='Edit commit message')
    pull_parser.add_argument('--force', action='store_true',
                            help='Overwrite local changes')
    pull_parser.add_argument('--jobs', type=int, metavar='N',
                            help='Number of parallel jobs (default: auto)')
    pull_parser.add_argument('--no-commit', action='store_true',
                            help="Don't auto-commit changes")
    pull_parser.add_argument('-m', '--message', dest='commit_message',
                            help='Commit with message')
    pull_parser.add_argument('-r', '--repository',
                            help='Limit updates to files coming from a given repository')
    pull_parser.add_argument('-p', '--path', action='append', dest='paths',
                            help='Limit to files under a given path')
    pull_parser.add_argument('--save', action='store_true',
                            help='(Deprecated) Remote-tracking files now update automatically')
    
    # Status/list subcommands
    subparsers.add_parser('status', help='List all tracked files')
    subparsers.add_parser('list', help='Alias for status')
    
    # Remove subcommand
    remove_parser = subparsers.add_parser('remove', help='Remove a tracked file')
    remove_parser.add_argument('path', help='File path to stop tracking')
    remove_parser.add_argument('target_dir', nargs='?', help='Target directory (required if multiple entries exist)')
    remove_parser.add_argument('--dry-run', action='store_true',
                              help='Show what would be done')
    remove_parser.add_argument('--repository', help='Repository URL to disambiguate entries')
    
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
            dry_run=args.dry_run,
            force=args.force
        )
    
    elif args.command == 'pull':
        pull_files(
            force=args.force,
            dry_run=args.dry_run,
            jobs=args.jobs,
            commit_message=args.commit_message,
            edit=args.edit,
            no_commit=args.no_commit,
            auto_commit=args.commit,
            save=args.save,
            repo=args.repository,
            paths=args.paths,
        )
    
    elif args.command in ('status', 'list'):
        status_files()
    
    elif args.command == 'remove':
        remove_file(args.path, target_dir=args.target_dir, repository=args.repository, dry_run=args.dry_run)
    
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
