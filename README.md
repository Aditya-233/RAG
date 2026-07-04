# R.A.G.

### Repository Architecture & Graph Engine

A minimal Git implementation built entirely with the Python standard library.

R.A.G. recreates the core mechanics of Git from scratch, including content-addressable object storage, a staging index, references, and a commit Directed Acyclic Graph (DAG). The project is designed as an educational implementation that prioritizes clarity over feature completeness.

**Highlights**

- Zero third-party dependencies
- Pure Python (3.10+)
- Content-addressable object database
- Immutable blob, tree, and commit objects
- SHA-1 object hashing
- Zlib-compressed object storage
- Staging index
- Branches and references
- Commit history stored as a DAG

---

## Architecture

Every file, directory, and commit is stored as an immutable object identified by the SHA-1 hash of its contents.

```
Working Directory
        │
        ▼
     add/index
        │
        ▼
   Index (.rag/index)
        │
     commit
        ▼
Object Store (.rag/objects)
        │
        ▼
 Commit Graph (DAG)
        ▲
        │
    checkout
```

R.A.G. stores exactly three object types.

| Object     | Description                                                           |
| ---------- | --------------------------------------------------------------------- |
| **Blob**   | Raw file contents                                                     |
| **Tree**   | Directory snapshot containing `(mode, name, sha)` entries             |
| **Commit** | Tree reference, parent reference, author metadata, and commit message |

Because every object is addressed by the hash of its contents:

- identical files are stored only once
- objects are immutable
- commit history is naturally tamper-evident

---

## Repository Layout

```
.rag/
├── HEAD
├── index
├── refs/
│   └── heads/
│       └── main
└── objects/
    └── xx/
        └── xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

- **HEAD** points to the current branch or detached commit.
- **index** stores the staging area.
- **refs** stores branch tips.
- **objects** stores compressed blob, tree, and commit objects.

---

## Installation

No installation is required.

```bash
git clone https://github.com/Aditya-233/RAG.git
cd RAG

python main.py init
```

**Requirements**

- Python 3.10+
- No external dependencies

---

## Commands

```bash
python main.py <command> [options]
```

| Command    | Description                                |
| ---------- | ------------------------------------------ |
| `init`     | Initialize a repository                    |
| `status`   | Show staged, unstaged, and untracked files |
| `add`      | Stage files                                |
| `commit`   | Create a commit                            |
| `diff`     | Show workspace vs. index differences       |
| `log`      | Display commit history                     |
| `checkout` | Restore a branch or commit                 |

---

## Example

```bash
python main.py init

echo "hello world" > hello.txt

python main.py add .

python main.py commit -m "Initial commit"

python main.py log

python main.py checkout <commit-sha>
```

---

## Environment Variables

Commit author information can be configured through environment variables.

| Variable           | Default           |
| ------------------ | ----------------- |
| `RAG_AUTHOR_NAME`  | `Developer`       |
| `RAG_AUTHOR_EMAIL` | `dev@example.com` |

Example:

```bash
RAG_AUTHOR_NAME="Aditya" \
RAG_AUTHOR_EMAIL="me@example.com" \
python main.py commit -m "Initial commit"
```

---

## Project Structure

```
RAG/
├── main.py
├── src/
│   ├── __init__.py
│   └── engine.py
├── LICENSE
└── README.md
```

The complete version control engine is implemented in a single file:

```
src/engine.py
```

---

## Design Notes

### Commit Graph

Commits form a Directed Acyclic Graph (DAG). Each commit references:

- one tree
- zero or one parent commit
- author metadata
- timestamp
- commit message

---

## Current Features

- Content-addressable object database
- Blob, tree, and commit objects
- SHA-1 hashing
- Zlib compression
- Staging index
- Commit history
- Branch references
- Checkout
- Unified diff
- Basic `.gitignore` support

---

## Limitations

R.A.G. intentionally focuses on Git's core storage model.

The following features are not currently implemented:

- merge commits
- rebasing
- remotes
- packfiles
- garbage collection
- tags
- hooks
- submodules
- Git's full ignore pattern syntax (`**`, negation, etc.)

---

## Purpose

R.A.G. is intended as an educational implementation of Git's internal architecture. Rather than reproducing every Git feature, it demonstrates how a modern version control system can be built using immutable objects, references, and a commit DAG with fewer than a thousand lines of dependency-free Python.
