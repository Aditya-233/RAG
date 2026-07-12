#!/usr/bin/env python3

import fnmatch
import hashlib
import json
import os
import sys
import time
import zlib
from pathlib import Path


class RepositoryError(Exception):
    pass


def find_repo_root(start_dir: str) -> Path:
    current = Path(start_dir).resolve()

    # In all OS root folder parent is root itself...
    while current.parent != current:
        if (current / ".rag").is_dir():
            return current

        current = current.parent

    # .rag may exist in root folder but that's dangerous so we will put a check in init to ensure this never happens
    raise RepositoryError("Not a R.A.G. repository: .rag missing")


def read_object(repo_root: Path, sha: str) -> bytes:
    obj_path = repo_root / ".rag" / "objects" / sha[:2] / sha[2:]
    if not obj_path.is_file():
        raise RepositoryError(f"Object not found: {sha}")

    raw_data = zlib.decompress(obj_path.read_bytes())
    null_idx = raw_data.find(b"\0")

    # return encoded-content
    return raw_data[null_idx + 1 :]


def write_object(repo_root: Path, obj_type: str, content: bytes) -> str:
    # Git follows {`obj-type` `length`b\0`content`}
    # Since file-content is in bytes need to convert header into bytes too
    header = f"{obj_type} {len(content)}".encode() + b"\0"

    # SHA1 contain 160 bit -> reduce to 40 using hexadecimal.
    # Using iterative .update() to prevent massive RAM spikes on large files.
    hash_obj = hashlib.sha1()
    hash_obj.update(header)
    hash_obj.update(content)
    sha = hash_obj.hexdigest()

    obj_path = repo_root / ".rag" / "objects" / sha[:2] / sha[2:]

    # If 2 sub-folder contain the same file they will reach this point we have to ensure they don't get stored twice
    if not obj_path.exists():
        # Create objects/sha[:2]/sha[2:] and we do atomic operation since write_bytes takes long time
        # We append os.getpid() to the temp file to prevent race conditions from concurrent processes
        obj_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = obj_path.parent / f"tmp_{sha[2:]}_{os.getpid()}"

        compressed = zlib.compress(header + content)
        tmp_path.write_bytes(compressed)
        tmp_path.replace(obj_path)

    return sha


def read_index(repo_root: Path) -> dict[str, list]:
    # We are certain that index_path exists since we created a empty one upon rag init
    index_path = repo_root / ".rag" / "index"

    try:
        # Schema: { path/to/file: [mode, sha, mtime, size] }
        # in mode -> "100644" for normal files or "100755" for executable files is the git-standard
        # in sha -> 40 character unique ID for each file (object blob hash)
        # in mtime -> when it was modified last time
        # in size -> size of the file
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def write_index(repo_root: Path, entries: dict[str, list]) -> None:
    # We have modified the index file but need to write it in users directory too
    index_path = repo_root / ".rag" / "index"

    # We use a temporary lock file to write atomically. If the system crashes mid-write,
    # the original index file won't be corrupted with partial JSON data.
    lock_path = index_path.with_suffix(".lock")

    # Convert the dictionary to a JSON so that reversing JSON -> dictionary is easy.
    # We use sort_keys=True natively to ensure the index file is identically sorted across all OS without manual loops.
    json_string = json.dumps(entries, indent=2, sort_keys=True)

    lock_path.write_text(json_string, encoding="utf-8")
    lock_path.replace(index_path)


def get_head(repo_root: Path) -> str | None:
    main_branch_file = repo_root / ".rag" / "refs" / "heads" / "main"

    # Return None for the first commit if the branch file doesn't exist yet
    if not main_branch_file.is_file():
        return None

    return main_branch_file.read_text(encoding="utf-8").strip()


def load_gitignore(repo_root: Path) -> list[str]:
    ignore_path = repo_root / ".ragignore"

    # It's not compulsory to have a .ragignore
    if not ignore_path.is_file():
        return []

    rules = []

    # Maintaining utf-8 everywhere since Windows doesn't do it by default
    for line in ignore_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()

        # In case user has a empty line b/w content we have ignore it and in .ragignore `#` are for comments
        # line[0] == "#" works but this is more easy to read
        if line and not line.startswith("#"):
            rules.append(line)
    return rules


