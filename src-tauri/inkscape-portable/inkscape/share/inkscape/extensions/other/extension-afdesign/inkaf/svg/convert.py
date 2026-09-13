#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024 Manpreet Singh <manpreet.singh.dev@proton.me>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import base64
import logging
from typing import List, Optional, Tuple
import math

import inkex
import lxml.etree
from inkex.base import SvgOutputMixin
from inkex import PathElement

from inkaf.parser.extract import AFExtractor
from inkaf.svg import util
from inkaf.svg.enhance import process
from inkaf.svg.fill import (
    AFColor,
    AFGradient,
    AFGradientStop,
    AFGradientType,
    parse_fdsc,
)
from inkaf.svg.raster import AFBitmap, AFImage
from inkaf.svg.util import (
    MAX_CLIP_PATH_RECURSION,
    ClipPathRecursionError,
    get_transformed_bbox,
    interpolate_float,
)
from inkaf.utils import DuplicateFilter, parse

from ..parser.types import AFObject, Document, EnumT
from .curve import AFCurve
from .util import bbox_helper
from .shape import (
    AFCornerPos,
    AFCornerType,
    AFCurveShape,
    AFEllipse,
    AFPie,
    AFPointsShapeBase,
    AFRectangle,
    AFShape,
    AFStar,
    corner_map,
    parse_shape,
)
from .styles import (
    AF_BLEND_MODES,
    SVG_BLEND_MODES,
    AFBlendModeV0,
    AFStroke,
    AFStrokeJoin,
    AFStrokeOrder,
    AFStrokeType,
    parse_fill,
    parse_stroke,
    parse_stroke_color,
    parse_transform,
    stroke_cap_map,
    stroke_join_map,
    stroke_order_map,
)
from .text import (
    TEXT_TYPES,
    AFGlyphAtt,
    AFParaAlign,
    AFParaAtt,
    AFText,
    AFTextLineStyle,
    AFTextType,
)
from .adjustment import adj_dict, Adjustment

logger = logging.getLogger(__name__)
dup_filter = DuplicateFilter()
logger.addFilter(dup_filter)


