# Btrfs Unix socket path experiment

This is an isolated Linux permission experiment for the `kotomi` host. It does
not modify the installed extension directory or open its socket; it reads only
the directory's metadata for a filesystem comparison. It creates a random
root-owned test directory under `/vol1/1000`, runs controlled operations as
`kurumi` (UID 1000, GID 1001, supplementary group 1000), and removes only the
objects it created after checking their device, inode, type, and owner.
The script intentionally leaves the empty outer test directory behind with
mode `0700`; its exact path and cleanup status are included in the JSON output.

## Run conditions

Run the reviewed script as root on `kotomi` with Python 3.11 or newer. It opens
`/`, `/vol1`, and `/vol1/1000` one component at a time with directory file
descriptors and `O_NOFOLLOW`, checking each opened descriptor against the
corresponding `lstat` device and inode. It reads `mnt_id` from the held
`/vol1/1000` descriptor's `/proc/self/fdinfo` entry and requires exactly one
matching `/proc/self/mountinfo` record whose filesystem type is `btrfs`. A
missing, repeated, malformed, or non-Btrfs mount record blocks the run before
it creates the experiment directory. It does not require mountinfo's
`major:minor` value to equal `stat.st_dev`: subvolume device numbers can differ.
Immediately before `mkdir`, it reopens the fixed path components and checks
their device/inode identity and the held/current mount IDs again. A missing,
changed, or symbolic-link path blocks before `mkdir`.

The local account/group data must still match the approved numeric identity;
otherwise the script returns `BLOCKED` before creating the experiment
directory. It does not invoke `sudo`, run shell commands, access the network, or
read application configuration.

Before running it, copy the reviewed script to a root-owned location and compare
its SHA-256 with the value recorded by the coordinating reviewer. Execute the
root-owned copy with Python isolated mode (`python3 -I -S`). Do not execute a
copy from `/tmp` or another user-writable directory. The script's only intended
filesystem writes are inside the random experiment directory; production
metadata is read with `lstat` and mount information only.

## Experiments

The probe creates three root-owned parent directories, each containing a
root-owned Unix socket with mode `0600`:

- `parent-0755`, with no inherited access or default ACL;
- `parent-0777`, with no inherited access or default ACL, as the rename-positive
  control;
- `parent-acl`, with the exact numeric ACL observed on the production directory
  (`user::rwx`, `user:955:rwx`, `group::r-x`, `mask::rwx`, `other::r-x`). The
  probe compares every ACL entry, owner, group, and resulting mode before it
  runs that case; a mismatch is reported and the case is skipped.

As `kurumi`, a child process verifies its real, effective, and saved UID/GID,
supplementary groups, and zero effective/permitted/inheritable/ambient
capabilities. Before each operation it checks that it can reach the fixture
through the inherited directory descriptor, and reports that result. It then
attempts to connect to the root socket, rename it, and
replace its path with a mock socket. The mock server reports `SO_PEERCRED` and
returns a fixed string so the root parent can verify which service answered.
A separate case tries to rename a root-owned `0700` child directory inside a
writable parent and create a replacement at the old name.

Every child process and socket operation has a timeout. The probe uses file
descriptors and fixed names for operations. Cleanup only removes known objects
whose recorded device, inode, owner, and type still match. If it finds unknown
entries or a changed directory, it leaves them in place and reports the
experiment path for review; it never recursively deletes a directory. It also
does not remove the outer test directory by name: `/vol1/1000` is writable by
the tested user, so checking then removing that directory entry would leave a
race. After `PRESERVED_EMPTY_PRIVATE_DIRECTORY`, an administrator can verify
the exact reported path, device/inode, owner, mode, and emptiness before any
manual removal.

## Reading the result

`COMPLETE` means the isolated cases ran, the `0777` positive control recorded
the target's completed connection attempt (success or an errno such as `EACCES`),
confirmed fixture reachability by dirfd, renamed and replaced its socket, and
reached the mock service, their known objects were removed, and the empty private outer
directory was retained with mode `0700`. A failed positive control leaves the
overall result `PARTIAL`. Each case
reports the actual connection errno, rename result, mock replacement result,
and cleanup outcome. It does not mean the production socket was vulnerable.
`PARTIAL` means a case or identity check did not complete. `BLOCKED` means a
required platform, account, filesystem, identity, or cleanup condition failed.
The ACL case may say `NOT RUN` if the test filesystem cannot reproduce the
observed ACL exactly.

The report compares the experiment base and production directory using two
separate read-only observations: descriptor mount ID (`same`, `different`, or
`unknown`) and `stat.st_dev` (`same`, `different`, or `unknown`). Production
`st_dev` is compared only after the no-follow opened descriptor matches the
path's `lstat` device and inode; open failure or mismatch leaves the comparison
`unknown`, while the raw `lstat` device is retained as an unverified observation.
Missing production metadata yields `unknown`. Even a matching mount ID does not prove
that the paths are in the same Btrfs subvolume; a matching `st_dev` does not
prove that ACL policy, ancestor permissions, kernel behavior, or container path
resolution are identical. These comparisons do not block the isolated
experiment by themselves. The experiment must not be cited as proof that the
production socket can be replaced or that a production exploit was attempted.
It does not change the WebUI 8774 identity bypass and does not close R2.
