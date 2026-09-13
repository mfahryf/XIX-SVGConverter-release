#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024 Manpreet Singh <manpreet.singh.dev@proton.me>
# SPDX-FileCopyrightText: 2024 Jonathan Neuhauser <jonathan.neuhauser@outlook.com>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from abc import abstractmethod
import abc
import cmath
from enum import Enum
from typing import Dict, List, Optional, Tuple, Type

import math
import inkex
from inkex.paths import Line, Move, ZoneClose, Curve, Arc, move
from inkex import Path, Vector2d, BoundingBox

from ..parser.types import AFObject
from .util import bbox_helper, safe_div, interpolate_float

# TODO: For shapes, the stroke's start point should be at the top right corner of the shape


class AFShape:
    _id: int = 0

    def __init__(self, b_box: BoundingBox):
        self.b_box = b_box

        self.id = self.__class__._id
        self.__class__._id += 1

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFShape:
        stype = shpe.get_type()
        assert stype is not None, "Undefined Shape type"
        return cls(b_box)


class AFPointsShapeBase(AFShape):
    def __init__(self, b_box: BoundingBox):
        super().__init__(b_box)

    @property
    @abstractmethod
    def points(self) -> List[Vector2d]: ...


class AFEllipseBase(AFShape):
    def __init__(self, b_box: BoundingBox):
        super().__init__(b_box)

    @property
    def radii(self) -> Vector2d:
        b_box = self.b_box
        return Vector2d(b_box.width / 2, b_box.height / 2)


class AFCornerPos(Enum):
    TL = 0
    TR = 1
    BR = 2
    BL = 3


class AFCornerType(Enum):
    ROUNDED = 0
    STRAIGHT = 1
    CONCAVE = 2
    CUTOUT = 3
    NONE = 4


corner_map = {
    AFCornerType.NONE: "F",  # radius will be set to 0
    AFCornerType.ROUNDED: "F",
    AFCornerType.CONCAVE: "IF",
    AFCornerType.STRAIGHT: "C",
}


class AFRectangle(AFShape):
    def __init__(
        self,
        b_box: BoundingBox,
        radii: List[float] = [0.25] * 4,
        corner_types: List[AFCornerType] = [AFCornerType.NONE] * 4,
        lock: bool = True,
        absolute_size: bool = False,
    ):
        super().__init__(b_box)
        self.lock = lock
        self.absolute_size = absolute_size
        self.corner_types = corner_types
        self.radii = radii

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFRectangle:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShNR", "Not a rectangle"
        assert isinstance(sup, AFRectangle)

        if "Lock" in shpe:
            sup.lock = shpe["Lock"]
        if "AbSz" in shpe:
            sup.absolute_size = shpe["AbSz"]
        if "CTyp" in shpe:
            sup.corner_types = [AFCornerType(ct.id) for ct in shpe["CTyp"]]
        if "ShCR" in shpe:
            sup.radii = shpe["ShCR"]
        return sup

    @property
    def radii(self) -> List[float]:
        radii = self._radii
        if not self.absolute_size:
            radii = [r * min(self.b_box.width, self.b_box.height) for r in radii]
        if self.lock:
            radii = [radii[0]] * 4

        radii = [
            r if c != AFCornerType.NONE else 0.0
            for r, c in zip(radii, self.corner_types)
        ]

        if (
            radii[0] + radii[1] > self.b_box.width
            or radii[2] + radii[3] > self.b_box.width
            or radii[3] + radii[0] > self.b_box.height
            or radii[1] + radii[2] > self.b_box.height
        ):
            ratio = min(
                safe_div(self.b_box.width, (radii[0] + radii[1]), float("inf")),
                safe_div(self.b_box.width, (radii[2] + radii[3]), float("inf")),
                safe_div(self.b_box.height, (radii[3] + radii[0]), float("inf")),
                safe_div(self.b_box.height, (radii[1] + radii[2]), float("inf")),
            )
            if ratio != float("inf"):
                radii = [r * ratio for r in radii]
        return radii

    @radii.setter
    def radii(self, value: List[float]):
        assert len(value) == 4
        self._radii = value

    @property
    def corner_types(self) -> List[AFCornerType]:
        if self.lock:
            return [AFCornerType(self._corner_types[0])] * 4
        return [ct for ct in self._corner_types]

    @corner_types.setter
    def corner_types(self, value: List[AFCornerType]):
        assert len(value) == 4 and all(isinstance(ct, AFCornerType) for ct in value)
        self._corner_types = value

    def _corner_path(self, pos: AFCornerPos) -> List[Tuple[str, List[float]]]:
        corner_type = self.corner_types[pos.value]
        radius = self.radii[pos.value]

        if radius <= 0:
            return []

        dx = radius if pos in [AFCornerPos.TL, AFCornerPos.TR] else -radius
        dy = radius if pos in [AFCornerPos.TR, AFCornerPos.BR] else -radius

        if corner_type == AFCornerType.STRAIGHT:
            return [("l", [dx, dy])]

        if corner_type in [AFCornerType.ROUNDED, AFCornerType.CONCAVE]:
            # arc: a rx ry x-axis-rotation large-arc-flag sweep-flag dx dy
            is_round = corner_type == AFCornerType.ROUNDED
            return [("a", [radius, radius, 0, 0, int(is_round), dx, dy])]

        if corner_type == AFCornerType.CUTOUT:
            return (
                [("h", [dx]), ("v", [dy])]
                if pos in [AFCornerPos.TL, AFCornerPos.BR]
                else [("v", [dy]), ("h", [dx])]
            )

        return (
            [("h", [dx]), ("v", [dy])]
            if pos not in [AFCornerPos.TL, AFCornerPos.BR]
            else [("v", [dy]), ("h", [dx])]
        )

    def _corner_path_effect(self) -> inkex.PathEffect:
        assert all(ct in corner_map for ct in self.corner_types), (
            f"Invalid corner types {[ct for ct in self.corner_types if ct not in corner_map]}"
        )

        params = " @ ".join(
            f"{corner_map[c]},1,0,1,0,{r / length},0,1"
            for c, r, length in zip(
                self.corner_types, self.radii, [self.b_box.width, self.b_box.height] * 2
            )
        )
        return inkex.PathEffect.new(
            effect="fillet_chamfer",
            lpeversion="1",
            method="arc",
            flexible="true",  # radius in %
            satellites_param=params,  # Inkscape 1.2
            nodesatellites_param=params,  # Inkscape 1.3
        )


