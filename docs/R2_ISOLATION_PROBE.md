# R2 isolation probe

This probe gathers bounded, read-only diagnostics and runs one temporary Unix
socket DAC experiment. It does **not** fix the WebUI identity boundary and a
successful `/tmp` experiment does not show that the production socket or its
filesystem enforces the same permissions.

## Before running

Review `scripts/r2_isolation_probe.py` and compare its SHA-256 with this pinned
value before copying it to the NAS:

`817013f581d89104aa643b18e9d6c27b5b9e0dbd2c2a8ed8c50a08ea6bfa59a5`

Because `/tmp` is shared and writable, the following is one shell block to
paste into a root shell on `kotomi`. It copies into a newly created private
directory, refuses to overwrite an existing script, verifies the copied
script against the pinned hash, and runs it only after the hash matches. A
mismatch exits before Python starts. Its exit trap removes only the script and
directory it created, after checking the exact `/root/fnmusic-r2-probe.*`
path, root ownership, directory mode, and file type.

```sh
(
set -eu
umask 077
source_file=/tmp/r2_isolation_probe.py
expected_hash=817013f581d89104aa643b18e9d6c27b5b9e0dbd2c2a8ed8c50a08ea6bfa59a5
probe_dir=$(mktemp -d /root/fnmusic-r2-probe.XXXXXX) || exit 1
probe_file=$probe_dir/r2_isolation_probe.py
cleanup() {
    case "$probe_dir" in
        /root/fnmusic-r2-probe.[A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9]) ;;
        *) return 0 ;;
    esac
    [ ! -L "$probe_dir" ] && [ -d "$probe_dir" ] || return 0
    [ "$(stat -c '%u:%a:%F' "$probe_dir" 2>/dev/null)" = "0:700:directory" ] || return 0
    if [ -f "$probe_file" ] && [ ! -L "$probe_file" ] \
       && [ "$(stat -c '%u' "$probe_file" 2>/dev/null)" = "0" ]; then
        rm -f -- "$probe_file"
    fi
    rmdir -- "$probe_dir" 2>/dev/null || true
}
trap cleanup 0
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
case "$probe_dir" in
    /root/fnmusic-r2-probe.[A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9][A-Za-z0-9]) ;;
    *) exit 1 ;;
esac
[ ! -L "$probe_dir" ] && [ -d "$probe_dir" ]
[ "$(stat -c '%u:%a:%F' "$probe_dir")" = "0:700:directory" ]
[ ! -e "$probe_file" ] && [ ! -L "$probe_file" ]
install -o root -g root -m 0700 "$source_file" "$probe_file"
actual_line=$(sha256sum "$probe_file")
actual_hash=${actual_line%% *}
[ "$actual_hash" = "$expected_hash" ] || { printf '%s\n' 'SHA-256 mismatch; probe not run' >&2; exit 1; }
python3 -I -S "$probe_file" --target-user kurumi
)
```

The surrounding subshell confines `set -eu` and the cleanup traps to this block.
Both the probe and its privilege-dropped child use `-I -S` to prevent imports
from shared script directories and user site packages. The verification happens
against the installed root-owned copy immediately before execution. The probe
output is JSON; preserve it for review, but do not add unrelated command output
or credentials.

The script never invokes `sudo`. It requires Linux, effective UID 0, and an
existing local `/etc/passwd` non-root target account (account lookup does not
use network NSS). If a prerequisite is missing it prints
`BLOCKED` and exits without starting the experiment. Output is JSON. Preserve
the output for review.

## What it checks

- Creates a randomly named, root-owned directory under `/tmp`, mode `0711`,
  and a local mock Unix socket, mode `0600`. A child process drops supplementary
  groups, GID, UID, and verifies its identity and zero effective, permitted,
  inheritable, and ambient capabilities before attempting a connection. The
  probe distinguishes an expected `EACCES` from a permitted connection. A
  same-UID client then connects to the mock server, whose `SO_PEERCRED` value
  must match the expected UID.
- Removes only that probe's own temporary directory, after checking its
  canonical parent, name, type, and owner. It does not search for or kill
  processes.
- Uses `lstat` on the known production socket path and its parent, without
  connecting to either. It reports type, mode, numeric owner/group, and, when
  available, a summary of ACL entry structure and filesystem type. It does not
  follow a symlink or print raw ACL entries.
- If Docker is available to the invoking user, requests only the fixed
  `fnmusic-sources` container's state, PID, configured/image user, user
  namespace mode, network mode, and mount types/read-write flags. It does not
  print environment variables, mount paths, or the full inspect result. It
  reads that process's UID/GID maps and effective capability value from
  `/proc`. If Docker access or the process data is unavailable, that section
  says `NOT RUN`.

The temporary `/tmp` result describes ordinary Linux DAC behavior only. It
cannot establish the behavior of the production volume's ACLs, fnOS permission
layers, container UID mapping, container network, or a process with the same
UID. The production path inspection is metadata only; it does not prove who
can connect. Testing filesystem behavior on the production volume would create
files there and requires separate user authorization and a separate procedure.

## Architecture limits

The current WebUI shares the `fnmusic-sources` container with the audio-source
services. It calls those services over container loopback and controls them
through `supervisorctl`. Moving only the published port or moving the WebUI to
a separate container can break those functions. Giving a replacement WebUI
unrestricted access to the supervisor control socket would grant broad process
control. This probe measures some runtime identity and mount facts; it does
not choose or validate a production redesign, and it does not close R2.

## Status meanings

- `COMPLETE`: the `/tmp` different-UID denial and same-UID peer check passed;
  review the other sections independently. This does not mean R2 is fixed.
- `FAIL`: the different-UID process connected to the `0600` mock socket.
- `BLOCKED`: a required identity, privilege drop, capability, peer credential,
  or bounded experiment could not be verified.
- `NOT RUN`: an optional observation (usually Docker or `getfacl`) was
  unavailable or denied.
