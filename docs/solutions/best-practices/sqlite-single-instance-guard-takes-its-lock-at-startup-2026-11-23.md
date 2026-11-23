---
title: A SQLite single-instance guard takes its exclusive lock at startup
date: 2026-11-23
category: best-practices
module: hosted-delivery-viewer
problem_type: best_practice
severity: medium
tags: [sqlite, node-sqlite, locking, single-instance, portable-server]
applies_when: "A process relies on SQLite's exclusive locking mode to refuse a second process on the same database file"
---

# A SQLite single-instance guard takes its exclusive lock at startup

## Problem

The portable hosted delivery server must run as a single instance per data directory, because its write-once blobs
and per-delivery rechecks assume one writer. It uses SQLite's lock as the guard, rather than a lock file, because
the operating system drops a file lock when its process dies, so a crashed server leaves no stale lock behind.

Opening the database with `PRAGMA locking_mode=EXCLUSIVE` is not enough on its own. In that mode SQLite takes no lock
at open. It takes the shared lock on the first read and the exclusive lock on the first write, then keeps it. An
instance that has started but not yet written holds nothing, so a second instance on the same directory starts too,
and the two meet only at their first commits.

## Solution

Startup sets the mode and then takes the write lock at once with an empty exclusive transaction:

```ts
db.exec('PRAGMA locking_mode=EXCLUSIVE');
db.exec('PRAGMA synchronous=FULL');
db.exec('BEGIN EXCLUSIVE');
// ... create tables if absent ...
db.exec('COMMIT');
```

After that `COMMIT` the connection keeps the exclusive lock until the process exits. A second process's
`BEGIN EXCLUSIVE` fails as busy, and the server refuses to start with the named error `hosted.data_dir_in_use`
(`hosted/delivery-viewer/src/node/sqlite-store.ts`).

## Why This Matters

A guard that only bites on the first write lets two writers run until traffic arrives, which is exactly the
condition the guard exists to rule out. It also fails late, as a busy error in the middle of a commit, rather than
early, as a clear refusal at startup.

## Prevention

Test the guard against an idle first instance on an existing data directory: start one server, write nothing, then
start a second and expect the startup refusal. A test that lets the first instance write before the second starts
passes even when the lock is taken lazily
(`hosted/delivery-viewer/test/node/server.test.ts`, "refuses a second instance on the data directory an idle first
one holds"). Read `PRAGMA locking_mode` back from the open connection, so a dropped pragma fails a test.

The guard holds on a local block device. Network filesystems such as NFS, EFS, Azure Files and FUSE mounts may not
honour SQLite's locks, so the hosting docs list them as unsupported rather than relying on the guard there.

## Examples

- The plan review for the portable hosted service caught the lazy lock: a reviewer noted that a second instance
  started after the first had written would hide it. The plan then required the startup `BEGIN EXCLUSIVE` and the
  idle-instance test.

## Related

- `docs/hosted-deliveries.md`, "Choose a host": the volume requirement and the unsupported filesystems.
- SQLite's documentation for `PRAGMA locking_mode`.