class AFEllipse(AFEllipseBase):
    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFEllipse:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShpE", "Not an ellipse"
        assert isinstance(sup, AFEllipse)
        return sup


class AFTriangle(AFPointsShapeBase):
    def __init__(self, b_box: BoundingBox, pos: float = 0.5):
        super().__init__(b_box)
        self.pos = pos

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFTriangle:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShpT", "Not a triangle"
        assert isinstance(sup, AFTriangle)

        if "Pos " in shpe:
            sup.pos = shpe["Pos "]

        return sup

    @property
    def points(self) -> List[Vector2d]:
        b_box = self.b_box
        return [
            Vector2d(b_box.left + (b_box.width * self.pos), b_box.top),
            Vector2d(b_box.right, b_box.bottom),
            Vector2d(b_box.left, b_box.bottom),
        ]


class AFDiamond(AFPointsShapeBase):
    def __init__(self, b_box: BoundingBox, pos: float = 0.5):
        super().__init__(b_box)
        self.pos = pos

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFDiamond:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShpD", "Not a diamond"
        assert isinstance(sup, AFDiamond)

        if "Pos " in shpe:
            sup.pos = shpe["Pos "]

        return sup

    @property
    def points(self) -> List[Vector2d]:
        b_box = self.b_box
        hpos, vpos = 0.5, 1 - self.pos
        return [
            Vector2d(b_box.left + (b_box.width * hpos), b_box.top),
            Vector2d(b_box.right, b_box.top + (b_box.height * vpos)),
            Vector2d(b_box.left + (b_box.width * hpos), b_box.bottom),
            Vector2d(b_box.left, b_box.top + (b_box.height * vpos)),
        ]


class AFTrapezoid(AFPointsShapeBase):
    def __init__(self, b_box: BoundingBox, posl: float = 0.25, posr: float = 0.75):
        super().__init__(b_box)
        self.posl = posl
        self.posr = posr

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFTrapezoid:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShTz", "Not a trapezoid"
        assert isinstance(sup, AFTrapezoid)

        if "PosL" in shpe:
            sup.posl = shpe["PosL"]
        if "PosR" in shpe:
            sup.posr = shpe["PosR"]

        return sup

    @property
    def points(self) -> List[Vector2d]:
        b_box = self.b_box
        posl, posr = self.posl, self.posr
        return [
            Vector2d(b_box.right, b_box.bottom),
            Vector2d(b_box.left, b_box.bottom),
            Vector2d(b_box.left + (b_box.width * posl), b_box.top),
            Vector2d(b_box.left + (b_box.width * posr), b_box.top),
        ]


