# SPDX-FileCopyrightText: 2024 Jonathan Neuhauser <jonathan.neuhauser@outlook.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import abc
from typing import Dict, Union

import inkex

from inkaf.parser.types import AFObject


class Adjustment(abc.ABC):
    name = "Adjustment"

    @abc.abstractmethod
    def to_svg_filter(self) -> inkex.Filter: ...

    @classmethod
    def from_af(cls, adj: AFObject) -> Adjustment:
        tp = adj.get_type()
        if tp not in adj_dict:
            raise NotImplementedError(f"Unknown Adjustment type {adj.get_type()}")
        cl = adj_dict[tp]
        if isinstance(cl, str):
            raise NotImplementedError(
                f"SVG conversion for Adjustment type '{cl}' not implemented"
            )

        return cl._from_af(adj)

    @classmethod
    def _from_af(cls, adj: AFObject) -> Adjustment:
        return cls()


class InvertAdjustment(Adjustment):
    name = "Invert"

    def to_svg_filter(self) -> inkex.Filter:
        # This doesn't seem to be entirely correct yet
        f = inkex.Filter.new(
            inkex.Filter.ColorMatrix.new(
                values="-1 0 0 0 1 0 -1 0 0 1 0 0 -1 0 1 0 0 0 1 0"
            ),
        )
        f.set("color-interpolation-filters", "sRGB")
        return f


adj_dict: Dict[str, Union[str, type[Adjustment]]] = {
    "InRA": InvertAdjustment,
    "ExRA": "Exposure",
    "LeRA": "Levels",
    "WBRA": "White Balance",
    "BCRA": "Brightness / Contrast",
    "SHRA": "Shadows / Hightlights",
    "CrRA": "Curves",
    "CMRA": "Channel Mixer",
    "HsRA": "HSL",
    "CBRA": "Color Balance",
    "GrRA": "Gradient Map",
    "BWRA": "Black & White",
    "RcRA": "Recolor",
    "PoRA": "Posterize",
    "VbRA": "Vibrance",
    "SPRA": "Soft Proof",
    "3DLA": "Lookup Table",
    "PfRA": "Lens Filter",
    "SpTA": "Split Toning",
    "OCRN": "OCIO (OpenColorIO)",
    "NrmA": "Normals",
    "ThRA": "Threshold",
}
