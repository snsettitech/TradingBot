"""Strategy Playbooks Module.

Contains deterministic, rule-based trading strategies focused on key levels.
"""

from tsxbot.strategies.playbooks.breakout_acceptance import BreakoutAcceptancePlaybook
from tsxbot.strategies.playbooks.fakeout_reversal import FakeoutReversalPlaybook
from tsxbot.strategies.playbooks.level_bounce import LevelBouncePlaybook
from tsxbot.strategies.playbooks.orb_pullback import ORBPullbackPlaybook

__all__ = [
    "BreakoutAcceptancePlaybook",
    "FakeoutReversalPlaybook",
    "LevelBouncePlaybook",
    "ORBPullbackPlaybook",
]
