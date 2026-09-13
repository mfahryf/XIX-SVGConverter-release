#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024 Manpreet Singh <manpreet.singh.dev@proton.me>
#
# SPDX-License-Identifier: GPL-2.0-or-later


import math

import inkex


def process(svg: inkex.SvgDocumentElement) -> None:
    for elem in svg:
        if isinstance(elem, inkex.Defs):
            process(elem)
        if not isinstance(elem, inkex.ShapeElement):
            continue
        if isinstance(elem, inkex.elements._groups.GroupBase):
            enhance_group(elem)
            continue
        enhance_shape(elem)


def enhance_group(svg: inkex.elements._groups.GroupBase) -> None:
    group_elements = [e for e in svg]
    keep_group = (
        svg.getparent() is None
        or not isinstance(svg, inkex.Group)
        or any(s not in ("opacity") for s in svg.style)
        or any(a not in ("id", "transform", "style", "clip-path") for a in svg.keys())
        or (
            svg.get("clip-path") is not None
            and any(
                e.get("clip-path") is not None
                for e in group_elements
                if not isinstance(e, inkex.Group)
            )
        )
    )

    for elem in group_elements:
        if not isinstance(elem, inkex.ShapeElement):
            continue

        if not keep_group:
            elem.transform = svg.transform @ elem.transform
            elem.style["opacity"] = float(elem.style("opacity", default=1.0)) * float(
                svg.style.get("opacity", default=1.0)
            )
            if svg.clip is not None and not isinstance(
                elem, inkex.elements._groups.GroupBase
            ):
                elem.clip = svg.clip

        if isinstance(elem, inkex.elements._groups.GroupBase):
            enhance_group(elem)
            continue

        enhance_shape(elem)

    # Remove excessive groups
    if not keep_group:
        for elem in svg:
            svg.remove(elem)
            svg.addprevious(elem)
        svg.getparent().remove(svg)


def enhance_shape(svg: inkex.ShapeElement) -> None:
    # Enhance transforms
    tfm: inkex.Transform = svg.transform
    enhance_tfm(svg)
    baked_tfm = tfm @ -svg.transform
    enhance_gradients(svg, baked_tfm)
    enhance_clip(svg)


def enhance_clip(svg: inkex.ShapeElement) -> None:
    if svg.clip is None:
        return
    assert len(list(svg.clip)) == 1
    if isinstance(list(svg.clip)[0], inkex.Use):
        list(svg.clip)[0].transform = -svg.transform


def enhance_gradients(svg: inkex.ShapeElement, tfm: inkex.Transform) -> None:
    root: inkex.SvgDocumentElement = svg.getroottree().getroot()
    fill = root.getElementById(svg.style.get("fill", None))
    stroke = root.getElementById(svg.style.get("stroke", None))

    for grad in (fill, stroke):
        if not isinstance(grad, inkex.Gradient):
            continue
        gtfm = inkex.Transform(grad.get("gradientTransform"))
        grad.set("gradientTransform", tfm @ gtfm)


def enhance_tfm(svg: inkex.ShapeElement) -> None:
    if isinstance(svg, (inkex.TextElement, inkex.Image, inkex.Use)):
        return

    tfm: inkex.Transform = svg.transform
    svg.transform = inkex.Transform()

    if isinstance(svg, inkex.PathElement):
        if svg.get("inkscape:original-d", None):
            svg.original_path = svg.original_path.transform(tfm, inplace=True)
        # On stars / arcs, we preserve the transforms so that the
        # control points (inkscape:c1 etc.) are also transformed
        if svg.get("sodipodi:type", None) not in ("star", "arc"):
            svg.path = svg.path.transform(tfm, inplace=True)
        else:
            svg.transform = tfm
        return

    if isinstance(svg, inkex.Polygon):
        svg.path = svg.path.transform(tfm, inplace=True)
        return

    # Scaling is handled in the conversion step; this adjusts for uneven stroke width caused by shearing.
    if not (tfm.a or tfm.b) or not (tfm.c or tfm.d):
        svg.transform = tfm
        return

    scale = inkex.Transform(
        (math.hypot(tfm.a, tfm.b), 0, 0, math.hypot(tfm.c, tfm.d), 0, 0)
    )
    svg.transform = inkex.Transform(
        (
            tfm.a / scale.a,
            tfm.b / scale.a,
            tfm.c / scale.d,
            tfm.d / scale.d,
            tfm.e,
            tfm.f,
        )
    )

    if isinstance(svg, (inkex.Rectangle)):
        pos0 = scale.apply_to_point((svg.left, svg.top))
        pos1 = scale.apply_to_point((svg.left + svg.width, svg.top + svg.height))
        svg.set("x", min(pos1.x, pos0.x))
        svg.set("y", min(pos1.y, pos0.y))
        svg.set("width", abs(pos1.x - pos0.x))
        svg.set("height", abs(pos1.y - pos0.y))
        return

    if isinstance(svg, inkex.Ellipse):
        pos0 = scale.apply_to_point((svg.center - svg.radius))
        pos1 = scale.apply_to_point((svg.center + svg.radius))
        svg.radius = (pos1 - pos0) / 2
        svg.center = pos0 + svg.radius
        return

    raise NotImplementedError(f"Enhance Transform not implemented for {type(svg)}")
