# Bind-mounted Unix socket isolation probe

This Linux-only probe compares operations on disposable Unix-socket fixtures in
the original mount namespace and a newly created private mount namespace. It
uses randomized fixture roots under /run and /vol1/1000; it does not inspect,
open, or connect to an installed application socket.

## Fixture and namespace checks

Run only a separately reviewed, root-owned copy on Linux. The script does not
invoke sudo. Before creating fixtures it requires the current process to be
root, verifies the local kurumi account as UID 1000/GID 1001 with only
supplementary group 1000, checks the trusted Python and script paths including
their parent directories, and requires /run to be tmpfs and /vol1/1000 to be
btrfs.

The script creates root-owned sockets and directories of its own. It tests
unmounted Btrfs directory profiles 0755, 0777, and an exact numeric POSIX
ACL profile. For each profile it records rename, unlink, and replacement
results using a child whose real/effective/saved UID is 1000, GID is 1001,
supplementary groups are exactly [1000], capability sets are zero, and
NoNewPrivs is set. The unmounted 0777 replacement case is the positive control.
Each child inherits a root-opened directory descriptor and performs operations
relative to that descriptor after dropping credentials. Result JSON marks this
as `inherited-directory-fd` access and sets
`production_path_traversal_verified` to false. These observations establish
what the child can do through an already-held descriptor; they do not establish
that UID 1000 can reach the fixture, or any production socket, through ordinary
pathname traversal. The top-level report repeats this limit in
`target_access_scope`.

Before any fixture creation or socket bind, admission verifies the complete
held parent chains `/` to `/run` and `/` to `/vol1/1000`. Every directory must
still match its parent entry and held descriptor, be owned by root:root, have
no group/other write bits, and have no POSIX access or default ACL. An ACL
read error or any ACL xattr blocks admission. These checks establish that the
target user and other non-root subjects cannot replace an ancestor while the
probe runs. A one-time write probe, a random fixture name, or a later inode
comparison cannot close the mkdir-to-first-stat race in a writable parent.

The architecture assessment for this setup confirms `/vol1/1000` is writable
by UID 1000, so it intentionally fails this admission rule and the Btrfs
experiment returns `BLOCKED` before mkdir, bind, chmod, ACL changes, or worker
startup. Do not weaken the directory permissions or ACL to make the probe
start. A future run would need an explicitly reviewed root-owned, non-writable
fixture anchor or a separately prepared isolated Btrfs image; the script does
not select or create either automatically.

Setup uses an incremental ownership ledger. For each randomized root and case
directory, the first parent-relative `stat` after creation is retained as an
independent initial device/inode identity before the directory is opened. The
held descriptor must match that initial identity before the ledger marks it
bound. A missing initial stat or any mismatch permanently marks the object
unbound for cleanup; exception handling does not replace the initial identity
with a later descriptor stat. Cleanup preflight rejects that state before any
chmod, unlink, or rmdir. If creation succeeds but the initial identity cannot
be registered, the entry is treated as unknown and retained. A setup failure
stops before namespace work, shuts down every
registered socket server, and verifies each thread has exited before cleanup.
If a thread is still alive, or any ledger identity or directory entry differs,
the probe retains all fixture objects and reports `BLOCKED`. Cleanup removes
only ledger-matched objects; outer roots remain empty with mode 0700. It never
searches for or cleans residue from an earlier run.

The numeric ACL profile remains exact and requires the case directory owner to
be UID/GID `0:0` both before setting the ACL and during read-back verification;
the expected ownership never comes from the fixture's observed metadata. Its
report records the initial owner check, locked fixture metadata, ACL
clear/set/get stages and errno values, decoded numeric entries, actual
mode/UID/GID, and each difference from the approved profile. A mismatch is
`BLOCKED`; the probe does not change the mode to make the ACL check pass.
The JSON output includes `failure_stage`, known `experiment_roots`, the
incremental `setup_ledger`, and per-item `cleanup` statuses, including after
setup exceptions.

For the mounted cases, a worker must first create a different mount namespace,
apply recursive private propagation, and verify the namespace identity and all
mount propagation records from mountinfo. The parent independently verifies
those facts before it authorizes the worker to bind a self-created /run
socket onto a self-created Btrfs socket fixture. Namespace identity follows
the `/proc/.../ns/mnt` magic link and compares the namespace object's device
and inode. Fixture socket paths used by root are fixed leaves below a held,
identity-checked directory descriptor, reached through `/proc/self/fd/<fd>`;
this keeps an ancestor rename or replacement from redirecting the operation.

While that bind remains active, the original namespace runs the requested
UID-1000 operation against the same Btrfs fixture. It records the Btrfs entry
identities, mount IDs, root service response, and resulting state. The private
namespace then independently runs its UID-1000 operation through the bind view,
checks its root service response, unmounts only its own still-verified mount,
and verifies that the Btrfs underlay matches the original-namespace snapshot.
The report labels each view separately. A bind failure has no fallback.

Root-side fixture clients verify SO_PEERCRED and fixed responses before and
after operations. The positive control also verifies that a root client reached
the target-created replacement socket, received its fixed response, and was
identified as root by that socket. The report keeps original-namespace and
private-namespace results separate; neither view is generalized to the other.

## Cleanup and result states

Waits for child processes, worker events, socket clients, and server threads
are bounded. The namespace worker runs in its own process group so timeout
handling can terminate both it and a target child. If the worker or its child
cannot be confirmed reaped, the probe leaves the fixture roots untouched and
reports BLOCKED.

Cleanup compares each entry with its recorded device, inode, owner, group, and
type before unlinking it. It never recursively removes a tree. Unknown entries,
identity changes, failed unmount checks, or cleanup errors are preserved and
reported as BLOCKED. After successful child cleanup, empty outer fixture roots
are retained with mode 0700 and their paths/identities are included in the
report.

- COMPLETE: both namespace views were observed, the unmounted 0777 positive
  control succeeded, required service checks passed, and known fixture entries
  were cleaned.
- UNSAFE_OBSERVED: a target operation succeeded in a protected case. The
  report retains the operation and the namespace view where it occurred.
- PARTIAL: the observations or positive control did not fully complete.
- BLOCKED: a precondition, identity, namespace/propagation, bind, timeout, or
  cleanup check failed.

These states describe only the disposable fixtures. They do not establish
production socket behavior, application compatibility, or results in any
namespace that was not separately observed.

## Offline tests

Run the userspace-only suite on a development machine:

    python -B -m unittest offline_tests.test_bind_mount_socket_probe -v

The tests mock mount and namespace operations. They cover namespace identity
comparison, initial-stat-to-descriptor identity binding and refusal to clean
inode replacements, fixed root:root ACL validation before and after set/get,
and preflight rejection of a UID-1000-owned Btrfs parent with no fixture or
mount mutation. They also cover the original/private same-fixture protocol
ordering, bind failure, independent results, positive-control failure, timeout
handling, and cleanup refusal. A
Linux/root-only Unix-socket lifecycle test exercises a one-accept
fake service and confirms post-operation validation reuses the already-verified
client record instead of reconnecting after the service exits. Linux-only
checks also exercise a real same-namespace child and procfd directory pinning;
a root-only check tests socket binding after its ancestor path is replaced.
Those checks are skipped on systems without Linux procfs, `SO_PEERCRED`, or
root. Mocked protocol tests do not emulate Linux kernel mount behavior,
bind-mount visibility, ACL enforcement, or the root-run experiment. No mount
operation is run by the offline suite, and skipped checks must not be reported
as passed.
