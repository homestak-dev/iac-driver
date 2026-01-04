# Secrets

**This directory is deprecated.**

Secrets are now managed in the [site-config](https://github.com/homestak-dev/site-config) repository.

## Migration

Host credentials have moved to:
```
site-config/hosts/{hostname}.tfvars
```

## Setup

```bash
# Clone site-config as sibling
cd ..
git clone https://github.com/homestak-dev/site-config.git

# Setup and decrypt
cd site-config
make setup
make decrypt
```

The iac-driver will automatically discover `../site-config/` as a sibling directory.
