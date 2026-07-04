"""R.A.G. - Repository Architecture & Graph Engine."""

import difflib
import fnmatch
import hashlib
import json
import os
import sys
import time
import zlib
from pathlib import Path
from typing import Optional


class RepositoryError(Exception):
    pass


def read_object(repo_root: Path, sha: str) -> tuple[str, bytes]:
    """Read and decompress a loose object, returning (type, data)."""
    if len(sha) != 40:
        raise RepositoryError(f"Object not found/invalid: {sha}")

    object_path: Path = repo_root / ".rag" / "objects" / sha[:2] / sha[2:]
    if not object_path.is_file():
        raise RepositoryError(f"Object not found/invalid: {sha}")

    compressed_data: bytes = object_path.read_bytes()
    raw_data: bytes = zlib.decompress(compressed_data)

    null_byte_index: int = raw_data.find(b"\0")
    if null_byte_index == -1:
        raise ValueError("Invalid object header: null byte missing")

    header: str = raw_data[:null_byte_index].decode()
    object_type: str = header.split(" ")[0]
    object_data: bytes = raw_data[null_byte_index + 1 :]

    return object_type, object_data


def write_object(repo_root: Path, object_type: str, raw_content: bytes) -> str:
    """Compress and write a loose object, returning its SHA-1 hex digest."""
    header: bytes = f"{object_type} {len(raw_content)}".encode()
    full_data: bytes = header + b"\0" + raw_content

    sha: str = hashlib.sha1(full_data).hexdigest()
    compressed: bytes = zlib.compress(full_data)

    object_path: Path = repo_root / ".rag" / "objects" / sha[:2] / sha[2:]
    if not object_path.is_file():
        object_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path = object_path.parent / f"tmp_{sha[2:]}"
        tmp_path.write_bytes(compressed)
        tmp_path.replace(object_path)

    return sha


def find_repo_root(start_dir: str | Path = ".") -> Path:
    """Walk up the directory tree to find the .rag directory."""
    current_dir: Path = Path(start_dir).resolve()

    while current_dir.parent != current_dir:
        rag_dir: Path = current_dir / ".rag"
        if rag_dir.is_dir():
            return current_dir
        current_dir = current_dir.parent

    # Check the filesystem root itself
    if (current_dir / ".rag").is_dir():
        return current_dir

    raise RepositoryError("Not a R.A.G. repository: .rag missing")


def read_index(repo_root: Path) -> dict[str, tuple[str, str]]:
    """Read the JSON index file, returning {rel_path: (mode, sha)}."""
    index_path: Path = repo_root / ".rag" / "index"
    if not index_path.is_file():
        return {}

    try:
        raw_json: dict = json.loads(index_path.read_text(encoding="utf-8"))
        result: dict[str, tuple[str, str]] = {}
        for file_path, entry in raw_json.items():
            if len(entry) == 2:
                result[file_path] = (entry[0], entry[1])
        return result
    except Exception:
        return {}


def write_index(repo_root: Path, entries: dict[str, tuple[str, str]]) -> None:
    """Serialize and write the index to disk."""
    index_path: Path = repo_root / ".rag" / "index"
    index_path.parent.mkdir(parents=True, exist_ok=True)

    serializable: dict[str, list[str]] = {
        file_path: list(mode_sha) for file_path, mode_sha in sorted(entries.items())
    }
    json_text: str = json.dumps(serializable, indent=2)
    index_path.write_text(json_text, encoding="utf-8")


def get_head(repo_root: Path) -> tuple[Optional[str], Optional[str]]:
    """Read HEAD, returning (ref_name_or_None, commit_sha_or_None)."""
    head_path: Path = repo_root / ".rag" / "HEAD"
    if not head_path.is_file():
        raise RepositoryError("HEAD file missing")

    head_content: str = head_path.read_text(encoding="utf-8").strip()

    if head_content.startswith("ref:"):
        ref_name: str = head_content[4:].strip()
        ref_path: Path = repo_root / ".rag" / ref_name
        commit_sha: Optional[str] = None
        if ref_path.is_file():
            commit_sha = ref_path.read_text(encoding="utf-8").strip()
        return ref_name, commit_sha

    return None, head_content


