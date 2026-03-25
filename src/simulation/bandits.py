"""
Adaptive Learning & Recommender Engine.

Implements a Multi-Armed Bandit using Thompson Sampling to dynamically
recommend clinical cases based on the resident's historical engagement.
Arms are combinations of <specialty>_<difficulty>.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class BanditState:
    arm_id: str
    alpha: int  # successes (clicks/completions)
    beta: int   # failures (ignores)


class ThompsonSamplingBandit:
    """
    Thompson Sampling engine for recommending clinical cases.
    
    In Thompson Sampling, each arm's underlying success probability is 
    modeled as a Beta distribution: Beta(alpha, beta).
    We sample a random value from each arm's Beta distribution, and 
    choose the arm(s) with the highest sampled values.
    """
    
    def __init__(self, states: dict[str, BanditState] | None = None):
        """
        Initialize the bandit with historical states.
        `states` maps arm_id -> BanditState.
        """
        self.states = states or {}
    
    def get_or_create_arm(self, arm_id: str) -> BanditState:
        """Ensure an arm exists with a uniform prior Beta(1, 1)."""
        if arm_id not in self.states:
            self.states[arm_id] = BanditState(arm_id=arm_id, alpha=1, beta=1)
        return self.states[arm_id]
        
    def sample_arms(self, arm_ids: list[str], k: int = 1) -> list[str]:
        """
        Given a list of available arm_ids (e.g., all distinct specialty+difficulty
        combinations present in the database), sample from their Beta distributions
        and return the top `k` arms.
        """
        if not arm_ids:
            return []
            
        sampled = []
        for arm_id in arm_ids:
            state = self.get_or_create_arm(arm_id)
            # Sample from Beta(alpha, beta)
            theta = random.betavariate(state.alpha, state.beta)
            sampled.append((theta, arm_id))
            
        # Sort descending by sampled probability
        sampled.sort(key=lambda x: x[0], reverse=True)
        return [arm_id for _, arm_id in sampled[:k]]
        
    def update_arm(self, arm_id: str, success: bool) -> None:
        """
        Update the alpha/beta counts for a specific arm.
        """
        state = self.get_or_create_arm(arm_id)
        if success:
            state.alpha += 1
        else:
            state.beta += 1
