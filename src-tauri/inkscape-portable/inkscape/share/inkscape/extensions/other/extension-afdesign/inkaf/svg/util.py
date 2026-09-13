#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024 Manpreet Singh <manpreet.singh.dev@proton.me>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import math
from typing import Tuple

import inkex

MAX_CLIP_PATH_RECURSION = 1


class ClipPathRecursionError(Exception):
    pass


def interpolate_float(num1: float, num2: float, factor: float) -> float:
    return num1 + (num2 - num1) * factor


def safe_div(num1: float, num2: float, on_fail: float) -> float:
    return num1 / num2 if num2 != 0 else on_fail


def get_scale(tfm: inkex.Transform) -> Tuple[float, float]:
    dt = tfm.a * tfm.d - tfm.b * tfm.c

    if tfm.a or tfm.b:
        r = math.hypot(tfm.a, tfm.b)
        return r, dt / r

    if tfm.c or tfm.d:
        s = math.hypot(tfm.c, tfm.d)
        return dt / s, s

    return 0, 0


def get_scale_tfm(tfm: inkex.Transform) -> inkex.Transform:
    scale_x, scale_y = get_scale(tfm)
    return inkex.Transform((scale_x, 0, 0, scale_y, 0, 0))


def get_scale_tfm_abs(tfm: inkex.Transform) -> inkex.Transform:
    scale_x, scale_y = get_scale(tfm)
    return inkex.Transform((abs(scale_x), 0, 0, abs(scale_y), 0, 0))


def bbox_helper(values: Tuple[float, float, float, float]) -> inkex.BoundingBox:
    """Create a bounding box object from AF-sorted values"""
    return inkex.BoundingBox((values[0], values[2]), (values[1], values[3]))


def get_transformed_bbox(
    bbox: inkex.BoundingBox, tfm: inkex.Transform
) -> inkex.BoundingBox:
    """Get the bounding box of the transformed rectangle defined by `bbox` and `tfm`"""

    pts = [
        inkex.Vector2d(bbox.left, bbox.top),
        inkex.Vector2d(bbox.left, bbox.bottom),
        inkex.Vector2d(bbox.right, bbox.top),
        inkex.Vector2d(bbox.right, bbox.bottom),
    ]
    pts[:] = map(tfm.apply_to_point, pts)

    return inkex.BoundingBox(
        (min(pts, key=lambda p: p.x).x, max(pts, key=lambda p: p.x).x),
        (min(pts, key=lambda p: p.y).y, max(pts, key=lambda p: p.y).y),
    )
