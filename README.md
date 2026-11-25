# diskdedupe

`diskdedupe` is a command-line tool that finds duplicate files by content and moves the
redundant copies into a quarantine folder instead of deleting them. Every quarantine
operation writes a manifest, so the move can be undone with one command.

It has no third-party dependencies and runs on Windows, macOS and Linux with Python 3.10
or newer.

## How detection works

The scan always compares file content, never just names or sizes:

1. Files are grouped by exact size. A file with a unique size cannot be a duplicate.
2. Files in size groups larger than one are grouped again by the BLAKE2b digest of their
   first 64 KiB (small files are hashed completely).
3. Only files that still collide get a full-content BLAKE2b-256 digest.

Files that are hard links to the same inode are counted once, because they do not occupy
extra disk space. Symbolic links are never followed. Unreadable files and folders are
reported as warnings on stderr and skipped.

## Installation

```sh
git clone https://github.com/l1obyanc/diskdedupe.git
cd diskdedupe
python -m pip install .
```

After that the `diskdedupe` command is available. To run it from a checkout without
installing, use `python -m pip install --editable .` or set `PYTHONPATH=src` and call
`python -m diskdedupe`.

## Usage

### Report duplicates

```sh
diskdedupe scan ~/Pictures ~/Downloads
```

Example output:

```text
Scanned 5 files under: /home/user/demo
Found 1 duplicate group: 2 redundant files, 645 KB reclaimable.

[1] 3 copies, 322 KB each, 645 KB wasted
    /home/user/demo/backup/beach copy.jpg
    /home/user/demo/backup/beach.jpg
    /home/user/demo/photos/2019/beach.jpg
```

Useful options:

```sh
diskdedupe scan . --min-size 1MB --exclude "*.tmp" --exclude "node_modules/*"
diskdedupe scan . --json
```

`--min-size` accepts plain bytes or suffixes such as `64k`, `10MB` or `2G`. Empty files
are skipped by default; pass `--min-size 0` to include them. `--exclude` patterns are
matched against the path relative to each scanned root and against the file name.

### Quarantine redundant copies

`quarantine` prints the plan without touching anything. Review it, then add `--apply`.

```sh
diskdedupe quarantine ~/Pictures ~/Backup --into ~/dupes-quarantine
diskdedupe quarantine ~/Pictures ~/Backup --into ~/dupes-quarantine --apply
```

By default the copy with the shortest path is kept (ties broken alphabetically). Choose a
different rule with `--keep newest`, `--keep oldest`, `--keep first` or `--keep last`.

Quarantined files keep their relative location:

```text
~/dupes-quarantine/
    manifest.json
    files/
        Pictures/2019/beach.jpg
```

Name collisions inside the quarantine folder are resolved with a ` (1)`, ` (2)`, ...
suffix. The destination folder must not exist yet or must be empty.

### Restore a quarantine

```sh
diskdedupe restore ~/dupes-quarantine/manifest.json
diskdedupe restore ~/dupes-quarantine/manifest.json --apply
```

Files are moved back to the absolute paths recorded in the manifest. If a file already
exists at that path, it is skipped unless `--overwrite` is given. A dry run happens when
`--apply` is omitted.

## Safety model

- No data is ever deleted by `diskdedupe`. Quarantine is a move into a folder you choose.
- `quarantine` and `restore` are dry runs unless `--apply` is passed.
- The quarantine destination is created only when the plan is applied.
- `restore` refuses to replace existing files unless `--overwrite` is passed.
- If a file cannot be moved during `--apply`, the remaining moves still run, the manifest
  records the moves that succeeded, and the command exits with an error.
- Hard links are not reported as duplicates, so reported waste reflects real disk usage.
- Keep the quarantine folder outside the folders you usually scan. If it lives inside a
  scanned folder, the next scan will see the quarantined copies again.

## Exit codes

| Command      | 0                        | 1                                 | 2           |
| ------------ | ------------------------ | --------------------------------- | ----------- |
| `scan`       | no duplicates found      | duplicates found                  | error       |
| `quarantine` | success (plan or applied)| -                                 | error       |
| `restore`    | everything restored      | skipped conflicts or missing files| error       |

Errors include invalid arguments, unreadable manifests and missing paths. The `2` code
keeps `scan` usable in scripts and CI jobs.

## Development

Run the test suite (standard library only):

```sh
python -m unittest discover -s tests -t .
```

Layout:

```text
src/diskdedupe/
    scanner.py      filesystem walk and duplicate detection
    actions.py      quarantine planning, apply, restore, manifest handling
    formatting.py   size parsing, human-readable output
    cli.py          argument parsing and command handlers
    models.py       shared data structures
tests/              scanner, action, formatting and CLI tests
```

## License

MIT. See [LICENSE](LICENSE).
