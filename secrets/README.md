# Secrets Management

Encrypted secrets using [SOPS](https://github.com/getsops/sops) + [age](https://github.com/FiloSottile/age).

## Quick Start

```bash
# First time setup (configures git hooks, checks dependencies)
make setup

# Decrypt secrets (requires age key)
make decrypt

# Check current status
make check
```

## Prerequisites

### Install tools

```bash
# age (Debian/Ubuntu)
apt install age

# sops (download binary)
SOPS_VERSION="3.9.4"
curl -fsSL "https://github.com/getsops/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.amd64" \
  -o /usr/local/bin/sops
chmod +x /usr/local/bin/sops
```

### Set up age key

**Option A: Generate new key** (new host)
```bash
mkdir -p ~/.config/sops/age
age-keygen -o ~/.config/sops/age/keys.txt
chmod 600 ~/.config/sops/age/keys.txt

# Add your public key to .sops.yaml and re-encrypt files
```

**Option B: Copy existing key** (additional host)
```bash
mkdir -p ~/.config/sops/age
# Copy keys.txt from another host
chmod 600 ~/.config/sops/age/keys.txt
```

## How It Works

```
secrets/
├── mother.tfvars       # Plaintext (gitignored, local only)
├── mother.tfvars.enc   # Encrypted (committed, safe to publish)
├── father.tfvars       # Plaintext (gitignored)
└── father.tfvars.enc   # Encrypted (committed)
```

- `.enc` files are encrypted and safe to commit
- Plaintext files are gitignored and never leave your machine
- Git hooks auto-encrypt on commit and auto-decrypt on checkout

## Makefile Targets

| Target | Description |
|--------|-------------|
| `make setup` | Configure git hooks, check dependencies |
| `make decrypt` | Decrypt all `.enc` files to plaintext |
| `make encrypt` | Encrypt all plaintext files to `.enc` |
| `make clean` | Remove plaintext files (keeps `.enc`) |
| `make check` | Show current status |
| `make help` | Show all targets |

## Adding a New Host

1. Generate age key on new host (see above)
2. Get the public key: `grep "public key" ~/.config/sops/age/keys.txt`
3. Add public key to `.sops.yaml` (comma-separated list)
4. Re-encrypt all files: `make encrypt`
5. Commit updated `.enc` files

## Removing a Host

1. Remove the host's public key from `.sops.yaml`
2. Re-encrypt all files: `make encrypt`
3. Commit updated `.enc` files
4. Rotate any secrets the host had access to

## Manual Operations

```bash
# Encrypt a single file
sops -e secrets/new.tfvars > secrets/new.tfvars.enc

# Decrypt a single file
sops -d secrets/mother.tfvars.enc > secrets/mother.tfvars

# Edit encrypted file in-place (decrypts to $EDITOR, re-encrypts on save)
sops secrets/mother.tfvars.enc
```
