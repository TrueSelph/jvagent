"""Mock storefront action: product-FAQ lookup + product-catalog search.

Ships with the leadgen reference agent to give the FAQ and product-search
skills real tools to call. Backed by in-memory dummy data — not for
production use.
"""

from .storefront_action import StorefrontAction

__all__ = ["StorefrontAction"]