def is_ignored(repo_root: Path, relative_path: str, is_directory: bool = False) -> bool:
    """Return True if the path matches any .gitignore rule or is inside .rag/.git."""
    path_parts: list[str] = relative_path.split("/")

    # Always ignore version control directories
    if ".rag" in path_parts or ".git" in path_parts:
        return True

    gitignore_path: Path = repo_root / ".gitignore"
    if not gitignore_path.is_file():
        return False

    file_name: str = path_parts[-1]

    try:
        gitignore_text: str = gitignore_path.read_text(
            encoding="utf-8", errors="replace"
        )
        for raw_line in gitignore_text.splitlines():
            stripped: str = raw_line.strip()

            if not stripped or stripped.startswith("#"):
                continue

            is_dir_only: bool = stripped.endswith("/")
            is_rooted: bool = stripped.startswith("/")

            pattern: str = stripped
            if is_dir_only:
                pattern = pattern[:-1]
            if is_rooted:
                pattern = pattern[1:]

            if is_dir_only and not is_directory:
                continue

            if fnmatch.fnmatch(relative_path, pattern) or fnmatch.fnmatch(
                file_name, pattern
            ):
                return True

        return False
    except Exception:
        return False


def read_tree_recursive(
    repo_root: Path, tree_sha: str, path_prefix: str = ""
) -> dict[str, tuple[str, str]]:
    """Recursively expand a tree object into {rel_path: (mode, blob_sha)}."""
    result: dict[str, tuple[str, str]] = {}

    object_type, tree_data = read_object(repo_root, tree_sha)
    if object_type != "tree":
        raise RepositoryError(f"Object {tree_sha} is {object_type}, expected tree")

    byte_index: int = 0
    data_length: int = len(tree_data)

    while byte_index < data_length:
        space_pos: int = tree_data.find(b" ", byte_index)
        null_pos: int = tree_data.find(b"\0", space_pos)

        if space_pos == -1 or null_pos == -1:
            break

        entry_mode: str = tree_data[byte_index:space_pos].decode()
        entry_name: str = tree_data[space_pos + 1 : null_pos].decode()
        entry_sha: str = tree_data[null_pos + 1 : null_pos + 21].hex()

        byte_index = null_pos + 21
        full_path: str = f"{path_prefix}{entry_name}"

        if entry_mode == "40000":
            # Directory — recurse
            subtree: dict[str, tuple[str, str]] = read_tree_recursive(
                repo_root, entry_sha, f"{full_path}/"
            )
            result.update(subtree)
        else:
            result[full_path] = (entry_mode, entry_sha)

    return result


def build_tree_from_index(repo_root: Path) -> str:
    """Build a tree object hierarchy from the current index, return root tree SHA."""
    index_entries: dict[str, tuple[str, str]] = read_index(repo_root)

    def build_subtree(prefix: str) -> str:
        """Recursively build a tree object for the given path prefix."""
        direct_files: dict[str, tuple[str, str]] = {}
        subdirectory_names: set[str] = set()

        for file_path, (mode, blob_sha) in index_entries.items():
            if not file_path.startswith(prefix):
                continue

            relative: str = file_path[len(prefix) :]

            if "/" in relative:
                subdir_name: str = relative.split("/", 1)[0]
                subdirectory_names.add(subdir_name)
            else:
                direct_files[relative] = (mode, blob_sha)

        tree_entries: list[tuple[str, str, str]] = []

        for subdir in sorted(subdirectory_names):
            subdir_sha: str = build_subtree(f"{prefix}{subdir}/")
            if subdir_sha:
                tree_entries.append(("40000", subdir, subdir_sha))

        for file_name, (mode, blob_sha) in sorted(direct_files.items()):
            tree_entries.append((mode, file_name, blob_sha))

        tree_entries.sort(key=lambda entry: entry[1])

        raw_parts: list[bytes] = []
        for entry_mode, entry_name, entry_sha in tree_entries:
            header_bytes: bytes = f"{entry_mode} {entry_name}".encode()
            sha_bytes: bytes = bytes.fromhex(entry_sha)
            raw_parts.append(header_bytes + b"\0" + sha_bytes)

        return write_object(repo_root, "tree", b"".join(raw_parts))

    return build_subtree("")


