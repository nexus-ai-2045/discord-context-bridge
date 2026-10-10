"""Small, independently testable gates for user-visible context claims."""

from .policy import CLAIMS, evaluate_context_claims

__all__ = ["CLAIMS", "evaluate_context_claims"]