class AFConverter:
    def __init__(self) -> None:
        self.extractor: AFExtractor
        self.afdoc: Document
        self.doc: lxml.etree._ElementTree
        self.document: inkex.SvgDocumentElement
        self._page_label = ""
        self._has_artboards = False

    def convert(self, extractor: AFExtractor, enhance: bool = True) -> None:
        self.extractor = extractor
        self.afdoc = parse(extractor)
        self.doc = SvgOutputMixin.get_template(
            width=self.afdoc["DocR"].get("DfSz", (100, 100))[0],
            height=self.afdoc["DocR"].get("DfSz", (100, 100))[1],
            unit="px",
        )
        self.document = self.doc.getroot()
        root_chlds = self.afdoc["DocR"].get("Chld", [])
        assert len(root_chlds) <= 1
        if root_chlds:
            self._parse_doc(root_chlds[0])

        if "DCMD" in self.afdoc["DocR"] and "FlNm" in self.afdoc["DocR"]["DCMD"]:
            title = self.afdoc["DocR"]["DCMD"]["FlNm"]
            title = (
                title[: -len(".afdesign")] if title.endswith(".afdesign") else title
            ) + ".svg"
            self.document.add(inkex.Title.new(title))

        if enhance:
            process(self.document)

    def _parse_doc(self, child: AFObject) -> None:
        assert child.get_type() == "Sprd", "Not a document"
        assert not self.document.getchildren()
        self.document.set("width", f"{child['SprB'][2]}")
        self.document.set("height", f"{child['SprB'][3]}")
        self.document.set(
            "viewBox",
            f"{child['SprB'][0]} {child['SprB'][1]} {child['SprB'][2]} {child['SprB'][3]}",
        )

        self._parse_children(child.get("Chld", []))
        self._add_guides(child)

        if self._has_artboards and self._page_label:
            self.document.namedview.get_pages()[0].label = self._page_label

    def _add_guides(
        self, child: AFObject, page_offset: inkex.Vector2d = inkex.Vector2d()
    ) -> None:
        v_guides: list[float] = child.get("GdsV", [])
        h_guides: list[float] = child.get("GdsH", [])

        for pos in v_guides:
            self.document.namedview.add_guide(pos + page_offset.x, orient=False)

        for pos in h_guides:
            self.document.namedview.add_guide(pos + page_offset.y, orient=True)

        # guide color and opacity
        guide_color_af = self.afdoc["DocR"].get("GFil")
        if guide_color_af is None:
            return

        guide_color = parse_fdsc(guide_color_af)
        self.document.namedview.set("guidecolor", self._convert_fill(guide_color))
        self.document.namedview.set(
            "guideopacity",
            (guide_color.alpha if isinstance(guide_color, AFColor) else 1.0),
        )

    def _add_artboard(self, child: AFObject) -> None:
        assert child.get_type() in ["ShpN", "PCrv"], "Invalid artboard type"

        bbox = bbox_helper(child["ShpB" if child.get_type() == "ShpN" else "CvsB"])
        if "Xfrm" in child:
            bbox = get_transformed_bbox(bbox, parse_transform(child["Xfrm"]))

        vx, vy, _, _ = self.document.get_viewbox() or [0, 0, 0, 0]
        self._add_guides(child, inkex.Vector2d(bbox.left - vx, bbox.top - vy))

        if not self._has_artboards:
            self.document.set(
                "viewBox", f"{bbox.left} {bbox.top} {bbox.width} {bbox.height}"
            )
            self.document.set("width", bbox.width)
            self.document.set("height", bbox.height)

            self._page_label = child.get("Desc", "Artboard1")
            self._has_artboards = True
            return

        self.document.namedview.new_page(
            f"{bbox.left - vx}",
            f"{bbox.top - vy}",
            f"{bbox.width}",
            f"{bbox.height}",
            label=child.get(
                "Desc", f"Artboard{len(self.document.namedview._get_pages())}"
            ),
        )

    def _parse_children(
        self,
        children: list[AFObject],
        parent: Optional[inkex.Group] = None,
        parent_af: Optional[AFObject] = None,
        parent_tfm: inkex.Transform = inkex.Transform(),
    ) -> None:
        if parent is None:
            parent = self.document
        assert parent is not None

        for child in children:
            self._add_child(child, parent, parent_af, parent_tfm, False)

    def _add_path_effect(
        self,
        element: inkex.ShapeElement,
        path_effect: inkex.PathEffect,
    ) -> None:
        self.document.defs.add(path_effect)
        path_effect_str = element.get("inkscape:path-effect", "")
        if path_effect_str:
            element.set(
                "inkscape:path-effect", f"{path_effect_str},{path_effect.get_id(1)}"
            )
        else:
            element.set("inkscape:path-effect", f"{path_effect.get_id(1)}")

    def _add_clip(
        self, child: inkex.ShapeElement, parent: inkex.ShapeElement, href: bool = True
    ) -> None:
        assert not isinstance(parent, inkex.elements._groups.GroupBase), (
            f"{parent} of type {type(parent)} is a group"
        )
        assert isinstance(parent, inkex.ShapeElement), (
            f"{parent} of type {type(parent)} is not a ShapeElement"
        )

        if href:
            use = inkex.Use()
            use.href = parent
            use.transform = -child.transform
        else:
            use = parent

        clip = inkex.ClipPath()
        self.document.defs.add(clip)
        clip.add(use)

        child_clip = child
        for i in range(MAX_CLIP_PATH_RECURSION):
            if child_clip.clip is None:
                clip.set("id", f"clip-path{i}-{child.get_id()}")
                child_clip.clip = clip
                return
            assert isinstance(child_clip.clip, inkex.ClipPath)
            child_clip = child_clip.clip

        raise ClipPathRecursionError("Too many nested clip paths")

    def _add_masks(
        self, svg: inkex.ShapeElement, masks: List[AFObject], tfm: inkex.Transform
    ) -> None:
        def fill_pop(element: inkex.ShapeElement) -> None:
            if isinstance(element, inkex.elements._groups.GroupBase):
                for child in element:
                    fill_pop(child)
                return
            element.style.pop("fill")
            element.style.pop("stroke")

        if not masks:
            return

        masks_svg = self.document.defs.add(inkex.Mask(id=f"mask-{svg.get_id()}"))
        curr_mask_svg = masks_svg
        prev_ele: Optional[inkex.ShapeElement] = None
        curr_ele: Optional[inkex.ShapeElement] = None

        for mask_af in masks:
            if mask_af.get_type() in adj_dict:
                try:
                    adj = Adjustment.from_af(mask_af)
                    svg.style("filter").append(adj.to_svg_filter())
                except Exception as e:
                    logger.error(
                        f"Failed to apply adjustment: {e}",
                        exc_info=logger.isEnabledFor(logging.INFO),
                    )

                continue

            if prev_ele is not None:
                curr_mask_svg = self.document.defs.add(
                    inkex.Mask(id=f"mask-{prev_ele.get_id()}")
                )
                prev_ele.set("mask", curr_mask_svg.get_id(2))
            try:
                self._add_child(mask_af, curr_mask_svg, no_catch=True)
            except Exception as e:
                logger.error(
                    f"Failed to parse mask: {e}",
                    exc_info=logger.isEnabledFor(logging.INFO),
                )
                if prev_ele is not None:
                    prev_ele.set("mask", None)
                continue

            curr_ele = list(curr_mask_svg)[-1]
            assert curr_ele is not None
            curr_ele.transform @= tfm
            tfm = tfm @ -curr_ele.transform
            fill_pop(curr_ele)
            curr_ele.style["fill"] = "#ffffff"
            curr_ele.style["stroke"] = "#ffffff"

            prev_ele = curr_ele

        if curr_ele is not None:
            svg.set("mask", masks_svg.get_id(2))

    def _parse_child(self, child: AFObject, tfm: inkex.Transform) -> inkex.ShapeElement:
        # Sprd -> Document
        if child.get_type() == "Sprd":
            raise RuntimeError("Document not at root")

        # ShpN -> Shape
        if child.get_type() == "ShpN":
            scale = util.get_scale_tfm_abs(tfm)
            return self._convert_shape(parse_shape(child["Shpe"], child["ShpB"], scale))

        # PCrv -> Curve
        elif child.get_type() == "PCrv":
            return PathElement.new(
                AFCurve.from_af(child["Crvs"])
                .get_svg_path()
                .transform(tfm, inplace=True)
            )

        # ImgN -> Image
        elif child.get_type() in ("ImgN", "Rstr"):
            img = self._convert_image(AFImage.from_af(child))
            bbox = util.get_transformed_bbox(
                inkex.BoundingBox(
                    (img.left, img.left + img.width), (img.top, img.top + img.height)
                ),
                util.get_scale_tfm_abs(tfm),
            )
            img.set("x", bbox.left)
            img.set("y", bbox.top)
            img.set("width", bbox.width)
            img.set("height", bbox.height)
            return img

        # Scop -> Layer
        elif child.get_type() == "Scop":
            return inkex.Layer()  # TODO: Parse layer properties

        # Grup -> Group
        elif child.get_type() == "Grup":
            return inkex.Group()  # TODO: Parse group properties

        # TxtA -> Text
        elif child.get_type() in TEXT_TYPES:
            return self._convert_text(AFText.from_af(child), tfm)

        else:
            raise NotImplementedError(
                f'Object type "{child.get_type()}" not implemented'
            )

    def _add_child(
        self,
        child: AFObject,
        parent: inkex.Group,
        parent_af: Optional[AFObject] = None,
        parent_tfm: inkex.Transform = inkex.Transform(),
        no_catch: bool = True,
    ) -> None:
        # parent svg object converted from parent_af
        parent_object: Optional[inkex.ShapeElement] = (
            parent
            if parent_af is not None and parent_af.get_type() in ["Scop", "Grup"]
            else parent.getchildren()[0]
            if parent_af is not None and parent.getchildren()
            else None
        )
        tfm = self._combine_tfm(
            self._get_xfrm(child) @ self._get_ftxs(child), parent_tfm
        )
        conversion_failed = True

        style_cascade = [child]
        symbol = False
        try:
            if child.get("ABEn", False):
                self._add_artboard(child)
            try:
                svg = self._parse_child(child, tfm)
            except KeyError:
                # Most likely we have encountered as symbol
                if "SLnk" in child:
                    child = child["SLnk"]["ILOb"][1]
                # Sometimes the symbol wraps the actual object again
                # It appears the properties are grouped
                # into "Master" categories, and we can look them up using them
                # Likely properties end with "Ma" or "M".
                # e.g. StMa, VisM, FiEM, EdMa, HKMs, TMas, TWMa, BGMs, VBFM, VLSM, VTRM
                # These are only relvant when the symbols goes out of sync
                # with the master symbol
                # For most objects the properties are hidden behing BlMM, TxFM is for text
                if "TxFM" in child:
                    child = child["TxFM"]
                elif "BlMM" in child:
                    child = child["BlMM"]
                svg = self._parse_child(child, inkex.Transform())

                style_cascade.insert(0, child)
                symbol = True
        except NotImplementedError as e:
            if no_catch:
                raise e
            logger.error(
                f"Not implemented: {e}", exc_info=logger.isEnabledFor(logging.INFO)
            )
            svg = inkex.Layer.new(label=f"-Failed-{child.get_type()}-")
        except Exception as e:
            if no_catch:
                raise e
            if isinstance(e, KeyError):
                logger.error(
                    f"Expected key missing on {child.get_type()}. Most likely a symbol"
                )
            else:
                logger.exception(f"Failed to convert object type {child.get_type()}")
            svg = inkex.Layer.new(label=f"-Failed-{child.get_type()}-")
        else:
            conversion_failed = False

        if child.get_type() in ["Scop", "Grup"]:
            layer = parent.add(svg)
        else:
            layer = parent.add(inkex.Group())
            layer.add(svg)

        try:
            self._add_common_properties(
                child, svg, parent_tfm, conversion_failed or symbol
            )
            for i, child in enumerate(style_cascade):
                self._add_layer_style(layer, child)
                self._add_style(svg, child, tfm)
                self._add_fx(layer, child)
                self._set_blend_mode(layer, child)
        except NotImplementedError as e:
            logger.error(
                f"Not implemented: {e}", exc_info=logger.isEnabledFor(logging.INFO)
            )
        except Exception:
            logger.exception(f"Failed to set object properties {child.get_type()}")

        if parent_object is not None and not isinstance(parent_object, inkex.Group):
            self._add_clip(layer, parent_object)

        # Vector Masks
        try:
            self._add_masks(layer, child.get("AdCh", []), tfm)
        except Exception:
            logger.exception(f"Failed to add vector masks to #{svg.get_id()}")

        self._parse_children(child.get("Chld", []), layer, child, tfm)

    def _convert_shape(self, child: AFShape) -> inkex.ShapeElement:
        if isinstance(child, AFPointsShapeBase):
            return inkex.Polygon.new(
                points=" ".join([f"{p.x},{p.y}" for p in child.points])
            )
        if isinstance(child, AFRectangle):
            return self._convert_rectangle(child)
        if isinstance(child, AFEllipse):
            return inkex.Ellipse.new(center=child.b_box.center, radius=child.radii)
        if isinstance(child, AFPie):
            if math.isclose(child.start_angle, child.end_angle, abs_tol=1.75e-5):
                child.start_angle = 0
                child.end_angle = math.pi * 2
            outer = PathElement.arc(
                child.b_box.center,
                child.radii.x,
                child.radii.y,
                arctype="slice",
                start=-child.end_angle,
                end=-child.start_angle,
            )
            if not child.inner_radius:
                return outer
            inner = PathElement.arc(
                child.b_box.center,
                child.radii.x * child.inner_radius,
                child.radii.y * child.inner_radius,
                arctype="slice",
                start=-child.end_angle,
                end=-child.start_angle,
            )
            p1 = outer.path.to_absolute()
            p2 = inner.path.reverse().to_absolute()

            if child.start_angle == 0 and child.end_angle == math.pi * 2:
                p = p1 + p2
            else:
                p = (
                    [inkex.paths.Move(*p1[-3].args[-2:])]
                    + [inkex.paths.Line(*p2[2].args)]
                    + p2[3:-1]  # inner arc
                    + [inkex.paths.Line(*p1[0].args)]
                    + p1[1:-2]  # outer arc
                )
            return PathElement.new(p)
        if isinstance(child, AFStar):
            if child.radii.x == child.radii.y and (
                (child.curved_edges and not child.left_curve and not child.right_curve)
                or (
                    not child.curved_edges
                    and not child.inner_circle
                    and not child.outer_circle
                )
            ):
                return PathElement.star(
                    child.b_box.center,
                    (child.radii.x, child.inner_radius_clipped * child.radii.x),
                    child.num_points,
                    args=(
                        -math.pi / 2,
                        -math.pi / 2 + math.pi / child.num_points,
                    ),
                )

        if isinstance(child, AFCurveShape):
            return PathElement.new(child.get_svg_path())

        raise NotImplementedError(f"Conversion for shape {type(child)} not implemented")

    def _convert_rectangle(self, child: AFRectangle) -> inkex.ShapeElement:
        b_box, radii = child.b_box, child.radii

        if any(ct not in corner_map for ct in child.corner_types):
            path_commands = []
            path_commands.append(("M", [b_box.right, b_box.top + radii[1]]))
            path_commands.append(("L", [b_box.right, b_box.bottom - radii[2]]))
            path_commands.extend(child._corner_path(AFCornerPos.BR))
            path_commands.append(("L", [b_box.left + radii[3], b_box.bottom]))
            path_commands.extend(child._corner_path(AFCornerPos.BL))
            path_commands.append(("L", [b_box.left, b_box.top + radii[0]]))
            path_commands.extend(child._corner_path(AFCornerPos.TL))
            path_commands.append(("L", [b_box.right - radii[1], b_box.top]))
            path_commands.extend(child._corner_path(AFCornerPos.TR))
            path_commands.append(("Z", []))
            return PathElement.new(path_commands)

        rect = inkex.Rectangle.new(b_box.left, b_box.top, b_box.width, b_box.height)
        if all(
            r == radii[0] and ct == AFCornerType.ROUNDED
            for r, ct in zip(radii, child.corner_types)
        ) or all(r == 0 for r in radii):
            rect.set("rx", radii[0])
            return rect

        self._add_path_effect(rect, child._corner_path_effect())
        return rect

    def _convert_text(self, text: AFText, tfm: inkex.Transform) -> inkex.TextElement:
        tspans = self._convert_tspans(text, tfm)

        if text.type in [AFTextType.FOLLOW_CURVE, AFTextType.FOLLOW_CURVE2]:
            text_ele = self._convert_text_on_curve(text, tspans)
        else:
            text_ele = self._convert_text_in_shape(text, tspans)

        text_ele.style["white-space"] = "pre"
        text_ele.set("xml:space", "preserve")
        text_ele.style["font-size"] = 0
        text_ele.style["line-height"] = 0
        return text_ele

    def _convert_tspans(self, text: AFText, tfm: inkex.Transform) -> List[inkex.Tspan]:
        para_tspans = []

        for glyph, glyph_atts, para_atts in zip(
            text.glyphs, text.glyph_atts, text.para_atts
        ):
            paras: List[str] = [para + "\n" for para in glyph.split("\u2029")]
            curr_patt_idx = 0
            curr_gatt_idx = 0
            pos_para_offset = 0
            pos_prev_gatt = 0

            for para_str in paras:
                pspan = inkex.Tspan()
                para_tspans.append(pspan)

                max_para_font_size = 0.0
                for glyph_att in glyph_atts.runs[curr_gatt_idx:]:
                    glyph_str = para_str[
                        pos_prev_gatt - pos_para_offset : glyph_att.index
                        - pos_para_offset
                    ]
                    pos_prev_gatt += len(glyph_str)

                    gspan = inkex.Tspan.new(glyph_str.replace("\u0000", ""))
                    pspan.add(gspan)
                    self._add_glyph_style(gspan, glyph_att, tfm)

                    max_para_font_size = max(glyph_att.font_size, max_para_font_size)

                    # End of glyph_att
                    if glyph_att.index <= pos_para_offset + len(para_str):
                        curr_gatt_idx += 1

                    # End of current paragraph (reached \u2029)
                    if glyph_att.index >= pos_para_offset + len(para_str):
                        break

                para_att = para_atts.runs[curr_patt_idx]
                self._add_para_style(
                    pspan,
                    para_att,
                    max_para_font_size,
                )

                # Adjust offests
                pos_para_offset += len(para_str)

                # End of current paragraph_att
                if pos_para_offset >= para_att.index:
                    curr_patt_idx += 1

        return para_tspans

    def _convert_text_on_curve(
        self, text: AFText, tspans: List[inkex.Tspan]
    ) -> inkex.TextElement:
        assert text.text_path is not None
        text_ele = inkex.TextElement.new()
        line = text_ele.add(inkex.TextPath.new(*tspans))
        path = PathElement.new(text.text_path.path.get_svg_path())

        self.document.defs.add(path)
        line.href = path
        line.set("startOffset", f"{text.text_path.starts[0] * 100}%")
        line.set("side", "right" if text.text_path.reverses[0] else "left")
        return text_ele

    def _convert_text_in_shape(
        self,
        text: AFText,
        tspans: List[inkex.Tspan],
    ) -> inkex.TextElement:
        def _convert_shape_inside(
            shape: AFShape | AFCurve, offs: Tuple[float, float], text_type: AFTextType
        ) -> inkex.ShapeElement:
            if text_type != AFTextType.ARTISTIC:
                if isinstance(shape, AFCurve):
                    return PathElement.new(shape.get_svg_path())
                return self._convert_shape(shape)

            assert isinstance(shape, AFRectangle)
            bbox = shape.b_box
            x1, y1, x2, y2 = (
                bbox.left,
                bbox.top,
                bbox.right + offs[0],
                bbox.bottom + offs[1],
            )
            return self._convert_shape(
                AFRectangle(inkex.BoundingBox((x1, x2), (y1, y2)))
            )

        text_ele = inkex.TextElement.new()
        line = text_ele.add(inkex.Tspan.new(*tspans))
        line.set("sodipodi:role", "line")

        bottom_offset = 0
        right_offset = 0

        if tspans:
            top_offset_factor = 0.65
            line1_font_size = max(
                [t.style("font-size") for t in list(tspans[0])] or [0]
            )
            line1_height = (
                float(tspans[0].style("line-height")[:-2]) * top_offset_factor
            )
            tspans[0].style["line-height"] = f"{line1_height}pt"

            bottom_offset = max(line1_height, line1_font_size)
            right_offset = line1_font_size

        shape_inside = _convert_shape_inside(
            text.shape_inside, (right_offset, bottom_offset), text.type
        )
        self.document.defs.add(shape_inside)
        text_ele.style["shape-inside"] = shape_inside
        return text_ele

    def _add_glyph_style(
        self, tspan: inkex.Tspan, glyph_att: AFGlyphAtt, tfm: inkex.Transform
    ) -> None:
        # Fill and stroke
        self._apply_color(tspan, True, glyph_att.fill)
        self._apply_color(tspan, False, glyph_att.stroke_fill)
        self._apply_stroke_style(tspan, glyph_att.stroke, tfm)

        # Font
        tspan.style["font-size"] = glyph_att.font_size
        tspan.style["font-family"] = glyph_att.font.family
        tspan.style["font-weight"] = glyph_att.font.weight
        tspan.style["font-style"] = "italic" if glyph_att.font.italic else "normal"
        tspan.style["font-stretch"] = glyph_att.font.stretch.name.lower()

        if glyph_att.lang:
            tspan.style["lang"] = glyph_att.lang

        if glyph_att.underline != AFTextLineStyle.NONE:
            tspan.style["text-decoration-line"] = "underline"
            tspan.style["text-decoration-style"] = (
                "solid" if glyph_att.underline == AFTextLineStyle.SINGLE else "double"
            )
            if glyph_att.underline_fill:
                tspan.style["text-decoration-fill"] = self._convert_fill(
                    glyph_att.underline_fill
                )

        if glyph_att.line_through != AFTextLineStyle.NONE:
            tspan.style["text-decoration-line"] = "line-through"
            tspan.style["text-decoration-style"] = (
                "solid"
                if glyph_att.line_through == AFTextLineStyle.SINGLE
                else "double"
            )
            if glyph_att.line_through_fill:
                tspan.style["text-decoration-fill"] = self._convert_fill(
                    glyph_att.line_through_fill
                )

    def _add_para_style(
        self, tspan: inkex.Tspan, para_att: AFParaAtt, para_font_size: float
    ) -> None:
        tspan.style["line-height"] = (
            f"{para_att.para_leading.get_line_height(para_font_size)}pt"
        )

        # TODO: Doesn't work
        if para_att.align == AFParaAlign.LEFT:
            tspan.style["text-anchor"] = "start"
            tspan.style["text-align"] = "start"
        elif para_att.align == AFParaAlign.CENTER:
            tspan.style["text-anchor"] = "middle"
            tspan.style["text-align"] = "middle"
        elif para_att.align == AFParaAlign.RIGHT:
            tspan.style["text-anchor"] = "end"
            tspan.style["text-align"] = "end"
        elif para_att.align == AFParaAlign.JUSTIFY_LEFT:
            tspan.style["text-align"] = "justify"
            tspan.style["text-anchor"] = "start"
        elif para_att.align == AFParaAlign.JUSTIFY_CENTER:
            tspan.style["text-align"] = "justify"
            tspan.style["text-anchor"] = "middle"
        elif para_att.align == AFParaAlign.JUSTIFY_RIGHT:
            tspan.style["text-align"] = "justify"
            tspan.style["text-anchor"] = "end"
        elif para_att.align == AFParaAlign.JUSTIFY_ALL:
            tspan.style["text-align"] = "justify"
            tspan.style["text-anchor"] = "middle"
        elif para_att.align in [
            AFParaAlign.TOWARDS_SPLINE,
            AFParaAlign.AWAY_FROM_SPLINE,
        ]:
            raise NotImplementedError(f"Alignment {para_att.align} not implemented")
        else:
            raise NotImplementedError(f"Unknown alignment: {para_att.align}")

    def _convert_image(self, child: AFImage) -> inkex.ShapeElement:
        try:
            if isinstance(child.bitmap.emb_file, str) and child.media_type:
                return self._convert_bitm(child.bitmap, child.media_type)
            elif isinstance(child.bitmap.emb_file, AFObject):
                return self._convert_bitm(child.bitmap)

        except (AssertionError, KeyError, RuntimeError, TypeError):
            logger.warning(
                "Could not load embedded image " + child.bitmap.emb_file
                if isinstance(child.bitmap.emb_file, str)
                else "",
                # exc_info=True,
            )

        path = child.file_path_rel or child.file_path_abs
        if path:
            img = inkex.Image.new(
                x=0, y=0, width=child.bitmap.width, height=child.bitmap.height
            )
            prefix = "file:///" if child.file_path_abs == path else ""
            path = path.replace("\\", "/")
            img.set("xlink:href", f"{prefix}{path}")
            return img

        raise RuntimeError(
            f"Could not load image '{child.bitmap.emb_file if isinstance(child.bitmap.emb_file, str) else ''}'. No relative or absolute path found."
        )

    def _convert_bitm(
        self, child: AFBitmap, mtype: Optional[str] = None
    ) -> inkex.ShapeElement:
        img = inkex.Image.new(x=0, y=0, width=child.width, height=child.height)

        if child.emb_file is not None:
            if isinstance(child.emb_file, str):
                data = self._get_embedded_image_data(child.emb_file)
            elif isinstance(child.emb_file, AFObject):
                mtype = "image/png"
                data = base64.b64encode(
                    AFBitmap.bitmap_from_blocks(child.emb_file, self.extractor)
                ).decode("utf-8")
            img.set(
                "xlink:href",
                f"data:{mtype};base64,{data}",
            )
            return img
        raise NotImplementedError("Bitmap type not supported")

    def _get_embedded_image_data(self, path: str) -> str:
        img_doc = parse(self.extractor, path)
        assert img_doc["Size"] > 0 and len(img_doc["Data"]) == img_doc["Size"], (
            "Invalid embedded image data"
        )
        return base64.b64encode(img_doc["Data"]).decode("utf-8")

    def _add_layer_style(self, svg: inkex.ShapeElement, child: AFObject) -> None:
        # Visibility and Opacity
        # Opac -> layer opacity, FOpc -> filter opacity
        if not child.get("Visi", True):
            svg.style["display"] = "none"
        svg.style["opacity"] = child.get("Opac", 1.0) * child.get("FOpc", 1.0)

    def _add_style(
        self, svg: inkex.ShapeElement, child: AFObject, tfm: inkex.Transform
    ) -> None:
        def adjust_gradient_tfm(grad: AFGradient) -> None:
            """
            Adjusts the gradient transform to account for transformations baked into the
            objects during conversion.
            """
            if child.get_type() in ["ShpN", "ImgN"]:
                grad.tfm = util.get_scale_tfm_abs(tfm) @ grad.tfm
            elif child.get_type() in ["PCrv"]:
                grad.tfm = tfm @ grad.tfm

        fill = parse_fill(child)
        if child.get_type() == "ImgN":
            if fill is not None:
                # TODO: Allow changing image fill
                pass
        else:
            if isinstance(fill, AFGradient):
                adjust_gradient_tfm(fill)
            if fill is not None:
                self._apply_color(svg, True, fill)
            else:
                if "fill" not in svg.style:
                    svg.style["fill"] = None
                    svg.style["fill-opacity"] = 1

        # Stroke
        stroke_fill = parse_stroke_color(child)
        if isinstance(stroke_fill, AFGradient):
            adjust_gradient_tfm(stroke_fill)
        if stroke_fill is not None:
            self._apply_color(svg, False, stroke_fill)

        stroke = parse_stroke(child)
        if stroke is not None:
            strokecopy = None
            if child.get_type() == "ImgN" and stroke.style != AFStrokeType.NONE:
                # Split image and stroke
                strokecopy = inkex.Rectangle.new(
                    svg.get("x"), svg.get("y"), svg.get("width"), svg.get("height")
                )
                strokecopy.transform = svg.transform
                if svg.clip is not None:
                    strokecopy.clip = svg.clip
                strokecopy.style["fill"] = None
                strokecopy.style["stroke"] = svg.style("stroke")
                strokecopy.style["stroke-opacity"] = svg.style("stroke-opacity")
                self._apply_stroke_style(strokecopy, stroke, tfm)
            else:
                self._apply_stroke_style(svg, stroke, tfm)

            power_stroke = stroke.get_power_stroke(
                strokecopy if strokecopy is not None else svg
            )
            if power_stroke is not None:
                if strokecopy is None:
                    if svg.style("fill") is not None:
                        # Split fill and stroke
                        strokecopy = svg.copy()
                    else:
                        strokecopy = svg
                strokecopy.style["fill"] = strokecopy.style["stroke"]
                strokecopy.style["stroke"] = None
                strokecopy.style["fill-opacity"] = strokecopy.style["stroke-opacity"]
                strokecopy.style.pop("stroke-opacity")
                self._add_path_effect(strokecopy, power_stroke)

            if strokecopy is not None:
                svg.style["stroke"] = None
                svg.pop("stroke-opacity")

                if stroke.order == AFStrokeOrder.FRONT:
                    svg.addnext(strokecopy)
                else:
                    svg.addprevious(strokecopy)

        else:
            if "stroke" not in svg.style:
                svg.style["stroke"] = None
                svg.style["stroke-opacity"] = 1

    def _set_blend_mode(self, svg: inkex.ShapeElement, child: AFObject) -> None:
        blend_enum = child.get("Blnd")
        if blend_enum is None:
            return
        assert isinstance(blend_enum, EnumT)

        mode = AF_BLEND_MODES[blend_enum.version](blend_enum.id)
        if mode == AFBlendModeV0.NORMAL:
            return

        if mode.name.lower().replace("_", "-") in SVG_BLEND_MODES:
            svg.style["mix-blend-mode"] = mode.name.lower().replace("_", "-")
            return

        raise NotImplementedError(f"Blend mode {mode.name} not supported")

    def _add_filter(self, svg: inkex.ShapeElement, filt: inkex.Filter) -> None:
        filters = svg.style.get("filter", "")
        svg.style["filter"] = " ".join([filters, f"url(#{filt.get_id()})"])

    def _add_fx(self, svg: inkex.ShapeElement, child: AFObject) -> None:
        for fx in child.get("FiEf", []):
            if not fx.get("Enab", False):
                continue

            filt: inkex.Filter = inkex.Filter()
            self.document.defs.add(filt)
            filt.set("color-interpolation-filters", "sRGB")

            if fx.get_type() == "Gaus":
                radius: float = fx["Radi"] / 3.0
                preserve_alpha: bool = fx["PrAl"]

                filt.add(
                    inkex.Filter.GaussianBlur.new(stdDeviation=radius, result="blur")
                )

                if preserve_alpha:
                    cpt = filt.add(
                        inkex.Filter.ColorMatrix.new(
                            values="1 0 0 0 0 0 1 0 0 0 0 0 1 0 0 0 0 0 100 0",
                            result="blurOpaque",
                        )
                    )
                    cpt.set("in", "blur")

                    composite = filt.add(inkex.Filter.Composite.new(operator="atop"))
                    composite.set("in", "blurOpaque")
                    composite.set("in2", "SourceAlpha")

            else:
                raise NotImplementedError(
                    f"Filter type {fx.get_type()} not implemented"
                )

            self._add_filter(svg, filt)

    def _apply_stroke_style(
        self, svg: inkex.ShapeElement, stroke: AFStroke, tfm: inkex.Transform
    ) -> None:
        # return if stroke type or fill is none
        if stroke.style == AFStrokeType.NONE or svg.style("stroke") is None:
            svg.style["stroke"] = None
            svg.style["stroke-opacity"] = 1
            return

        scale_tfm = util.get_scale_tfm_abs(tfm)
        stroke_width = (
            (stroke.width * (scale_tfm.a + scale_tfm.d) / 2)
            if stroke.scale_with_object
            else stroke.width
        )
        svg.style["stroke-width"] = stroke_width

        if stroke.style == AFStrokeType.DASHED:
            pattern = list(stroke.dash_pattern)
            while pattern[-1] == 0:
                del pattern[-1]
            svg.style["stroke-dasharray"] = [
                ((d if d > 0 else 0.05) * stroke_width) for d in pattern
            ]

        elif stroke.style != AFStrokeType.SOLID:
            raise NotImplementedError(f"Stroke style {stroke.style} not implemented")

        svg.style["stroke-linecap"] = stroke_cap_map[stroke.cap]
        svg.style["paint-order"] = stroke_order_map[stroke.order]

        svg.style["stroke-linejoin"] = stroke_join_map[stroke.join]
        if stroke.join == AFStrokeJoin.MITER:
            svg.style["stroke-miterlimit"] = stroke.miter_clip

    def _apply_color(
        self,
        svg: inkex.ShapeElement,
        fill: bool,
        stroke_fill: Optional[AFColor | AFGradient],
    ):
        prefix = "fill" if fill else "stroke"
        svg.style[prefix] = self._convert_fill(stroke_fill)
        svg.style[f"{prefix}-opacity"] = (
            stroke_fill.alpha if isinstance(stroke_fill, AFColor) else 1.0
        )

    def _convert_fill(self, fill: Optional[AFColor | AFGradient]) -> Optional[str]:
        if fill is None:
            return None
        if isinstance(fill, AFColor):
            return self._convert_color(fill)

        gradient_svg = self._convert_gradient(fill)
        self.document.defs.add(gradient_svg)
        return f"url(#{gradient_svg.get_id()})"

    @staticmethod
    def _convert_gradient(grad: AFGradient) -> inkex.Gradient:
        if grad.grad_type == AFGradientType.LINEAR:
            gradient = inkex.LinearGradient()
            gradient.set("x1", 0)
            gradient.set("y1", 0)
            gradient.set("x2", 1)
            gradient.set("y2", 0)

        elif grad.grad_type in [AFGradientType.RADIAL, AFGradientType.ELLIPTICAL]:
            gradient = inkex.RadialGradient()
            gradient.set("cx", 0)
            gradient.set("cy", 0)
            gradient.set("r", 1)

        else:
            raise NotImplementedError(
                f'Gradient type "{grad.grad_type}" not implemented'
            )

        gradient.set("gradientTransform", grad.tfm)
        gradient.set("gradientUnits", "userSpaceOnUse")

        for stop in AFConverter._convert_stops(grad.stops):
            gradient.add(stop)

        return gradient

    @staticmethod
    def _convert_stops(stops: List[AFGradientStop]) -> List[inkex.Stop]:
        assert len(stops) >= 2, "At least two stops required"
        res = []
        prev_stop = None
        for stop in stops:
            if prev_stop is not None:
                s1, s2 = AFConverter._convert_stop(prev_stop, next_stop=stop)
                res.extend([s1, s2])
            prev_stop = stop
        res.extend(AFConverter._convert_stop(stops[-1]))
        return res

    @staticmethod
    def _convert_stop(
        self, next_stop: AFGradientStop | None = None
    ) -> List[inkex.Stop]:
        def _make_stop(offset: float, color: AFColor) -> inkex.Stop:
            stop = inkex.Stop()
            stop.set("offset", offset)
            stop.style = inkex.Style(
                {
                    "stop-color": AFConverter._convert_color(color),
                    "stop-opacity": color.alpha,
                }
            )
            return stop

        stops = []
        stops.append(_make_stop(self.pos, self.color))
        if next_stop is None:
            return stops

        stops.append(
            _make_stop(
                interpolate_float(self.pos, next_stop.pos, self.mid),
                self.color.interpolate(next_stop.color, 0.5),
            )
        )
        return stops

    @staticmethod
    def _convert_color(color: AFColor) -> str:
        return "#{0:02x}{1:02x}{2:02x}".format(
            *[
                round(c * 255) if isinstance(c, float) else int(c)
                for c in color.to_rgb().color
            ]
        )

    @staticmethod
    def _get_xfrm(child: AFObject) -> inkex.Transform:
        """
        Get the transformation (Xfrm) from the AFObject, if present.

        For text with `shape-inside`, the scale part of Xfrm is applied to the shape.
        The scale for the text itself is present in FTxS.
        """
        return parse_transform(child["Xfrm"]) if "Xfrm" in child else inkex.Transform()

    @staticmethod
    def _get_ftxs(child: AFObject) -> inkex.Transform:
        """
        Get the Frame Text Scale (FTxS) transformation from the AFObject, if present.

        Contains the scale transformation for text objects having `shape-inside`.
        """
        return (
            parse_transform(child["FTxS"])
            if child.get_type() in TEXT_TYPES and "FTxS" in child
            else inkex.Transform()
        )

    @classmethod
    def _add_common_properties(
        cls,
        child: AFObject,
        svg: inkex.ShapeElement,
        parent_tfm: Optional[inkex.Transform] = None,
        conversion_failed: bool = False,
    ) -> None:
        if "Desc" in child and child["Desc"]:
            svg.label = child["Desc"]

        # Xfrm, FTxS -> Transform
        tfm = cls._combine_tfm(cls._get_xfrm(child), parent_tfm or inkex.Transform())

        if conversion_failed and child.get_type() not in ["Scop", "Grup"]:
            svg.transform @= tfm
            return

        # Remove the transformation already baked into the object
        if child.get_type() in TEXT_TYPES - {AFTextType.ARTISTIC.value}:
            shape = svg.style("shape-inside")
            if shape is None:
                shape = svg.getchildren()[0].href
            shape.transform = util.get_scale_tfm_abs(tfm) @ -cls._get_ftxs(child)
            tfm = tfm @ -util.get_scale_tfm_abs(tfm) @ cls._get_ftxs(child)

        elif child.get_type() in ["ShpN", "ImgN"]:
            scale = util.get_scale_tfm_abs(tfm)
            tfm = inkex.Transform(
                (
                    tfm.a / scale.a,
                    tfm.b / scale.a,
                    tfm.c / scale.d,
                    tfm.d / scale.d,
                    tfm.e,
                    tfm.f,
                )
            )

        elif child.get_type() in ["PCrv", "Scop", "Grup"]:
            tfm = inkex.Transform()

        svg.transform @= tfm

    @staticmethod
    def _combine_tfm(
        tfm: inkex.Transform, parent_tfm: inkex.Transform
    ) -> inkex.Transform:
        return parent_tfm @ tfm