class AFPie(AFEllipseBase):
    def __init__(
        self,
        b_box: BoundingBox,
        start_angle: float = math.pi / 2,
        end_angle: float = 0.0,
        inner_radius: float = 0.0,
    ):
        super().__init__(b_box)
        self.start_angle = start_angle
        self.end_angle = end_angle
        self.inner_radius = inner_radius

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFPie:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShPi", "Not a pie"
        assert isinstance(sup, AFPie)

        if "AngS" in shpe:
            sup.start_angle = shpe["AngS"]
        if "AngE" in shpe:
            sup.end_angle = shpe["AngE"]
        if "IRad" in shpe:
            sup.inner_radius = shpe["IRad"]

        return sup


class AFSquareStar(AFPointsShapeBase, AFEllipseBase):
    def __init__(
        self,
        b_box: BoundingBox,
        sides: int = 5,
        cutout: float = 0.5,
    ):
        super().__init__(b_box)
        self.sides = sides
        self.cutout = cutout

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFSquareStar:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShSS", "Not a square star"
        assert isinstance(sup, AFSquareStar)

        if "COut" in shpe:
            sup.cutout = shpe["COut"]
        if "Side" in shpe:
            sup.sides = shpe["Side"]

        return sup

    @property
    def points(self) -> List[Vector2d]:
        center, radii, sides, cutout = (
            self.b_box.center,
            self.radii,
            self.sides,
            self.cutout,
        )
        ellipse_points = points_on_ellipse(radii, center, sides)

        points: List[Vector2d] = []
        for p1, p2 in zip(ellipse_points, ellipse_points[1:] + [ellipse_points[0]]):
            mid = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2

            points.append(
                Vector2d(
                    interpolate_float(p1[0], mid[0], cutout),
                    interpolate_float(p1[1], mid[1], cutout),
                )
            )
            if cutout != 0:
                points.extend(
                    (
                        Vector2d(
                            interpolate_float(p2[0], mid[0], cutout),
                            interpolate_float(p2[1], mid[1], cutout),
                        ),
                        Vector2d(
                            interpolate_float(p2[0], center[0], cutout),
                            interpolate_float(p2[1], center[1], cutout),
                        ),
                    )
                )

        return points


class AFDoubleStar(AFPointsShapeBase, AFEllipseBase):
    def __init__(
        self,
        b_box: BoundingBox,
        points: int = 5,
        inner_radius: float = 0.525731086730957,  # default in Designer
        point_radius: float = 0.80901700258255,  # default in Designer
    ):
        super().__init__(b_box)
        self.num_points = points
        self.inner_radius = inner_radius
        self.point_radius = point_radius

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFDoubleStar:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShDS", "Not a square star"
        assert isinstance(sup, AFDoubleStar)

        if "Pnts" in shpe:
            sup.num_points = shpe["Pnts"]
        if "IRad" in shpe:
            sup.inner_radius = shpe["IRad"]
        if "PRad" in shpe:
            sup.point_radius = shpe["PRad"]

        return sup

    @property
    def points(self) -> List[Vector2d]:
        center, radii, npoints = self.b_box.center, self.radii, self.num_points
        dt = 2 * math.pi / npoints
        outer_p1 = points_on_ellipse(radii, center, npoints)
        outer_p2 = points_on_ellipse(radii * self.point_radius, center, npoints, dt / 2)
        inner_p = points_on_ellipse(
            radii * self.inner_radius, center, npoints * 2, dt / 4
        )

        return [
            p
            for pts in zip(outer_p1, inner_p[::2], outer_p2, inner_p[1::2])
            for p in pts
        ]


def points_on_ellipse(
    radii: Vector2d,
    center: Vector2d,
    count: int,
    offset: float = 0,
) -> List[Vector2d]:
    """Returns a list of `count` number of points on an ellipse."""
    step = 2 * math.pi / count
    return [
        Vector2d(
            radii.x * math.cos(i * step - 90 * math.pi / 180 + offset) + center.x,
            radii.y * math.sin(i * step - 90 * math.pi / 180 + offset) + center.y,
        )
        for i in range(count)
    ]


class AFCurveShape(AFEllipseBase, abc.ABC):
    def get_svg_path(self) -> Path:
        path = self._get_svg_path()
        return path.transform(
            inkex.Transform(scale=tuple(self.radii), translate=tuple(self.b_box.center))
        )

    @abc.abstractmethod
    def _get_svg_path(self): ...


