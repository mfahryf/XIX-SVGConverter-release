"""
VNGroupElement
"""

from dataclasses import dataclass
from typing import List

from .base import VNBaseElement


@dataclass
class VNGroupElement(VNBaseElement):
    """Group Element properties."""

    groupElements: List[VNBaseElement]
