# Test Runs

Documentation of integration test runs for the homestak IaC project.

## Structure

- `TEMPLATE.md` - Template for manual test documentation
- `YYYY-MM-DD.HH:MM:SS-{passed|failed}.md` - Individual test run reports

## Generating Reports

Use the script to auto-generate a test summary:

```bash
../scripts/generate-test-summary.sh [test-name] [inner-pve-ip]
```

## Contents

Each test run report includes:
- Test status and timestamps
- Infrastructure topology
- VM configurations and IPs
- Authentication details
- Verification results
- Cleanup commands

## Security Note

Test run files may contain:
- API tokens
- Password hashes
- SSH public keys
- IP addresses

These are for test/dev environments only. Do not commit production credentials.