class AFStar(AFCurveShape):
    def __init__(
        self,
        b_box: BoundingBox,
        points: int = 5,
        inner_radius: float = 1,  # maximum if missing
        outer_circle: float = 0.0,
        inner_circle: float = 0.0,
        left_curve: float = 0.0,
        right_curve: float = 0.0,
        curved_edges: bool = False,
    ):
        super().__init__(b_box)
        self.num_points = points
        self.inner_radius = inner_radius
        self.outer_circle = outer_circle
        self.inner_circle = inner_circle
        self.left_curve = left_curve
        self.right_curve = right_curve
        self.curved_edges = curved_edges

    @property
    def inner_radius_clipped(self):
        """point distance from center, limited such that the outer tip is acute
        https://en.wikipedia.org/wiki/Apothem#Finding_the_apothem"""
        return min(self.inner_radius, math.cos(math.pi / self.num_points))

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFStar:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShSt", "Not a star"
        assert isinstance(sup, AFStar)

        if "CrcI" in shpe:
            sup.inner_circle = shpe["CrcI"]
        if "CrcO" in shpe:
            sup.outer_circle = shpe["CrcO"]
        if "CrvL" in shpe:
            sup.left_curve = shpe["CrvL"]
        if "CrvR" in shpe:
            sup.right_curve = shpe["CrvR"]
        if "Lgcy" in shpe:
            sup.curved_edges = shpe["Lgcy"]
        if "Pnts" in shpe:
            sup.num_points = shpe["Pnts"]
        if "IRad" in shpe:
            sup.inner_radius = shpe["IRad"]
        return sup

    def _get_rounded_corner_svg_path(self) -> Path:
        # The outer radius is normalized by the maximum possible outer radius (where two adjacent outer circles touch)
        max_outer_circle = math.sin(math.pi / self.num_points) / (
            1 + math.sin(math.pi / self.num_points)
        )

        # The actual outer circle radius
        outer_circle_radius = self.outer_circle * max_outer_circle

        # center points of inner and outer circles
        inner_centers = points_on_ellipse(
            Vector2d(self.inner_radius_clipped, self.inner_radius_clipped),
            Vector2d(0, 0),
            self.num_points,
            -math.pi / self.num_points,
        )
        outer_centers = points_on_ellipse(
            Vector2d(1 - outer_circle_radius, 1 - outer_circle_radius),
            Vector2d(0, 0),
            self.num_points,
        )

        # The inner circle is given as a ratio, but not sure which one. A good fit seems to be
        rin_factor = 1 / (self.num_points + 4) * 3.33

        # However it is capped by two quantities:
        # - the sum of both radii cannot be larger than the distance between outer and inner center
        # - the inner circles can at most touch each other
        inner_circle_radius = min(
            self.inner_circle * rin_factor,
            (outer_centers[0] - inner_centers[0]).length - outer_circle_radius,
            math.sin(math.pi / self.num_points) * self.inner_radius_clipped,
        )

        path = Path()
        outer_centers.append(outer_centers[0])
        inner_centers.append(inner_centers[0])

        for i, (pi, po, pi2) in enumerate(
            zip(inner_centers, outer_centers, inner_centers[1:] + inner_centers[0:1])
        ):
            tp1 = get_internal_tangent_points(
                outer_circle_radius, inner_circle_radius, po, pi
            )
            tp2 = get_internal_tangent_points(
                outer_circle_radius, inner_circle_radius, po, pi2
            )

            if i == 0:
                path.append(Move(*tp1[2]))
            else:
                if not math.isclose(inner_circle_radius, 0):
                    path.append(
                        Arc(
                            inner_circle_radius,
                            inner_circle_radius,
                            0,
                            False,
                            False,
                            *tp1[3],
                        )
                    )
                path.append(Line(*tp1[2]))

            if i < self.num_points:
                if not math.isclose(outer_circle_radius, 0):
                    # Large arc if angle between (tp1[2] - po) and (tp2[0]- po) > 180°
                    # Formula: https://stackoverflow.com/a/16544330
                    v1 = tp1[2] - po
                    v2 = tp2[0] - po
                    dot = v1.dot(v2)
                    det = v1.x * v2.y - v1.y * v2.x

                    path.append(
                        Arc(
                            outer_circle_radius,
                            outer_circle_radius,
                            0,
                            False if math.atan2(det, dot) > 0 else True,
                            True,
                            *tp2[0],
                        )
                    )
                path.append(Line(*tp2[1]))
        path.append(ZoneClose())

        return path

    def _get_path_with_curved_edges(self) -> Path:
        # center points of inner and outer circles
        inner_pts = points_on_ellipse(
            Vector2d(self.inner_radius_clipped, self.inner_radius_clipped),
            Vector2d(0, 0),
            self.num_points,
            math.pi / self.num_points,
        )
        outer_pts = points_on_ellipse(
            Vector2d(1, 1),
            Vector2d(0, 0),
            self.num_points,
        )

        n = self.num_points
        ri_ratio = self.inner_radius_clipped

        def get_edge_handles(right=True) -> Tuple[Optional[complex], Optional[complex]]:
            if right:
                percent = self.right_curve
            else:
                percent = self.left_curve
            if percent == 0:
                return None, None

            # There's probably a geometrical justification to these formulas, but I've figured
            # them out through regression

            v1l = (
                math.sin(math.pi / n) * 2 ** (-1 / 3) * percent
                if percent > 0
                else (-(1 / ri_ratio - 1 / math.cos(math.pi / n)) * ri_ratio * percent)
            )

            v1a = math.pi / 2 if percent < 0 else 0

            v2l = (
                (math.cos(math.pi / n) * (1 - ri_ratio) * ((percent - 0.5) / 0.5) ** 3)
                * 2 ** (-1 / 3)
                if percent > 0.5
                else (
                    -math.tan(math.pi / n) * ri_ratio * percent * (2 + percent)
                    if percent < 0
                    else 0
                )
            )
            v2a = (
                -math.pi / 2 + math.pi / n
                if percent >= 0.5
                else (-math.pi + math.pi / n if percent < 0 else 0)
            )

            if right:
                return cmath.rect(v1l, v1a), cmath.rect(v2l, v2a)
            else:
                return -cmath.rect(v1l, v1a).conjugate(), -cmath.rect(
                    v2l, v2a
                ).conjugate()

        lh1, lh2 = get_edge_handles(False)
        rh1, rh2 = get_edge_handles(True)

        path = Path([Move(outer_pts[0])])

        for i, (po, pi, po2) in enumerate(
            zip(outer_pts, inner_pts, outer_pts[1:] + outer_pts[0:1])
        ):
            angle_incr = cmath.rect(1, 2 * math.pi / n * i)
            if rh1 is not None and rh2 is not None:
                path.append(Curve(po + rh1 * angle_incr, pi + rh2 * angle_incr, pi))
            else:
                # Just draw a line
                path.append(Line(pi))
            angle_incr = cmath.rect(1, 2 * math.pi / n * (i + 1))
            if lh1 is not None and lh2 is not None:
                path.append(Curve(pi + lh2 * angle_incr, po2 + lh1 * angle_incr, po2))
            else:
                # Just draw a line
                path.append(Line(po2))

        path.append(ZoneClose())
        return path

    def _get_svg_path(self) -> Path:
        if not self.curved_edges:
            # Rounded Corners
            return self._get_rounded_corner_svg_path()
        return self._get_path_with_curved_edges()


