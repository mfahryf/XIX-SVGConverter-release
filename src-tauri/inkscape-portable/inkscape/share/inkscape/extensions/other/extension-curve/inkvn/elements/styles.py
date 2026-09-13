"""
VNColor, VNGradient, pathStrokeStyle, basicStrokeStyle, styledElementData
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import inkex


class VNColor:
    """
    Represents a color with hex and alpha values.

    for styledTexts-fillColor-value,
    pathStrokeStyles-color and fills-color.
    """

    def __init__(self, color_dict: Dict):
        """
        Initializes the Color object from a dictionary containing color data.
        Handles both HSBA and RGBA formats.
        """
        self.hex: str = "#000000"
        self.alpha: float = 1.0

        color_data = self._extract_color_data(color_dict)
        if color_data is None:
            raise ValueError("Invalid color format")

        self.hex, self.alpha = color_data

    def _extract_color_data(self, color_dict: Dict) -> Optional[Tuple[str, float]]:
        """
        Extracts hex and alpha values from the given dictionary.
        Returns None if no valid color data is found.
        """
        rgba = color_dict.get("rgba")
        hsba = color_dict.get("hsba")

        if rgba:
            r, g, b, a = self._rgba_to_tuple(rgba)
        elif hsba:
            r, g, b, a = self._hsba_to_rgba_tuple(hsba)
        # Check for legacy HSB format
        elif "h" in color_dict and "s" in color_dict and "b" in color_dict:
            r, g, b, a = self._legacy_hsba_to_rgba_tuple(color_dict)
        else:
            return None

        hex_color = self._rgba_to_hex((r, g, b, a))
        return hex_color, a

    def _legacy_hsba_to_rgba_tuple(
        self, hsba: Dict
    ) -> Tuple[float, float, float, float]:
        """Converts an HSBA color to RGBA format."""
        hue = hsba.get("h", 0)
        saturation = hsba.get("s", 0)
        brightness = hsba.get("b", 0)
        alpha = hsba.get("a", 1)

        r, g, b = colorsys.hsv_to_rgb(hue, saturation, brightness)
        return r, g, b, alpha

    def _hsba_to_rgba_tuple(self, hsba: Dict) -> Tuple[float, float, float, float]:
        """Converts an HSBA color to RGBA format."""
        hue = hsba.get("hue", 0)
        saturation = hsba.get("saturation", 0)
        brightness = hsba.get("brightness", 0)
        alpha = hsba.get("alpha", 1)

        r, g, b = colorsys.hsv_to_rgb(hue, saturation, brightness)
        return r, g, b, alpha

    def _rgba_to_tuple(self, rgba: Dict) -> Tuple[float, float, float, float]:
        """Converts an RGBA color to tuple format."""
        red = rgba.get("red", 0)
        green = rgba.get("green", 0)
        blue = rgba.get("blue", 0)
        alpha = rgba.get("alpha", 1)

        return red, green, blue, alpha

    def _rgba_to_hex(self, rgba: Tuple[float, float, float, float]) -> str:
        """Converts RGBA tuple into str hex (#RRGGBB)."""
        r, g, b, _ = rgba
        r = int(r * 255)
        g = int(g * 255)
        b = int(b * 255)

        return f"#{r:02X}{g:02X}{b:02X}"


class VNGradient:
    """
    Represents a gradient with gradient and optional transform.
    """

    def __init__(
        self,
        fill_transform: Dict[str, Any],
        transform_matrix: Optional[List[float]],
        stops: List[Dict],
        typeRawValue: int,
    ):
        """
        Initializes the Gradient object from a Linearity Curve data.
        """
        self.gradient: inkex.Gradient = self._convert_gradient(
            tr=fill_transform, stops=stops, type_value=typeRawValue
        )
        self.transform: Optional[inkex.transforms.Transform] = None
        tr = inkex.transforms.Transform()
        if transform_matrix:
            tr.add_matrix(transform_matrix)
            self.transform = tr

    @staticmethod
    def _convert_gradient(
        tr: Dict[str, Any], stops: List[Dict], type_value: int
    ) -> inkex.Gradient:
        if type_value == 0:  # Linear Gradient
            gradient = inkex.LinearGradient()
            gradient.set("x1", tr["start"][0])
            gradient.set("y1", tr["start"][1])
            gradient.set("x2", tr["end"][0])
            gradient.set("y2", tr["end"][1])

        elif type_value == 1:  # Radial Gradient
            cx, cy = tr["start"]
            fx, fy = tr["end"]

            # Calculate radius (r) from start and end points. ??
            r = ((fx - cx) ** 2 + (fy - cy) ** 2) ** 0.5

            gradient = inkex.RadialGradient()
            gradient.set("cx", cx)
            gradient.set("cy", cy)
            gradient.set("r", r)

        gradient.set("gradientUnits", "userSpaceOnUse")

        # Add color stops
        for stop in stops:
            color = VNColor(color_dict=stop["color"])
            ratio = stop["ratio"]

            svg_stop = inkex.Stop()
            svg_stop.set("offset", ratio)
            svg_stop.style = inkex.Style(
                {
                    "stop-color": color.hex,
                    "stop-opacity": color.alpha,
                }
            )
            gradient.append(svg_stop)

        return gradient


@dataclass
class pathStrokeStyle:
    """
    Linearity Curve stroke format for path and text.
    """

    basicStrokeStyle: Optional[basicStrokeStyle]
    color: VNColor
    width: float


class basicStrokeStyle:
    """cap, dash, join, position"""

    def __init__(
        self, cap: int, dashPattern: Optional[List[float]], join: int, position: int
    ):
        self.cap: str = self._cap_to_svg(cap)
        self.dashPattern: List[float] = self._process_dash_pattern(dashPattern)
        self.join: str = self._join_to_svg(join)
        self.position: int = position

    @staticmethod
    def _process_dash_pattern(dashPattern: Optional[List[float]]) -> List[float]:
        if dashPattern is None:
            return [0]

        # delete trailing zeros
        while len(dashPattern) > 1 and dashPattern[-1] == 0:
            dashPattern.pop()

        return dashPattern

    @staticmethod
    def _cap_to_svg(cap):
        """Returns value for stroke-linecap attribute."""
        cap_map = {0: "butt", 1: "round", 2: "square"}
        return cap_map.get(cap, "butt")

    @staticmethod
    def _join_to_svg(join: int) -> str:
        """Returns value for stroke-linejoin attribute."""
        join_map = {
            0: "miter",
            1: "round",
            2: "bevel",
        }
        return join_map.get(join, "miter")


@dataclass
class styledElementData:
    """
    Stores style attributes for text for passing to
    read_vn_abst_path / read_vn_abst_text.
    """

    styled_data: Dict
    mask: int
    stroke: pathStrokeStyle
    color: Optional[VNColor]
    grad: Optional[VNGradient]
