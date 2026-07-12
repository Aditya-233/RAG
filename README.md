# R.A.G. — Repository Architecture & Graph

[![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)](https://www.python.org/)
[![Dependency Free](https://img.shields.io/badge/Dependencies-Zero-brightgreen)](#)
[![Systems Coding](https://img.shields.io/badge/Category-Systems_Programming-orange)](#)
[![License](https://img.shields.io/badge/License-MIT-purple)](#)

A pure Python, zero-dependency implementation of the core mechanics behind Git, built entirely from first principles. **R.A.G.** demonstrates how modern version control systems implement content-addressable storage, staging, immutable snapshots, and commit graphs without relying on external libraries.

---

# ⚙️ Core Architecture

Every tracked file moves through a deterministic pipeline before becoming part of repository history.

```mermaid
graph TD
    WD["📂 Working Directory"] -->|python rag.py add| INDEX["📄 Staging Index (.rag/index)"]
    INDEX -->|python rag.py commit| OBJECTS["📦 Object Database (.rag/objects)"]

    subgraph STORE [Content Addressable Storage]
        BLOB["📄 Blob Objects"]
        TREE["🌳 Tree Objects"]
        COMMIT["💬 Commit Objects"]
    end

    OBJECTS --> STORE
    COMMIT -->|Parent SHA-1| DAG["🔗 Commit DAG"]
    REFS["🚩 HEAD / main"] --> COMMIT
```

---

# 📈 Performance Characteristics

| Operation               | Complexity                           |
| ----------------------- | ------------------------------------ |
| `init`                  | **O(1)**                             |
| `status`                | **O(N)**                             |
| `add` (initial)         | **O(N)**                             |
| `add` (unchanged files) | **Near O(1)**                        |
| `commit`                | **O(1)** relative to repository size |

---

# 🚀 Cold-Disk Benchmark Results

Benchmarks were executed on **Arch Linux** using `benchmark_rag.py` with **OS page cache dropped before every operation**, measuring true filesystem performance rather than warm-cache execution.

| Repository Scale |         `add` |    `commit` |     `status` |
| ---------------: | ------------: | ----------: | -----------: |
|    **100 Files** |  **33.69 ms** | **2.30 ms** |  **3.59 ms** |
|    **500 Files** | **133.13 ms** | **6.05 ms** | **13.41 ms** |
|  **1,000 Files** | **283.41 ms** | **8.39 ms** | **26.53 ms** |

### Peak Memory Usage

| Repository Scale |  `init` |   `add` | `commit` | `status` |
| ---------------: | ------: | ------: | -------: | -------: |
|    **100 Files** | 0.13 MB | 0.48 MB |  0.32 MB |  0.17 MB |
|    **500 Files** | 0.13 MB | 0.82 MB |  0.46 MB |  0.42 MB |
|  **1,000 Files** | 0.13 MB | 1.29 MB |  0.65 MB |  0.79 MB |

---

# 🧩 Architecture Overview

## Content Addressable Storage (CAS)

Every file is hashed using SHA-1 before storage.

Instead of saving files by filename, objects are stored by their content hash.

This enables:

- Automatic deduplication
- Immutable object storage
- Efficient object lookup

---

## Blob Objects

Raw file contents are compressed using Python's built-in `zlib` module before being written into `.rag/objects`.

Files with identical contents share the same blob object.

---

## Tree Objects

Tree objects represent directory structures.

Each commit references a tree, which recursively references blobs and other trees, creating a complete snapshot of the repository.

---

## Commit Objects

Each commit stores:

- Root tree SHA
- Parent commit SHA
- Commit message
- Timestamp
- Author metadata

Commits form a Directed Acyclic Graph (DAG), mirroring Git's internal history representation.

---

## Staging Index

The staging index records the exact contents that will become the next commit.

Each tracked entry stores:

- File path
- SHA-1 hash
- File size
- Last modification time (`mtime`)

This metadata cache enables rapid change detection without repeatedly reading unchanged files.

---

# 📂 Repository Structure

```text
.
├── rag.py
├── benchmark_rag.py
├── README.md
└── .rag
    ├── HEAD
    ├── index
    ├── refs
    └── objects
```

---

# ✨ Features

- Repository initialization
- Content-addressable object storage
- SHA-1 object hashing
- Zlib object compression
- Metadata-aware staging index
- Immutable commits
- Fast repository status detection
- `.gitignore` pattern support
- Zero external dependencies

---

# 💻 Command Line Interface

Initialize a repository:

```bash
python rag.py init
```

Stage files:

```bash
python rag.py add <path>

python rag.py add .
```

Check repository status:

```bash
python rag.py status
```

Create a commit:

```bash
python rag.py commit -m "Commit message"
```

---

# 🧠 Concepts Demonstrated

This project implements many of the foundational ideas behind Git:

- Content-addressable storage
- Immutable object model
- SHA-1 hashing
- Filesystem traversal
- Object serialization
- Zlib compression
- Commit graph construction
- Metadata caching
- Index-based staging
- Snapshot-oriented version control

---

# 📊 Benchmark Methodology

The benchmark suite measures **cold-disk performance**, ensuring results reflect actual storage performance rather than operating system page cache effects.

Each benchmark:

1. Creates a fresh repository.
2. Generates repositories containing **100**, **500**, and **1,000** files (64 KB each).
3. Drops the Linux page cache before every measured operation.
4. Records execution latency and peak memory usage.

This methodology provides reproducible, filesystem-bound performance measurements.

---

# 🎯 Project Goals

R.A.G. is designed as an educational implementation rather than a replacement for Git.

Its objectives are to demonstrate:

- content-addressable storage
- immutable snapshots
- filesystem-efficient version control
- commit graph construction
- metadata-driven caching
- systems programming techniques using only the Python standard library