# https://en.wikipedia.org/wiki/Tangent_lines_to_circles#Tangent_lines_to_two_circles
# https://math.stackexchange.com/a/4048944
def get_internal_tangent_points(
    r1: float, r2: float, c1: Vector2d, c2: Vector2d
) -> List[Vector2d]:
    hypotenuse = (c1 - c2).length
    opp = r1 + r2

    if opp > hypotenuse:
        if not math.isclose(opp, hypotenuse):
            raise ValueError(
                f"math domain error. circles intersect ({r1} + {r2} = {opp}) > {hypotenuse}"
            )
        opp = hypotenuse
    theta = (
        math.atan2(c2.y - c1.y, c2.x - c1.x) + math.asin(opp / hypotenuse) - math.pi / 2
    )
    theta2 = (
        math.atan2(c2.y - c1.y, c2.x - c1.x) - math.asin(opp / hypotenuse) + math.pi / 2
    )

    return [
        Vector2d(c1.x + r1 * math.cos(theta), c1.y + r1 * math.sin(theta)),
        Vector2d(
            c2.x + r2 * math.cos(theta + math.pi),
            c2.y + r2 * math.sin(theta + math.pi),
        ),
        Vector2d(c1.x + r1 * math.cos(theta2), c1.y + r1 * math.sin(theta2)),
        Vector2d(
            c2.x + r2 * math.cos(theta2 + math.pi),
            c2.y + r2 * math.sin(theta2 + math.pi),
        ),
    ]


class AFCrescent(AFCurveShape):
    def __init__(
        self,
        b_box: BoundingBox,
        curve_l: float = -100,
        curve_r: float = 100,
    ):
        super().__init__(b_box)
        self.curve_l = curve_l
        self.curve_r = curve_r

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFCrescent:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShCr", "Not a crescent"
        assert isinstance(sup, AFCrescent)

        if "ArcL" in shpe:
            sup.curve_l = shpe["ArcL"]
        if "ArcR" in shpe:
            sup.curve_r = shpe["ArcR"]
        return sup

    def _get_svg_path(self) -> Path:
        path = Path([Move(0, 1)])

        if self.curve_l != 0:
            rl = (self.curve_l + 1 / self.curve_l) * 1 / 2
            path.append(
                Arc(
                    Vector2d(rl, rl),
                    0,
                    False,
                    self.curve_l < 0,
                    Vector2d(0, -1),
                )
            )
        else:
            path.append(Line(0, -1))

        if self.curve_r != 0:
            rr = (self.curve_r + 1 / self.curve_r) * 1 / 2
            path.append(
                Arc(
                    Vector2d(rr, rr),
                    0,
                    False,
                    self.curve_r > 0,
                    Vector2d(0, 1),
                )
            )
        else:
            path.append(Line(0, 1))
        path.append(ZoneClose())

        return path


