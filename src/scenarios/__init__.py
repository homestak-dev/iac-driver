"""Scenario definitions and orchestration."""

import logging
from pathlib import Path
from typing import Protocol, runtime_checkable

from config import HostConfig
from reporting import TestReport

logger = logging.getLogger(__name__)


@runtime_checkable
class Scenario(Protocol):
    """Protocol for scenario definitions."""
    name: str
    description: str

    def get_phases(self, config: HostConfig) -> list[tuple[str, object, str]]:
        """Return list of (phase_name, action, description) tuples."""
        ...


class Orchestrator:
    """Coordinates scenario execution."""

    def __init__(
        self,
        scenario: Scenario,
        config: HostConfig,
        report_dir: Path,
        skip_phases: list[str] = None
    ):
        self.scenario = scenario
        self.config = config
        self.report_dir = report_dir
        self.skip_phases = skip_phases or []
        self.report = TestReport(host=config.name, report_dir=report_dir, scenario=scenario.name)
        self.context = {}

    def run(self) -> bool:
        """Run all phases. Returns True if all passed."""
        logger.info(f"Starting scenario '{self.scenario.name}' on host: {self.config.name}")
        self.report.start()

        phases = self.scenario.get_phases(self.config)
        all_passed = True

        for phase_name, action, description in phases:
            if phase_name in self.skip_phases:
                logger.info(f"Skipping phase: {phase_name}")
                self.report.skip_phase(phase_name, description)
                continue

            logger.info(f"Running phase: {phase_name} - {description}")
            self.report.start_phase(phase_name, description)

            try:
                result = action.run(self.config, self.context)
                if result.success:
                    logger.info(f"Phase {phase_name} passed")
                    self.report.pass_phase(phase_name, result.message, result.duration)
                    self.context.update(result.context_updates or {})
                else:
                    logger.error(f"Phase {phase_name} failed: {result.message}")
                    self.report.fail_phase(phase_name, result.message, result.duration)
                    all_passed = False
                    if not result.continue_on_failure:
                        break
            except Exception as e:
                logger.exception(f"Phase {phase_name} raised exception")
                self.report.fail_phase(phase_name, str(e), 0)
                all_passed = False
                break

        self.report.finish(all_passed)
        return all_passed


# Registry of available scenarios
_scenarios: dict[str, type] = {}


def register_scenario(cls: type) -> type:
    """Decorator to register a scenario class."""
    _scenarios[cls.name] = cls
    return cls


def get_scenario(name: str) -> Scenario:
    """Get a scenario instance by name."""
    if name not in _scenarios:
        available = list(_scenarios.keys())
        raise ValueError(f"Unknown scenario: {name}. Available: {available}")
    return _scenarios[name]()


def list_scenarios() -> list[str]:
    """List available scenario names."""
    return sorted(_scenarios.keys())


# Import scenarios to trigger registration
from scenarios import nested_pve  # noqa: E402, F401
from scenarios import vm  # noqa: E402, F401
from scenarios import cleanup_nested_pve  # noqa: E402, F401
from scenarios import pve_setup  # noqa: E402, F401
from scenarios import bootstrap  # noqa: E402, F401
from scenarios import packer_build  # noqa: E402, F401