def get_workspace_files(repo_root: Path) -> dict[str, tuple[str, str, bytes]]:
    """Return all non-ignored files in the working tree as {rel_path: (mode, sha, bytes)}."""
    result: dict[str, tuple[str, str, bytes]] = {}

    for dir_root, subdirs, file_names in os.walk(repo_root):
        try:
            dir_relative: str = Path(dir_root).relative_to(repo_root).as_posix()
        except ValueError:
            continue

        filtered_subdirs: list[str] = []
        for subdir in subdirs:
            subdir_rel: str = f"{dir_relative}/{subdir}".strip("/")
            if not is_ignored(repo_root, subdir_rel, True):
                filtered_subdirs.append(subdir)
        subdirs[:] = filtered_subdirs

        for file_name in file_names:
            file_path: Path = Path(dir_root) / file_name
            rel_path: str = file_path.relative_to(repo_root).as_posix()

            if is_ignored(repo_root, rel_path):
                continue

            file_bytes: bytes = file_path.read_bytes()
            file_mode: str = "100755" if os.access(file_path, os.X_OK) else "100644"

            blob_header: bytes = f"blob {len(file_bytes)}".encode()
            blob_raw: bytes = blob_header + b"\0" + file_bytes
            blob_sha: str = hashlib.sha1(blob_raw).hexdigest()

            result[rel_path] = (file_mode, blob_sha, file_bytes)

    return result


def get_status(repo_root: Path) -> dict[str, list[str]]:
    """Return staged, unstaged, and untracked change lists."""
    _ref_name, head_sha = get_head(repo_root)

    head_tree: dict[str, tuple[str, str]] = {}
    if head_sha is not None:
        commit_type, commit_data = read_object(repo_root, head_sha)
        if commit_type == "commit":
            tree_sha: str = commit_data[5:45].decode()
            head_tree = read_tree_recursive(repo_root, tree_sha)

    index: dict[str, tuple[str, str]] = read_index(repo_root)
    workspace: dict[str, tuple[str, str, bytes]] = get_workspace_files(repo_root)

    staged: list[str] = []
    for file_path, (mode, sha) in sorted(index.items()):
        head_entry: Optional[tuple[str, str]] = head_tree.get(file_path)
        if head_entry != (mode, sha):
            prefix: str = "new file:" if file_path not in head_tree else "modified:"
            staged.append(f"{prefix}   {file_path}")

    for file_path in sorted(head_tree):
        if file_path not in index:
            staged.append(f"deleted:    {file_path}")

    unstaged: list[str] = []
    for file_path, (mode, sha, _content) in sorted(workspace.items()):
        if file_path in index and index[file_path] != (mode, sha):
            unstaged.append(f"modified:   {file_path}")

    for file_path in sorted(index):
        if file_path not in workspace:
            unstaged.append(f"deleted:    {file_path}")

    untracked: list[str] = [
        file_path
        for file_path in sorted(workspace)
        if file_path not in index and file_path not in head_tree
    ]

    return {"staged": staged, "unstaged": unstaged, "untracked": untracked}


def get_diff(repo_root: Path) -> str:
    """Generate a unified diff of workspace vs index/HEAD."""
    _ref_name, head_sha = get_head(repo_root)

    head_tree: dict[str, tuple[str, str]] = {}
    if head_sha is not None:
        commit_type, commit_data = read_object(repo_root, head_sha)
        if commit_type == "commit":
            tree_sha: str = commit_data[5:45].decode()
            head_tree = read_tree_recursive(repo_root, tree_sha)

    index: dict[str, tuple[str, str]] = read_index(repo_root)
    workspace: dict[str, tuple[str, str, bytes]] = get_workspace_files(repo_root)

    all_paths: set[str] = set(workspace) | set(index) | set(head_tree)
    diff_chunks: list[str] = []

    for file_path in sorted(all_paths):
        baseline_bytes: bytes = b""
        if file_path in index:
            _mode, blob_sha = index[file_path]
            _btype, baseline_bytes = read_object(repo_root, blob_sha)
        elif file_path in head_tree:
            _mode, blob_sha = head_tree[file_path]
            _btype, baseline_bytes = read_object(repo_root, blob_sha)

        workspace_bytes: bytes = (
            workspace[file_path][2] if file_path in workspace else b""
        )

        if baseline_bytes == workspace_bytes:
            continue

        baseline_lines: list[str] = baseline_bytes.decode(errors="replace").splitlines(
            keepends=True
        )
        workspace_lines: list[str] = workspace_bytes.decode(
            errors="replace"
        ).splitlines(keepends=True)

        diff_lines: list[str] = list(
            difflib.unified_diff(
                baseline_lines,
                workspace_lines,
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
            )
        )
        diff_chunks.extend(diff_lines)

    return "".join(diff_chunks)