class AFHeart(AFCurveShape):
    def __init__(
        self,
        b_box: BoundingBox,
        design_point: float = 0.1,
    ):
        super().__init__(b_box)
        self.design_point = design_point

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFHeart:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShHt", "Not a heart"
        assert isinstance(sup, AFHeart)

        if "Sprd" in shpe:
            sup.design_point = shpe["Sprd"]
        return sup

    def _get_svg_path(self) -> Path:
        bottom = -1 + self.design_point * 2
        return Path(
            [
                Move(0, bottom),
                Curve(0.210521, -1, 0.631586, -1, 0.842101, -0.8),
                Curve(1.05263, -0.6, 1.05263, -0.2, 0.842101, 0.2),
                Curve(0.69473, 0.5, 0.315796, 0.8, 0, 1),
                Curve(-0.315789, 0.8, -0.694735, 0.5, -0.842105, 0.2),
                Curve(-1.05263, -0.2, -1.05263, -0.6, -0.842105, -0.8),
                Curve(-0.631577, -1, -0.210525, -1, 0, bottom),
                ZoneClose(),
            ]
        )


class AFPolygon(AFCurveShape):
    def __init__(
        self,
        b_box: BoundingBox,
        curvature: float = 0,
        sides: int = 5,
        smooth: bool = False,
    ):
        super().__init__(b_box)
        self.curv = curvature
        self.sides = sides
        self.smooth = smooth

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFPolygon:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShPy", "Not a polygon"
        assert isinstance(sup, AFPolygon)

        if "Side" in shpe:
            sup.sides = shpe["Side"]
        if "Curv" in shpe:
            sup.curv = shpe["Curv"]
        if "Smth" in shpe:
            sup.smooth = shpe["Smth"]

        return sup

    def _get_svg_path(self) -> Path:
        # The handles of each point are symmetric with respect to the point's
        # connection line to the polygon center.
        # For 100% curvature, the handles coincide with the bezier approximation
        # of a circle, i.e. length of 4/3 tan(pi/(2n)) and perpendicular to the
        # point's symmetry axis.
        # For curvature -> 0+, the handle is placed at 1/3 of the line
        # between the current and the next node.
        # For curvatures in between, the handle position is linearly interpolated
        # between the 0 and 100% positions.
        # For negative curvatures, the 100% handle is mirrored on the straight
        # connection line between the point and its neighbor.

        pt100 = Vector2d(4 / 3 * math.tan(math.pi / (2 * self.sides)), 0)
        pts = points_on_ellipse(
            Vector2d(1, 1),
            Vector2d(0, 0),
            self.sides,
        )

        sidelen = (pts[1] - pts[0]).length

        pt0 = cmath.rect(sidelen * 1 / 3, math.pi / self.sides)

        ptm100 = cmath.rect(pt100.length, 2 * math.pi / self.sides)

        if self.curv > 0:
            if self.smooth:
                hdl = complex(pt100) * self.curv
            else:
                hdl = complex(pt100) * self.curv + pt0 * (1 - self.curv)
        else:
            hdl = ptm100 * (-self.curv) + pt0 * (1 + self.curv)
        hdl_in = -complex(hdl).conjugate()

        path = Path([Move(pts[0])])

        # Draw the polygon
        for i, (pt, pt2) in enumerate(zip(pts, pts[1:] + pts[0:1])):
            angle_incr = cmath.rect(1, 2 * math.pi / self.sides * i)
            angle_incr2 = cmath.rect(1, 2 * math.pi / self.sides * (i + 1))
            if self.curv == 0:
                path.append(Line(pt2))
            else:
                path.append(
                    Curve(pt + hdl * angle_incr, pt2 + hdl_in * angle_incr2, pt2)
                )

        path.append(ZoneClose())
        return path


