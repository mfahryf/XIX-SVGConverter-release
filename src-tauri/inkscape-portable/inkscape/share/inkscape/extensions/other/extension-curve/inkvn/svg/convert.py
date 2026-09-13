"""
inkvn Converter

Convert the intermediate data to Inkscape read by read.py
"""

import logging
from typing import List, Optional, Tuple, Union

import inkex
from inkex.base import SvgOutputMixin
import lxml.etree

from ..reader.read import CurveReader

from ..elements.artboard import VNArtboard
from ..elements.base import VNBaseElement
from ..elements.guide import VNGuideElement
from ..elements.group import VNGroupElement
from ..elements.image import VNImageElement
from ..elements.path import VNPathElement
from ..elements.text import VNTextElement, singleStyledText
from ..elements.styles import VNColor, VNGradient, pathStrokeStyle


class CurveConverter:
    """
    inkvn CurveConverter

    Convert the intermediate data to Inkscape.
    """

    def __init__(self) -> None:
        self.reader: CurveReader
        self.target_version: int
        self.has_transform_applied: bool
        self.doc: lxml.etree._ElementTree
        self.document: inkex.SvgDocumentElement
        self.offset_x: float
        self.offset_y: float

    def convert(self, reader: CurveReader, clip_page: bool = False) -> None:
        self.reader = reader

        """
        file version check

        New curve holds path data without transforms
        However, old curve has transforms already applied.
        I don't know exactly when the behavior has changed, so it's set to 30 as placeholder
        """
        self.has_transform_applied = reader.file_version < 30

        first_artboard = reader.artboards[0]

        self.doc = SvgOutputMixin.get_template(
            width=first_artboard.frame.width,
            height=first_artboard.frame.height,
        )
        self.document = self.doc.getroot()

        # Adding comments
        comment = lxml.etree.Comment(" Converted by extension-curve ")
        self.document.addprevious(comment)

        # first artboard becomes the front page
        # other artboards will be placed relative to the first one
        self.offset_x = first_artboard.frame.x
        self.offset_y = first_artboard.frame.y

        for target_artboard in reader.artboards:
            page = inkex.Page.new(
                width=target_artboard.frame.width,
                height=target_artboard.frame.height,
                x=target_artboard.frame.x - self.offset_x,
                y=target_artboard.frame.y - self.offset_y,
            )
            self.document.namedview.add(page)
            page.set("inkscape:label", target_artboard.title)

            self.load_page(
                self.document.add(inkex.Layer.new(label=target_artboard.title)),
                target_artboard,
                clip_page,
            )
            self.doc.getroot()

    def load_page(
        self, root_layer: inkex.Layer, artboard: VNArtboard, clip_page: bool = False
    ) -> None:
        """Convert  VNArtboard to inkex page."""

        # artboards have translations
        tr = inkex.transforms.Transform()
        tr_vector = inkex.Vector2d(
            artboard.frame.x - self.offset_x, artboard.frame.y - self.offset_y
        )
        tr.add_translate(tr_vector)
        root_layer.transform = tr

        # Artboard color/gradient
        rect = inkex.Rectangle.new(0, 0, artboard.frame.width, artboard.frame.height)
        rect.label = "background"

        # Fill Style
        if artboard.fillColor is not None:
            self.set_fill_color_styles(rect, artboard.fillColor)
            root_layer.add(rect)
        elif artboard.fillGradient is not None:
            if artboard.fillGradient.transform is not None:
                artboard.fillGradient.gradient.set(
                    "gradientTransform", artboard.fillGradient.transform
                )
            self.set_fill_grad_styles(rect, artboard.fillGradient)
            root_layer.add(rect)
        # if fill is none, rect will be dismissed

        # artboard clipping (optional), similar to Vectornator svg output.
        if clip_page:
            clip = inkex.ClipPath()
            clip_rect = inkex.Rectangle.new(
                0, 0, artboard.frame.width, artboard.frame.height
            )
            clip_rect.label = "page clipping"

            clip.add(clip_rect)
            self.document.defs.add(clip)

            if clip is not None:
                root_layer.style["clip-path"] = f"url(#{clip.get_id()})"

        # layers in the artboard
        for layer in artboard.layers:
            parent = root_layer.add(inkex.Layer.new(layer.name))
            parent.set("opacity", layer.opacity)
            parent.style["display"] = "inline" if layer.isVisible else "none"
            if layer.isLocked:
                parent.set("sodipodi:insensitive", "true")
            # ? isExpanded

            # elements in the layer
            for element in layer.elements:
                elm = self.load_element(element)
                if elm is not None:
                    parent.add(elm)

        # Guide
        for guide in artboard.guides:
            if guide is not None:
                self.add_guide(guide, tr_vector)

    def load_element(self, element: VNBaseElement) -> Optional[inkex.BaseElement]:
        """Converts an element to an SVG element."""
        try:
            if isinstance(element, VNGroupElement):
                return self.convert_group(element)

            elif isinstance(element, VNImageElement):
                return self.convert_image(element)

            elif isinstance(element, VNPathElement):
                return self.convert_path(element)

            elif isinstance(element, VNTextElement):
                # inkex.utils.debug(f'{element.name}: Text has been successfully parsed, but text import is not supported yet.')
                return self.convert_text(element)

            elif isinstance(element, VNBaseElement):
                return self.convert_base(element)  # will be empty path element

        except Exception as e:
            logging.error(f"Error converting element: {e}", exc_info=True)

    def convert_group(self, group_element: VNGroupElement) -> inkex.Group:
        """
        Converts a VNGroupElement to an SVG group (inkex.Group),
        or ClipPath when the group contains clipping mask.
        """

        group = inkex.Group()

        # BaseElement
        group.label = group_element.name
        group.style["opacity"] = group_element.opacity
        group.style["mix-blend-mode"] = group_element.convert_blend()
        group.style["display"] = "none" if group_element.isHidden else "inline"
        if group_element.isLocked:
            group.set("sodipodi:insensitive", "true")
        if not self.has_transform_applied and group_element.localTransform is not None:
            group.transform = group_element.localTransform.convert_transform()

        # blur
        if group_element.blur > 0:
            self.set_blur(group, group_element.convert_blur())

        # clipping mask
        clip_path_child = None
        clip = None
        for child in group_element.groupElements:
            # find the clipping mask
            if isinstance(child, VNPathElement) and child.mask == 1:
                clip_path_child = child
                break

        if clip_path_child:
            clip = inkex.ClipPath()
            clip_path_element = self.convert_path(clip_path_child)

            clip.add(clip_path_element)
            self.document.defs.add(clip)

        if clip is not None:
            group.style["clip-path"] = f"url(#{clip.get_id()})"

        for child in group_element.groupElements:
            svg_element = self.load_element(child)
            if svg_element is not None:
                group.add(svg_element)

        return group

    def convert_image(
        self, image_element: VNImageElement
    ) -> Union[inkex.Image, inkex.Group]:
        """Converts a VNImageElement to an SVG image (inkex.Image)."""
        image = inkex.Image()

        # BaseElement
        image.label = image_element.name
        image.style["opacity"] = image_element.opacity
        image.style["mix-blend-mode"] = image_element.convert_blend()
        image.style["display"] = "none" if image_element.isHidden else "inline"
        if image_element.isLocked:
            image.set("sodipodi:insensitive", "true")
        if image_element.transform is not None:
            image.transform = image_element.transform
        elif (
            not self.has_transform_applied and image_element.localTransform is not None
        ):
            image.transform = image_element.localTransform.convert_transform()

        # blur
        if image_element.blur > 0:
            self.set_blur(image, image_element.convert_blur())

        # Image
        img_format = image_element.image_format()
        width, height = image_element.image_dimension()

        image.set("preserveAspectRatio", "none")
        image.set(
            inkex.addNS("href", "xlink"),
            f"data:image/{img_format.lower()};base64,{image_element.imageData}",
        )
        image.set("width", width)
        image.set("height", height)

        # image cropping with clipping mask
        if image_element.cropRect is not None:
            clip = inkex.ClipPath()
            clip_element = image_element.convert_crop_rect()

            clip.add(clip_element)
            self.document.defs.add(clip)
            image.style["clip-path"] = f"url(#{clip.get_id()})"

        return image

    def convert_path(self, path_element: VNPathElement) -> inkex.PathElement:
        """Converts a VNPathElement to an SVG path (inkex.PathElement)."""
        path = inkex.PathElement()

        # pathGeometry
        if path_element.pathGeometries:
            for path_geometry in path_element.pathGeometries:
                path.path += path_geometry.path

        if not self.has_transform_applied and path_element.localTransform is not None:
            path.transform = path_element.localTransform.convert_transform()

            # Apply Transform
            path = inkex.PathElement.new(path.get_path().transform(path.transform))

        # PathEffect(corner), does not work for other paths in compoundPath
        if (
            not self.has_transform_applied
            and path_element.pathGeometries[0].corner_radius is not None
        ):
            corner_radius = path_element.pathGeometries[0].corner_radius
            if any(corner_radius):  # only if there are values other than 0
                self.set_corner(path, corner_radius)

        # BaseElement
        path.label = path_element.name
        path.style["opacity"] = path_element.opacity
        path.style["mix-blend-mode"] = path_element.convert_blend()
        path.style["display"] = "none" if path_element.isHidden else "inline"
        if path_element.isLocked:
            path.set("sodipodi:insensitive", "true")

        # blur
        if path_element.blur > 0:
            self.set_blur(path, path_element.convert_blur())

        # Stroke Style
        if path_element.strokeStyle is not None:
            self.set_stroke_styles(path, path_element.strokeStyle)
        else:
            path.style["stroke"] = "none"

        # Fill Style
        if path_element.fillColor is not None:
            self.set_fill_color_styles(path, path_element.fillColor)
        elif path_element.fillGradient is not None:
            # Add gradientTransform
            # matrix transform is based on Vectornator 4.13.2, format 13
            # and Linearity Curve 5.1.1, format 21
            if path_element.fillGradient.transform is not None:
                path_element.fillGradient.gradient.set(
                    "gradientTransform", path_element.fillGradient.transform
                )

            elif (
                not self.has_transform_applied
                and path_element.localTransform is not None
            ):
                gradient_transform = path_element.localTransform.convert_transform()
                path_element.fillGradient.gradient.set(
                    "gradientTransform", gradient_transform
                )

            self.set_fill_grad_styles(path, path_element.fillGradient)
        else:
            path.style["fill"] = "none"

        return path

    def convert_text(self, text_element: VNTextElement) -> inkex.TextElement:
        """Converts a VNTextElement to an SVG Text (inkex.TextElement)."""
        text = inkex.TextElement()

        # transform
        if text_element.transform is not None:
            text.transform = text_element.transform
        elif not self.has_transform_applied and text_element.localTransform is not None:
            # remove scale to prevent over-compressed look
            text.transform = text_element.localTransform.convert_transform(
                with_scale=False
            )

        # BaseElement
        text.label = text_element.name
        text.style["opacity"] = text_element.opacity
        text.style["mix-blend-mode"] = text_element.convert_blend()
        text.style["display"] = "none" if text_element.isHidden else "inline"
        if text_element.isLocked:
            text.set("sodipodi:insensitive", "true")

        # blur
        if text_element.blur > 0:
            self.set_blur(text, text_element.convert_blur())

        if text_element.string and text_element.styledText:
            offset = 0  # global offset(letter count)
            y_offset = 0
            line_height = 1.0  # default paragraph margin

            paragraphs = text_element.string.split("\n")
            styled_index = 0  # current styledText
            remaining_length = 0  # remaining range of styledText

            for para in paragraphs:
                # line-tspan
                # TODO how should I implement align-center/left/right?
                line_tspan = inkex.Tspan()
                line_tspan.set("sodipodi:role", "line")
                line_tspan.set("x", "0")
                line_tspan.set("y", f"{y_offset}px")

                # inkex.utils.debug(f"this para: {repr(para)}")

                para_offset = 0  # offset in para
                while para_offset < len(para) + 1:
                    # break the loop if there's no remaining styledText
                    if styled_index >= len(text_element.styledText):
                        break

                    styled = text_element.styledText[styled_index]

                    # skip styledText which contains only "\n"
                    # ignore this check when offset == 0(start)
                    if offset != 0:
                        while (
                            styled.length == 1
                            and text_element.string[offset - 1] == "\n"
                        ):
                            offset += 1
                            para_offset += 1
                            styled_index += 1
                            # break the loop if there's no remaining styledText
                            if styled_index >= len(text_element.styledText):
                                break

                            styled = text_element.styledText[styled_index]

                    # set remaining_length
                    if remaining_length <= 0:
                        remaining_length = styled.length

                    # apply style "until" there's no more text in para
                    apply_length = min(len(para) + 1 - para_offset, remaining_length)
                    substring = para[para_offset : para_offset + apply_length]

                    tspan = inkex.Tspan()
                    tspan.text = substring
                    self.set_tspan_style(tspan, styled)

                    line_tspan.append(tspan)

                    # update offset
                    para_offset += apply_length
                    remaining_length -= apply_length

                    # move on to next styledText
                    if remaining_length <= 0:
                        styled_index += 1

                text.append(line_tspan)
                y_offset += (
                    styled.fontSize * line_height
                )  # last font decides line height
                offset += len(para) + 1

        return text

    def convert_base(self, base_element: VNBaseElement) -> inkex.PathElement:
        """Converts a VNBaseElement to an empty SVG path (inkex.PathElement)."""
        inkex.utils.debug(
            f"{base_element.name}: This element will be imported as empty path."
        )

        path = inkex.PathElement()

        # BaseElement
        path.label = base_element.name
        path.style["opacity"] = base_element.opacity
        path.style["mix-blend-mode"] = base_element.convert_blend()
        path.style["display"] = "none" if base_element.isHidden else "inline"
        if base_element.isLocked:
            path.set("sodipodi:insensitive", "true")

        # Style
        path.style["stroke"] = "none"
        path.style["fill"] = "none"

        return path

    def set_stroke_styles(
        self, elem: inkex.BaseElement, stroke: pathStrokeStyle
    ) -> None:
        """Apply pathStrokeStyle to inkex.BaseElement."""
        elem.style["stroke"] = stroke.color.hex
        elem.style["stroke-opacity"] = stroke.color.alpha
        elem.style["stroke-linecap"] = stroke.basicStrokeStyle.cap
        elem.style["stroke-linejoin"] = stroke.basicStrokeStyle.join
        elem.style["stroke-dasharray"] = stroke.basicStrokeStyle.dashPattern
        elem.style["stroke-width"] = stroke.width

    def set_fill_color_styles(self, elem: inkex.BaseElement, fill: VNColor) -> None:
        """Apply fillColor to inkex.BaseElement."""
        elem.style["fill"] = fill.hex
        elem.style["fill-opacity"] = fill.alpha
        elem.style["fill-rule"] = "nonzero"

    def set_fill_grad_styles(self, elem: inkex.BaseElement, fill: VNGradient) -> None:
        """Apply fillGradient to inkex.BaseElement."""
        self.document.defs.add(fill.gradient)
        elem.style["fill"] = f"url(#{fill.gradient.get_id()})"
        elem.style["fill-rule"] = "nonzero"

    def set_blur(
        self, elem: inkex.BaseElement, blur: inkex.Filter.GaussianBlur
    ) -> None:
        """Apply blur to inkex.BaseElement."""
        filt: inkex.Filter = inkex.Filter()
        filt.set("color-interpolation-filters", "sRGB")
        filt.add(blur)
        self.document.defs.add(filt)

        # Only one filter will be there
        elem.style["filter"] = f"url(#{filt.get_id()})"

    def set_corner(self, elem: inkex.PathElement, corner_radius: List[float]) -> None:
        """Apply rounded corner to inkex.PathElement."""
        params = " @ ".join(f"F,0,0,1,0,{r},0,1" for r in corner_radius)

        # FIXME more cornerRadius work
        #  flexible="false" is how Linearity Curve behaved,
        #  but this is not optimal
        path_effect = inkex.PathEffect.new(
            effect="fillet_chamfer",
            lpeversion="1",
            method="auto",
            flexible="false",
            is_visible="true",
            satellites_param=params,  # Inkscape 1.2
            nodesatellites_param=params,  # Inkscape 1.3
        )

        self.document.defs.add(path_effect)
        elem.set("inkscape:original-d", str(elem.path))
        elem.set("inkscape:path-effect", f"{path_effect.get_id(1)}")
        elem.attrib.pop("d", None)  # delete "d", Inkscape auto-generates LPE path

    def set_tspan_style(self, elem: inkex.Tspan, styled: singleStyledText) -> None:
        # weight, style
        # Condensed is not supported
        known_styles = {
            "Regular": ("normal", "normal"),
            "Plain": ("normal", "normal"),
            "Ultrajada": ("normal", "normal"),
            "Bold": ("bold", "normal"),
            "Italic": ("normal", "italic"),
            "Oblique": ("normal", "oblique"),
            "ExtraBold": ("bold", "normal"),  # ExtraBold as Bold
            "Light": ("300", "normal"),  # numbers may not be optimal
            "Medium": ("500", "normal"),
            "SemiBold": ("600", "normal"),
            "BoldOblique": ("bold", "oblique"),  # combined
            "BoldItalic": ("bold", "italic"),
        }

        font_parts = styled.fontName.split("-")

        # font name without styling
        base_font_parts = []
        font_weight = "normal"
        font_style = "normal"

        for part in font_parts:
            if part in known_styles:
                font_weight, font_style = known_styles[part]
            # name
            else:
                base_font_parts.append(part)

        base_font = " ".join(base_font_parts)
        base_font = f"'{base_font}'"

        elem.style["font-size"] = f"{styled.fontSize}px"
        elem.style["letter-spacing"] = f"{styled.kerning}px"
        elem.style["font-family"] = base_font
        elem.style["font-weight"] = font_weight
        elem.style["font-style"] = font_style

        # fill
        if styled.fillColor:
            self.set_fill_color_styles(elem, styled.fillColor)
        # elif styled.fillGradient:
        #    self.set_fill_grad_styles(tspan, styled.fillGradient)
        else:
            elem.style["fill"] = "none"

        ## stroke
        # if styled.strokeStyle:
        #    self.set_stroke_styles(tspan, styled.strokeStyle)

        ## decorations
        # if styled.underline:
        #    tspan.style["text-decoration"] = "underline"
        # if styled.strikethrough:
        #    tspan.style["text-decoration"] = "line-through"

    def add_guide(self, guide_element: VNBaseElement, offset: inkex.Vector2d) -> None:
        """
        Creates a Guide from VNGuideElement.

        Older Vectornator(4.10.4) uses actual path data for guides,
        which are converted into inkvn GroupElement.

        So argument type is set to VNBaseElement
        """

        def extract_guide(inkex_path: inkex.Path) -> Tuple[float, bool]:
            """
            Extracts guide offset and orientation from an inkex.Path object.
            """
            path_data = inkex_path.to_arrays()

            if len(path_data) < 2:
                raise ValueError("Invalid guide path: Less than two points found.")

            (_, [x0, y0]), (_, [x1, y1]) = path_data[0], path_data[-1]

            if y0 == y1:  # horizontal
                return y0, True
            elif x0 == x1:  # vertical
                return x0, False
            else:
                raise ValueError(
                    "Invalid guide path: Not perfectly horizontal or vertical."
                )

        orientation = False
        guide_offset = 0

        if isinstance(guide_element, VNGuideElement):
            guide_offset = guide_element.offset
            orientation = guide_element.orientation == 1

        elif isinstance(guide_element, VNGroupElement):
            # legacy guide element.
            # there should be two elements, second one is the line
            sub_element = guide_element.groupElements[1]
            if isinstance(sub_element, VNPathElement):
                path_data = sub_element.pathGeometries[0].path
                guide_offset, orientation = extract_guide(path_data)

        if orientation:
            self.document.namedview.add_guide(guide_offset + offset.y, orient=True)
        else:
            self.document.namedview.add_guide(guide_offset + offset.x, orient=False)