def is_ignored(rel_path: str, is_dir: bool, ignore_rules: list[str]) -> bool:
    parts = rel_path.split("/")

    # No need to track .rag directory internals
    if ".rag" in parts:
        return True

    for rule in ignore_rules:
        pattern = rule

        # In .ragignore ending with / -> ignore all folder with that name
        is_dir_only = False
        if rule.endswith("/"):
            is_dir_only = True
            pattern = rule[:-1]

        # In .ragignore starting with / -> ignore folder in folder where .ragignore is present
        if rule.startswith("/"):
            pattern = rule[1:]

        # If the current entry is a directory and we are actually looking for files, since directory and file can share the same name
        if is_dir_only and not is_dir:
            continue

        # 1. pattern is src/logs/*.log and the rel_path is src/logs/error.log then OK
        # 2. pattern is .mp4 and the rel_path is src/media/asset/vid.mp4
        if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(parts[-1], pattern):
            return True

    return False


def get_workspace_files(repo_root: Path) -> dict:
    ignore_rules = load_gitignore(repo_root)
    workspace = {}

    def scan_dir(current_path: Path, current_rel_folder: str):
        # We use os.scandir instead of os.walk. scandir caches the file metadata automatically
        # so we don't have to make redundant system stat() calls, making it vastly faster.
        for entry in os.scandir(current_path):
            entry_name = entry.name
            rel_path = current_rel_folder + entry_name

            if entry.is_dir(follow_symlinks=False):
                dir_rel_path = rel_path + "/"
                if not is_ignored(dir_rel_path, True, ignore_rules):
                    scan_dir(Path(entry.path), dir_rel_path)
            elif entry.is_file(follow_symlinks=False):
                # Process the files in this directory
                if not is_ignored(rel_path, False, ignore_rules):
                    stat = entry.stat()
                    # Check if the file is executable
                    if os.access(entry.path, os.X_OK):
                        mode = "100755"
                    else:
                        mode = "100644"
                    workspace[rel_path] = (mode, stat.st_mtime, stat.st_size)

    scan_dir(repo_root, "")
    return workspace


def read_tree(repo_root: Path, tree_sha: str, current_folder: str = "") -> dict:
    result = {}

    raw_bytes = read_object(repo_root, tree_sha)
    current_index = 0
    total_bytes = len(raw_bytes)

    # [MODE] [SPACE] [FILE_NAME] [NULL_BYTE] [20_BYTE_BINARY_SHA]
    while current_index < total_bytes:
        space_index = raw_bytes.find(b" ", current_index)
        null_index = raw_bytes.find(b"\0", space_index)

        mode = raw_bytes[current_index:space_index].decode()

        name = raw_bytes[space_index + 1 : null_index].decode()

        sha_start = null_index + 1
        sha_end = sha_start + 20

        sha_bytes = raw_bytes[sha_start:sha_end]
        sha = sha_bytes.hex()

        current_index = sha_end

        full_path = current_folder + name

        # This mode means it is a sub-directory
        if mode == "40000":
            subfolder_prefix = full_path + "/"
            subfolder_tree = read_tree(repo_root, sha, subfolder_prefix)

            result.update(subfolder_tree)
        # It is a direct file
        else:
            result[full_path] = (mode, sha)

    return result


def cmd_init(args: list[str]) -> None:
    # Generally we do git init which makes a .git in the directory it was ran
    target: Path = Path(".").resolve()

    # If they specify the directory we will do use that Path instead
    if len(args) > 1:
        target = Path(args[1]).resolve()

    # We should avoid making .rag/ in root folder
    if target.parent == target:
        raise RepositoryError("Not a good idea to initialize in root folder")

    # We will make .rag, .rag/objects, .rag/refs/heads also we will warn the user if .rag already exists
    rag_dir = target / ".rag"
    if rag_dir.exists():
        raise RepositoryError("Project already in tracking, if you want to reset then delete .rag and retry")

    (rag_dir / "objects").mkdir(parents=True, exist_ok=True)
    (rag_dir / "refs" / "heads").mkdir(parents=True, exist_ok=True)

    # We will create the HEAD, index file too
    head_file = rag_dir / "HEAD"
    index_file = rag_dir / "index"

    head_file.write_text("ref: refs/heads/main\n")
    index_file.write_text("{}\n")

    print(f"Initialized empty R.A.G. repository in {rag_dir}")


def process_single_file(repo_root: Path, file_path: Path, index: dict, ignore_rules: list[str]) -> None:
    # Don't need /home/user/ only the relative path from .rag folder
    rel_path = file_path.relative_to(repo_root).as_posix()

    # Check if the file is in .gitignore
    if is_ignored(rel_path, False, ignore_rules):
        return

    # Get the current OS file metadata
    stat = file_path.stat()

    mode = "temp"
    if os.access(file_path, os.X_OK):
        mode = "100755"
    else:
        mode = "100644"

    time, size = stat.st_mtime, stat.st_size

    # Skip if the file hasn't changed since last added SHA checking is good but it's O(bytes)
    # If we change permission of a file stat.st_mtime don't track it so mode is required and size for extra-check O(1)
    if rel_path in index:
        curr_mode, _, curr_mtime, curr_size = index[rel_path]
        if mode == curr_mode and size == curr_size and time == curr_mtime:
            return

    # Hash, compress, and save the file object, then update the staging index
    sha = write_object(repo_root, "blob", file_path.read_bytes())
    index[rel_path] = [mode, sha, time, size]


