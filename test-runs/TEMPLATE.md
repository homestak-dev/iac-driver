# [Test Name] Test Run

**Date**: YYYY-MM-DD
**Test ID**: [test-name]-YYYYMMDD-HHMMSS
**Status**: [SUCCESS|FAILED|PARTIAL]

## Summary

[Brief description of what was tested and the outcome]

## Architecture

```
[ASCII diagram of the infrastructure topology]
```

## Event Log

| Timestamp | Phase | Event | Duration |
|-----------|-------|-------|----------|
| HH:MM | Phase N | Description | Xs/min |

**Total Duration**: X minutes

## Virtual Machines

### VM [ID] - [Name] (Inner PVE)

| Property | Value |
|----------|-------|
| VM ID | |
| Hostname | |
| IP Address | |
| MAC Address | |
| Memory | MB |
| Cores | |
| CPU Type | host |
| Disk | GB on [storage] |
| Network | [bridge] |
| OS | |

#### Inner PVE Configuration

| Property | Value |
|----------|-------|
| Storage: local | dir (/var/lib/vz) |
| Storage: local-zfs | zfspool (rpool/data) |
| Bridge Mode | dhcp |
| Bridge Port | eth0 |
| Packer | installed |
| OpenTofu | installed |

### VM [ID] - [Name] (Test VM)

| Property | Value |
|----------|-------|
| VM ID | |
| Hostname | |
| IP Address | |
| MAC Address | |
| Memory | MB |
| Cores | |
| CPU Type | host |
| Disk | GB on [storage] |
| Network | [bridge] |
| OS | |

## Authentication

### SSH Keys

**Key ID**: [key-name]

```
[public key]
```

**Private Key Location**: [path]

### API Tokens

| Host | Token |
|------|-------|
| [host] | [token] |

### Passwords/Hashes

```
[hashed passwords used]
```

## Access Commands

```bash
# [Description]
[command]
```

## Files Created/Modified

### [Component] (commit [hash])
- [file] - [description]

## Issues Encountered

### N. [Issue Title]
**Symptom**: [what happened]
**Cause**: [root cause]
**Fix**: [how it was resolved]

## Verification

```bash
$ [verification command]
[output]
```

## Cleanup Commands

```bash
# [Description]
[command]
```

## Notes

- [Additional observations]