class AFSegment(AFCurveShape):
    def __init__(
        self,
        b_box: BoundingBox,
        angle: float = 0,
        pos0: float = 0.25,
        pos1: float = 0,
    ):
        super().__init__(b_box)
        self.angle = angle
        self.pos0 = pos0
        self.pos1 = pos1

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFSegment:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShSg", "Not a segment"
        assert isinstance(sup, AFSegment)

        if "Angl" in shpe:
            sup.angle = shpe["Angl"]
        if "Pos0" in shpe:
            sup.pos0 = shpe["Pos0"]
        if "Pos1" in shpe:
            sup.pos1 = shpe["Pos1"]

        return sup

    def _get_svg_path(self):
        p = Path()

        # First the unrotated version, then we apply a transform in the end

        pos0 = (0.5 - self.pos0) / 0.5
        pos1 = (self.pos1 - 0.5) / 0.5

        p1 = math.cos(math.asin(pos0)) + pos0 * 1j
        p2 = -math.cos(math.asin(pos1)) - pos1 * 1j

        if pos0 != 1:
            # Start with the first cutoff segment
            p.append(Move(p1))
            p.append(Line(-p1.conjugate()))
            if pos1 == 1:
                p.append(Arc(1 + 1j, 0, True if pos0 > 0 else False, True, p1))
                p.append(ZoneClose())
            else:
                p.append(Arc(1 + 1j, 0, False, True, p2))
        if pos1 != 1:
            if pos0 == 1:
                p.append(Move(p2))
            p.append(Line(-p2.conjugate()))

            if pos0 == 1:
                p.append(Arc(1 + 1j, 0, True if pos1 > 0 else False, True, p2))
            else:
                p.append(Arc(1 + 1j, 0, False, True, p1))
            p.append(ZoneClose())
        if pos0 == 1 and pos1 == 1:
            p.append(move(0, 1))
            p.append(Arc(1 + 1j, 0, True, False, 0 - 1j))
            p.append(Arc(1 + 1j, 0, True, False, 0 + 1j))

        # Now rotate the path
        return p.transform(inkex.Transform(rotate=(90 - self.angle * 180 / math.pi)))


class AFTear(AFCurveShape):
    def __init__(
        self,
        b_box: BoundingBox,
        fixed_rounding: bool = False,
        curv: float = 0.3,
        bend: float = 0,
        tail: float = 0.5,
        ball: float = 0.25,
    ):
        super().__init__(b_box)
        self.fixed_rounding = fixed_rounding
        self.curv = curv
        self.tail = tail
        self.ball = ball
        self.bend = bend

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFTear:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShTr", "Not a tear"
        assert isinstance(sup, AFTear)

        if "Fixd" in shpe:
            sup.fixed_rounding = shpe["Fixd"]
        if "Ball" in shpe:
            sup.ball = shpe["Ball"]
        if "Curv" in shpe:
            sup.curv = shpe["Curv"]
        if "Bend" in shpe:
            sup.bend = shpe["Bend"]
        if "Tail" in shpe:
            sup.tail = shpe["Tail"]

        return sup

    def _get_svg_path(self):
        p = Path()
        top = (self.tail * 2 - 1) - 1j
        ball = self.ball if self.fixed_rounding else 1
        right = 1 + 1j * (1 - ball)
        left = -right.conjugate()
        side_handle = -(self.curv * (2 - ball)) * 1j
        topright_handle = self.bend * ((1 - self.tail) + (1 - ball) * 1j)
        topleft_handle = self.bend * (0.7 - self.tail + 1j - 0.5j * ball)

        p.append(Move(top))
        if self.bend != 0 or self.curv != 0:
            p.append(Curve(top + topright_handle, right + side_handle, right))
        else:
            p.append(Line(right))

        p.append(Arc(1 + 1j * (ball), 0, False, True, left))
        if self.bend != 0 or self.curv != 0:
            p.append(Curve(left + side_handle, top + topleft_handle, top))
        else:
            p.append(Line(top))

        p.append(ZoneClose())

        return p