def cmd_add(repo_root: Path, args: list[str]) -> None:
    if len(args) < 2:
        raise RepositoryError("Nothing specified, nothing added")

    index = read_index(repo_root)
    ignore_rules = load_gitignore(repo_root)

    # Starting from 1 since args -> [add, file-1, file-2]
    for target in args[1:]:
        target_path = Path(target).resolve()

        # We need to ensure this file is in the project root where rag is initialized
        if not target_path.is_relative_to(repo_root):
            raise RepositoryError(f"This {target_path} is not in this repo_root")

        # Target path may or may not exist since user can delete a file
        if not target_path.exists():
            # We use relative_to since user may type entire path to file or only file name
            # We also wants to avoid that Window \, Unix / remains same for given file
            rel = target_path.relative_to(repo_root).as_posix()

            # If the file was in our index we no longer need it
            removed_item = index.pop(rel, None)

            # If we add a file which is not in folder and not in index means user error
            if removed_item is None:
                raise RepositoryError(f"'{target}' not found anywhere")

            continue

        files_to_process = []

        # os.walk start at the project root and then returns (dirpath, dirnames, filenames)
        # It traverses all subdirectories until it's empty and in the process give the file-names too
        for root, _, files in os.walk(target_path):
            for f in files:
                full_path = Path(root) / f
                files_to_process.append(full_path)

        for file_path in files_to_process:
            process_single_file(repo_root, file_path, index, ignore_rules)

    write_index(repo_root, index)


def create_directory_tree(repo_root: Path, index: dict, current_folder: str) -> str:
    # We will store them as tuples: (name, mode, sha)
    immediate_items = []
    seen_subfolders = set()

    # Iterate directly over the dictionary avoiding .keys() to save memory overhead
    for file_path in index:
        # If the file is not inside the folder we are currently looking at, skip it.
        if not file_path.startswith(current_folder):
            continue

        # If file is "src/utils/math.py", remaining_path becomes "utils/math.py".
        remaining_path = file_path[len(current_folder) :]

        # If there is a slash in the remaining path, it lives inside a subfolder.
        is_in_subfolder = "/" in remaining_path

        if is_in_subfolder:
            # Get just the name of the subfolder (e.g., "utils")
            subfolder_name = remaining_path.split("/")[0]

            # If we haven't built the tree for this subfolder yet, do it now.
            if subfolder_name not in seen_subfolders:
                seen_subfolders.add(subfolder_name)

                # Recursively call this exact function for the subfolder
                next_folder_path = current_folder + subfolder_name + "/"
                subfolder_sha = create_directory_tree(repo_root, index, next_folder_path)

                # "40000" is the standard system mode for a directory
                if subfolder_sha is not None:
                    immediate_items.append((subfolder_name, "40000", subfolder_sha))

        else:
            # There is no slash, so it is a direct file sitting right in this folder.
            file_name = remaining_path

            # Grab the file's data from the index dictionary
            file_data = index[file_path]
            file_mode = file_data[0]
            file_sha = file_data[1]

            immediate_items.append((file_name, file_mode, file_sha))

    # Sort everything alphabetically by name. Git requires this to ensure the SHA hash is always perfectly consistent.
    immediate_items.sort()

    # bytes object are immutable but byte array is mutable so we use it and then convert it to bytes in end
    raw_bytes = bytearray()

    for name, mode, sha in immediate_items:
        # The format must be exactly: "<mode> <name>\0<20-byte-binary-sha>"
        header_string = mode + " " + name
        header_bytes = header_string.encode()

        null_byte = b"\0"
        sha_bytes = bytes.fromhex(sha)

        raw_bytes.extend(header_bytes)
        raw_bytes.extend(null_byte)
        raw_bytes.extend(sha_bytes)

    return write_object(repo_root, "tree", bytes(raw_bytes))


