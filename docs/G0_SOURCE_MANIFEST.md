# G0 installed-source manifest (read-only candidate)

`tools/g0_source_manifest.py` is an explicit, bounded source-inventory helper. It reads a fixed allowlist beneath one caller-supplied absolute repository root and emits a small JSON manifest containing file metadata and SHA-256 digests. It does not scan directories or follow symbolic links.

The tool always reports `g0_pass: false`, `production_ready: false`, and `effective_unit_verified: false`. Its `fnmusic-ext.service` entry is the repository's **unit template**, not the effective unit loaded by systemd. This manifest alone does not prove which code is running, who can reach a gateway, that an identity header is trusted, or that any service lifecycle is supported. It is not deployment authorization or a G0 result.

## Read boundary

The only paths read are:

| Path | Role |
| --- | --- |
| `VERSION` | Strict short version string plus digest; source bytes are never printed |
| `proxy/app.py` | Source |
| `proxy/webui_gateway.py` | Source |
| `proxy/takeover.py` | Source |
| `proxy/gateway_identity.py` | Source |
| `proxy/gateway_observation.py` | Source |
| `webui-service/app.py` | Source |
| `fnmusic-ext.service` | Repository unit template only |

Each file is limited to 8 MiB. The JSON output is limited to 32 KiB. Missing entries are reported explicitly; missing identity or observer files must not be interpreted as evidence about other installed code. Symbolic links, non-regular files, unstable reads, and oversized files are not hashed. Errors use fixed codes and omit exception details. The utility does not intentionally write, create, rename, unlink, chmod, or chown files; it does not read configuration, `.env`, cookies, logs, process state, sockets, or audio. As with ordinary file reads, filesystem mount policy may update access-time metadata; do not run it where even that normal read effect is disallowed.

## Local synthetic verification

Run the offline contract tests from the repository root:

```sh
python3 -B -m unittest discover -s offline_tests -p test_g0_source_manifest.py -v
```

The filesystem race and `dir_fd` cases require Linux and must execute without skips on the Linux CI matrix (Python 3.11 and 3.13). A Windows skip is not Linux validation. The fixtures use temporary directories and synthetic source bytes; passing them does not attest an installed NAS.

## Optional installed-source collection

Do not infer the installation root from this checkout or from an old observation. Before an operator runs this tool against a device, the exact root and the authorization for reading these source files must be established for that session. The operator should retain or share the resulting JSON only through an approved project channel and review its scope and fixed readiness fields.

```sh
python3 -B tools/g0_source_manifest.py '/verified/extension/repository/root'
```

The program accepts exactly one absolute root argument. It performs no remote connection or service action. A clean manifest only establishes hashes and metadata for the listed source entries at collection time. Compare those digests with a separately reviewed local candidate; do not copy or execute files as part of this step.

To establish the effective systemd unit, process/cgroup ownership, accepted gateway identity, peer/header provenance, or natural drain and rollback behavior requires separate supported read-only evidence. This tool does not collect or infer any of those facts. No device execution is recorded by this document.