class AFCloud(AFCurveShape):
    def __init__(self, b_box: BoundingBox, sides: int = 12, rint: float = 0.85):
        super().__init__(b_box)
        self.rint = rint
        self.sides = sides

    @classmethod
    def from_af(cls, shpe: AFObject, b_box: BoundingBox) -> AFCloud:
        sup = super().from_af(shpe, b_box)
        assert shpe.get_type() == "ShCl", "Not a cloud"
        assert isinstance(sup, AFCloud)

        if "Bubl" in shpe:
            sup.sides = shpe["Bubl"]
        if "IRad" in shpe:
            sup.rint = shpe["IRad"]
        return sup

    def _get_svg_path(self):
        """Cloud is designed around a critical shape. This shape is an n-sided polygon,
        where semi-circles are glued onto the polyon's sides, and the enveloping circle
        is the outer radius of the shape.

        For an inner radius of 1, all handles have the length l1= (4/3)*tan(pi/(2*2*n))
        For an inner radius of -> 0, the inner handle has length l0= tan(pi/8) * 4/3 ~ 0.5522
        which is the handle length of the classical circle approximation (4 segments)

        Trigonometry tells us that the critical shape corresponds
        to an inner radius of
        1 / ((tan(pi / n) + 1) cos(pi / n))
        and the radius of the inscribed semicircles is rin = tan(pi/n) / (tan(pi / n) + 1)
        so they have a handle length of lcrit=4/3 * tan(pi/8) * rin

        If r > cricical radius:
        - the endpoint of inner handles interpolate linearly between the
          critical one and a handle with length l1 perpendicular to the radius
        - the outer handles are always perpendicular to the radius. The length varies
          linearly between the lcrit and l1
        If r < critical radius:
        - angle of the inner handles is pi/n
        - length of the inner handles decreases linearly from l0 to lcrit
        - length of the outer handle increases linearly from 0 to lcrit"""

        outerpts = points_on_ellipse(
            Vector2d(1, 1),
            Vector2d(0, 0),
            self.sides,
        )
        innerpts = points_on_ellipse(
            Vector2d(self.rint, self.rint),
            Vector2d(0, 0),
            self.sides,
            -math.pi / self.sides,
        )
        rthr = 1 / (
            (math.tan(math.pi / self.sides) + 1) * math.cos(math.pi / self.sides)
        )
        lcrit = (
            math.tan(math.pi / self.sides)
            / (math.tan(math.pi / self.sides) + 1)
            * 4
            / 3
            * math.tan(math.pi / 8)
        )
        l0 = 4 / 3 * math.tan(math.pi / 8)
        l1 = 4 / 3 * math.tan(math.pi / (4 * self.sides))

        # Inner handle
        crit_handle_endpoint = (
            -1j * cmath.rect(rthr, -math.pi / self.sides) - lcrit * 1j
        )
        zero_handle_endpoint = -l0 * 1j
        one_handle_endpoint = (-1j + l1) * cmath.rect(1, -math.pi / self.sides)

        if self.rint < rthr:
            inner_handle_endpoint = (
                crit_handle_endpoint - zero_handle_endpoint
            ) * self.rint / rthr + zero_handle_endpoint
        else:
            inner_handle_endpoint = (one_handle_endpoint - crit_handle_endpoint) / (
                1 - rthr
            ) * (self.rint - rthr) + crit_handle_endpoint

        # Outer handle
        if self.rint < rthr:
            outer_handle_endpoint = -1j - lcrit * self.rint / rthr
        else:
            outer_handle_endpoint = -1j - (
                (l1 - lcrit) / (1 - rthr) * (self.rint - rthr) + lcrit
            )

        outer_handle_right = -outer_handle_endpoint.conjugate()
        inner_handle_right = -inner_handle_endpoint.conjugate()

        path = Path([Move(innerpts[0])])

        for i, (op, ip2) in enumerate(zip(outerpts, innerpts[1:] + innerpts[0:1])):
            angle1 = cmath.rect(1, 2 * math.pi / self.sides * i)

            path.append(
                Curve(
                    inner_handle_endpoint * angle1, outer_handle_endpoint * angle1, op
                )
            )
            path.append(
                Curve(outer_handle_right * angle1, inner_handle_right * angle1, ip2)
            )

        path.append(ZoneClose())

        return path


shape_map: Dict[str, Type[AFShape]] = {
    "ShNR": AFRectangle,
    "ShpE": AFEllipse,
    "ShpT": AFTriangle,
    "ShpD": AFDiamond,
    "ShTz": AFTrapezoid,
    "ShPi": AFPie,
    "ShSS": AFSquareStar,
    "ShDS": AFDoubleStar,
    "ShSt": AFStar,
    "ShCr": AFCrescent,
    "ShHt": AFHeart,
    "ShPy": AFPolygon,
    "ShSg": AFSegment,
    "ShTr": AFTear,
    "ShCl": AFCloud,
}


def parse_shape(
    shpe: AFObject,
    b_box: tuple[float, float, float, float],
    scale: Optional[inkex.Transform] = None,
) -> AFShape:
    shape_type = shpe.get_type()
    if shape_type in shape_map:
        bbox = bbox_helper(b_box)
        if scale:
            xy1 = scale.apply_to_point((bbox.left, bbox.top))
            xy2 = scale.apply_to_point((bbox.right, bbox.bottom))
            bbox = BoundingBox((xy1.x, xy2.x), (xy1.y, xy2.y))
        return shape_map[shape_type].from_af(shpe, bbox)

    raise NotImplementedError(f"Shape type {shape_type} not implemented")
