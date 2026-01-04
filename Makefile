# iac-driver Makefile

.PHONY: help install-deps

help:
	@echo "iac-driver - Infrastructure orchestration engine"
	@echo ""
	@echo "  make install-deps  - Install required system packages"
	@echo ""
	@echo "Secrets Management:"
	@echo "  Secrets are managed in the site-config repository."
	@echo "  See: ../site-config/ or https://github.com/homestak-dev/site-config"
	@echo ""
	@echo "  cd ../site-config && make decrypt"

install-deps:
	@echo "Installing iac-driver dependencies..."
	@apt-get update -qq
	@apt-get install -y -qq python3 > /dev/null
	@echo "Done."