def stage_paths(repo_root: Path, target_paths: list[str]) -> None:
    """Add or remove paths from the index."""
    entries: dict[str, tuple[str, str]] = read_index(repo_root)

    for target in target_paths:
        target_path: Path = Path(target).resolve()

        if not target_path.exists():
            # Remove from index if it was tracked
            if target_path.is_relative_to(repo_root):
                rel: str = target_path.relative_to(repo_root).as_posix()
                if rel in entries:
                    del entries[rel]
            continue

        if target_path.is_file():
            file_list: list[Path] = [target_path]
        else:
            file_list = [
                Path(dir_root) / file_name
                for dir_root, _subdirs, file_names in os.walk(target_path)
                for file_name in file_names
            ]

        for file_path in file_list:
            if not file_path.is_relative_to(repo_root):
                continue

            rel_path: str = file_path.relative_to(repo_root).as_posix()
            if not rel_path:
                continue

            if is_ignored(repo_root, rel_path):
                continue

            file_mode: str = "100755" if os.access(file_path, os.X_OK) else "100644"
            blob_sha: str = write_object(repo_root, "blob", file_path.read_bytes())
            entries[rel_path] = (file_mode, blob_sha)

    write_index(repo_root, entries)


def checkout_target(repo_root: Path, target: str) -> None:
    """Switch the working tree to a branch name or commit SHA."""
    rag_dir: Path = repo_root / ".rag"
    ref_path: Path = rag_dir / "refs" / "heads" / target

    target_sha: str = target
    if ref_path.is_file():
        target_sha = ref_path.read_text(encoding="utf-8").strip()

    try:
        commit_type, commit_data = read_object(repo_root, target_sha)
    except RepositoryError:
        raise RepositoryError(f"pathspec '{target}' did not match any known target")

    if commit_type != "commit":
        raise RepositoryError(f"Target {target_sha} is {commit_type}, expected commit")

    target_tree_sha: str = commit_data[5:45].decode()
    target_files: dict[str, tuple[str, str]] = read_tree_recursive(
        repo_root, target_tree_sha
    )

    current_files: dict[str, tuple[str, str, bytes]] = get_workspace_files(repo_root)
    for rel_path in list(current_files):
        file_path: Path = repo_root / rel_path
        if file_path.is_file():
            os.remove(file_path)

    for dir_root, subdirs, _files in os.walk(repo_root, topdown=False):
        for subdir_name in subdirs:
            dir_path: Path = Path(dir_root, subdir_name)
            dir_rel: str = dir_path.relative_to(repo_root).as_posix()
            if not is_ignored(repo_root, dir_rel, True):
                try:
                    dir_path.rmdir()
                except OSError:
                    pass  # Directory not empty — leave it

    for rel_path, (file_mode, blob_sha) in target_files.items():
        dest_path: Path = repo_root / rel_path
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        _btype, blob_data = read_object(repo_root, blob_sha)
        dest_path.write_bytes(blob_data)
        dest_path.chmod(0o755 if file_mode == "100755" else 0o644)

    write_index(repo_root, target_files)

    # Update HEAD
    if target.startswith("refs/"):
        ref_name: str = target
        is_symbolic_ref: bool = True
    else:
        ref_name = f"refs/heads/{target}"
        is_symbolic_ref = (rag_dir / ref_name).is_file()

    if is_symbolic_ref:
        head_value: str = f"ref: {ref_name}\n"
    else:
        head_value = f"{target}\n"

    (rag_dir / "HEAD").write_text(head_value, encoding="utf-8")


def _print_status(repo_root: Path) -> None:
    """Print the working tree status (helper for main)."""
    ref_name, _sha = get_head(repo_root)

    if ref_name is not None:
        branch_display: str = ref_name.replace("refs/heads/", "")
        print(f"On branch {branch_display}")
    else:
        print("On branch HEAD (detached)")

    status: dict[str, list[str]] = get_status(repo_root)

    sections: list[tuple[str, str]] = [
        ("staged", "Changes to be committed:"),
        ("unstaged", "Changes not staged for commit:"),
        ("untracked", "Untracked files:"),
    ]

    for key, heading in sections:
        items: list[str] = status[key]
        if items:
            print(f"\n{heading}")
            for item in items:
                print(f"\t{item}")

    if not any(status.values()):
        print("\nnothing to commit, working tree clean")


