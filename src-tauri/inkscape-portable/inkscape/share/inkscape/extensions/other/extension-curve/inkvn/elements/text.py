"""
VNTextElement, styledText, textProperty
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .base import VNBaseElement
from .styles import VNColor, VNGradient, pathStrokeStyle


@dataclass
class VNTextElement(VNBaseElement):
    """rich text data."""

    string: str
    transform: Optional[List[float]]  # matrix(legacy)
    styledText: Optional[List[singleStyledText]]
    textProperty: Optional[textProperty]


@dataclass
class singleStyledText:
    length: int  # effectiveRange
    fontName: str
    fontSize: float
    alignment: int
    kerning: float
    lineHeight: Optional[Dict]
    fillColor: Optional[VNColor]
    fillGradient: Optional[VNGradient]
    strokeStyle: Optional[pathStrokeStyle]
    strikethrough: bool
    underline: bool

    def convert_text_anchor(self):
        """
        alignment to SVG text-anchor

        0: Left
        1: Center
        2: Right
        3: Justify <- how do i do this?
        """
        return {0: "start", 1: "middle", 2: "end"}.get(self.alignment, "start")


@dataclass
class textProperty:
    # "fixedSize":{"height": float,"width": float}
    # autos don't contain values
    # TODO textProperty is not compatible with format 26 text
    textFrameLimits: Optional[Dict]  # autoWidth, autoHeight, fixedSize
    textFramePivot: Optional[Tuple[float, float]]
