"""Base interface for cross-venue strategies."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

from common.cross_venue_models import CrossVenueDecision, CrossVenuePosition, CrossVenueSnapshot


class BaseCrossVenueStrategy(ABC):
    def __init__(self, strategy_id: str = "cross_venue"):
        self.strategy_id = strategy_id

    @abstractmethod
    def on_tick(
        self,
        snapshot: CrossVenueSnapshot,
        position: CrossVenuePosition,
        context: Optional[Dict[str, Any]] = None,
    ) -> List[CrossVenueDecision]:
        """Return paired cross-venue decisions for the current tick."""
        ...