def _print_log(repo_root: Path, start_sha: Optional[str]) -> None:
    """Walk and print the commit history starting from start_sha."""
    if start_sha is None:
        raise RepositoryError("current branch has no commits yet")

    current_sha: Optional[str] = start_sha
    visited_shas: set[str] = set()

    while current_sha is not None and current_sha not in visited_shas:
        visited_shas.add(current_sha)

        commit_type, commit_data = read_object(repo_root, current_sha)
        if commit_type != "commit":
            raise RepositoryError(f"Object {current_sha} is not a commit")

        decoded: str = commit_data.decode(errors="replace")
        parts: list[str] = decoded.split("\n\n", 1)
        header_block: str = parts[0]
        commit_message: str = parts[1].rstrip("\n") if len(parts) > 1 else ""

        parent_shas: list[str] = [
            line[7:].strip()
            for line in header_block.splitlines()
            if line.startswith("parent ")
        ]
        author_lines: list[str] = [
            line[7:].strip()
            for line in header_block.splitlines()
            if line.startswith("author ")
        ]

        author_str: str = author_lines[0] if author_lines else ""
        indented_msg: str = "\n".join(
            f"    {line}" for line in commit_message.splitlines()
        )

        print(f"commit {current_sha}")
        print(f"Author: {author_str}")
        print(f"\n{indented_msg}\n")

        current_sha = parent_shas[0] if parent_shas else None


def main(argv: Optional[list[str]] = None) -> int:
    """Parse command-line arguments and dispatch to the appropriate operation."""
    args: list[str] = argv if argv is not None else sys.argv[1:]

    if not args:
        print("usage: rag <command> [<args>]")
        return 0

    command: str = args[0]

    try:
        if command == "init":
            init_path: Path = Path(args[1] if len(args) > 1 else ".").resolve()
            rag_dir: Path = init_path / ".rag"

            for subdirectory in ["objects", "refs/heads"]:
                (rag_dir / subdirectory).mkdir(parents=True, exist_ok=True)

            head_file: Path = rag_dir / "HEAD"
            index_file: Path = rag_dir / "index"

            if not head_file.exists():
                head_file.write_text("ref: refs/heads/main\n", encoding="utf-8")
            if not index_file.exists():
                index_file.write_text("{}\n", encoding="utf-8")

            print(f"Initialized empty R.A.G. repository in {rag_dir}")
            return 0

        # All other commands require an existing repository
        repo_root: Path = find_repo_root(".")

        if command == "status":
            _print_status(repo_root)

        elif command == "add":
            if len(args) < 2:
                raise RepositoryError("nothing specified, nothing added")
            stage_paths(repo_root, args[1:])

        elif command == "commit":
            has_short_flag: bool = "-m" in args
            has_long_flag: bool = "--message" in args
            if not has_short_flag and not has_long_flag:
                raise RepositoryError("commit message required (-m <message>)")

            flag: str = "-m" if has_short_flag else "--message"
            commit_message: str = args[args.index(flag) + 1]

            tree_sha: str = build_tree_from_index(repo_root)
            ref_name, parent_sha = get_head(repo_root)

            author_name: str = os.getenv("RAG_AUTHOR_NAME", "Developer")
            author_email: str = os.getenv("RAG_AUTHOR_EMAIL", "dev@example.com")
            timestamp: int = int(time.time())
            author_str: str = f"{author_name} <{author_email}> {timestamp} +0000"

            header_lines: list[str] = [f"tree {tree_sha}"]
            if parent_sha is not None:
                header_lines.append(f"parent {parent_sha}")
            header_lines.append(f"author {author_str}")
            header_lines.append(f"committer {author_str}")

            trailing_newline: str = "" if commit_message.endswith("\n") else "\n"
            commit_body: str = (
                "\n".join(header_lines) + f"\n\n{commit_message}" + trailing_newline
            )
            commit_bytes: bytes = commit_body.encode()
            commit_sha: str = write_object(repo_root, "commit", commit_bytes)

            ref_file: Path = repo_root / ".rag" / (ref_name if ref_name else "HEAD")
            ref_file.parent.mkdir(parents=True, exist_ok=True)
            ref_file.write_text(f"{commit_sha}\n", encoding="utf-8")

            branch_name: str = (
                ref_name.replace("refs/heads/", "") if ref_name else "detached HEAD"
            )
            root_tag: str = " (root-commit)" if ref_name and parent_sha is None else ""
            print(f"[{branch_name}{root_tag} {commit_sha[:7]}] {commit_message}")

        elif command == "diff":
            diff_output: str = get_diff(repo_root)
            if diff_output:
                print(diff_output, end="")

        elif command == "log":
            if len(args) > 1:
                log_start_sha: Optional[str] = args[1]
            else:
                _ref, log_start_sha = get_head(repo_root)
            _print_log(repo_root, log_start_sha)

        elif command == "checkout":
            if len(args) < 2:
                raise RepositoryError("target commit/branch required")
            checkout_target(repo_root, args[1])
            print(f"Switched to branch/commit '{args[1]}'")

        else:
            print(f"Unknown command: {command}")

        return 0

    except Exception as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