def cmd_commit(repo_root: Path, args: list[str]) -> None:
    if len(args) != 3 or args[1] not in ("-m", "--message"):
        raise RepositoryError("Commit message required (-m <msg>)")

    commit_message = args[2]

    index = read_index(repo_root)

    # Build the tree object from the staging area
    root_tree_sha = create_directory_tree(repo_root, index, "")

    # Information about the previous commit
    parent_commit_sha = get_head(repo_root)

    # Build the commit object from user-defined variables
    author_name = os.getenv("RAG_AUTHOR_NAME", "Dev")
    timestamp = int(time.time())
    author_info = f"{author_name} <dev@rag.local> {timestamp} +0000"

    commit_lines = []
    commit_lines.append(f"tree {root_tree_sha}")

    # For first commit parent is None
    if parent_commit_sha is not None:
        commit_lines.append(f"parent {parent_commit_sha}")

    commit_lines.append(f"author {author_info}")
    commit_lines.append(f"committer {author_info}")
    commit_lines.append("")  # An empty line separates the headers from the message
    commit_lines.append(commit_message)

    # Save the commit to the Object Database
    commit_content = "\n".join(commit_lines)
    commit_sha = write_object(repo_root, "commit", commit_content.encode())

    ref_file = repo_root / ".rag" / "HEAD"
    ref_file.write_text(f"{commit_sha}\n")

    # Standard output msg for user
    display_name = "HEAD"
    short_sha = commit_sha[:7]
    print(f"[{display_name} {short_sha}] {commit_message}")


def cmd_status(repo_root: Path) -> None:
    head_commit_sha = get_head(repo_root)

    print("On branch 'HEAD'")

    # Load the last committed snapshot (HEAD tree)
    head_tree = {}
    if head_commit_sha is not None:
        commit_bytes = read_object(repo_root, head_commit_sha)
        commit_text = commit_bytes.decode()

        # The commit text always starts with "tree <40-character-sha>"
        first_line = commit_text.split("\n")[0]
        tree_sha = first_line.replace("tree ", "")

        # Load the tree into a dictionary of {filepath: (mode, sha)}
        head_tree = read_tree(repo_root, tree_sha)
    else:
        print("No add or commits done")

    index = read_index(repo_root)
    workspace = get_workspace_files(repo_root)

    staged = []  # Changes ready to commit (Index vs HEAD)
    unstaged = []  # Changes not yet added (Workspace vs Index)
    untracked = []  # Entirely new files (Workspace only)

    # Find Staged Changes (Comparing Staging Area against Last Commit)
    for file_path, index_data in index.items():
        index_mode = index_data[0]
        index_sha = index_data[1]

        if file_path not in head_tree:
            staged.append(f"new file:   {file_path}")
        else:
            head_mode, head_sha = head_tree[file_path]
            # If the hash or permissions changed, it is modified
            if index_sha != head_sha or index_mode != head_mode:
                staged.append(f"modified:   {file_path}")

    # Check for files that are in the last commit, but missing from the staging area
    for file_path in head_tree.keys():
        if file_path not in index:
            staged.append(f"deleted:    {file_path}")

    # Find Unstaged & Untracked Changes (Comparing Hard Drive against Staging Area)
    for file_path, workspace_data in workspace.items():
        # workspace_data contains (mode, mtime, size)
        w_mtime = workspace_data[1]
        w_size = workspace_data[2]

        if file_path not in index:
            # The file is on the hard drive but not in the index
            untracked.append(file_path)
        else:
            index_data = index[file_path]
            i_mtime = index_data[2]
            i_size = index_data[3]

            # If the OS modification time or size is different, it changed
            if w_mtime != i_mtime or w_size != i_size:
                unstaged.append(f"modified:   {file_path}")

    # Check for files that are in the staging area, but were deleted from the hard drive
    for file_path in index.keys():
        if file_path not in workspace:
            unstaged.append(f"deleted:    {file_path}")

    if len(staged) > 0:
        print("\nChanges to be committed:")
        for item in sorted(staged):
            print(f"\t{item}")

    if len(unstaged) > 0:
        print("\nChanges not staged for commit:")
        for item in sorted(unstaged):
            print(f"\t{item}")

    if len(untracked) > 0:
        print("\nUntracked files:")
        for item in sorted(untracked):
            print(f"\t{item}")

    # If all buckets are empty, the repository is clean
    if len(staged) == 0 and len(unstaged) == 0 and len(untracked) == 0:
        print("\nnothing to commit, working tree clean")


def main(argv=None):
    # Making argv None when users use it and notNone when we run benchmark
    args = []
    if argv is not None:
        args = argv
    else:
        args = sys.argv[1:]
    # argv -> [python3, rag.py, init] so we ignore the first one

    if not args:
        print("usage: rag <command> [<args>]")
        return 0

    command = args[0]
    try:
        if command == "init":
            return cmd_init(args) or 0

        repo_root = find_repo_root(".")

        if command == "add":
            cmd_add(repo_root, args)
        elif command == "commit":
            cmd_commit(repo_root, args)
        elif command == "status":
            cmd_status(repo_root)
        else:
            print(f"Unknown command: {command}")
        return 0
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
