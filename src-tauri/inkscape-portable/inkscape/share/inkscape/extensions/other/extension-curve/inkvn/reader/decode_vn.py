"""
VI decoders

converts Vectornator JSON data to inkvn (Vectornator mode).
This is used for Linearity Curve 5.0.x import as well (format 19).

Needs more Vectornator files for reference
"""

import base64
import logging
from typing import Any, Dict, List, Union

import inkex

import inkvn.reader.extract as ext
import inkvn.reader.text as t
from inkvn.utils import NSKeyedUnarchiver

from ..elements.artboard import VNArtboard, VNLayer, Frame
from ..elements.base import VNBaseElement, VNTransform
from ..elements.guide import VNGuideElement
from ..elements.group import VNGroupElement
from ..elements.image import VNImageElement
from ..elements.path import VNPathElement, pathGeometry
from ..elements.text import VNTextElement, singleStyledText
from ..elements.styles import (
    VNColor,
    VNGradient,
    pathStrokeStyle,
    basicStrokeStyle,
    styledElementData,
)


def read_vn_artboard(archive: Any, gid_json: Dict) -> VNArtboard:
    """
    Reads gid.json(artboard) and returns artboard class.

    Argument `archive` is needed for image embedding.
    """
    # Layers
    layers = gid_json["layers"]
    layer_list: List[VNLayer] = []
    for layer in layers:
        if layer is not None:
            layer_list.append(read_vn_layer(archive, layer))

    # Guides
    guides = gid_json["guideLayer"]["elements"]
    guide_list: List[VNGuideElement] = []
    for guide in guides:
        if guide is not None:
            guide_list.append(read_vn_element(archive, guide))

    # Background Color
    fill_color = None
    fill_gradient = None
    fill_result = read_vn_fill(gid_json)
    if isinstance(fill_result, VNGradient):
        fill_gradient = fill_result
    elif isinstance(fill_result, VNColor):
        fill_color = fill_result

    return VNArtboard(
        title=gid_json["title"],
        frame=Frame(**gid_json["frame"]),
        layers=layer_list,
        guides=guide_list,
        fillColor=fill_color,
        fillGradient=fill_gradient,
    )


def read_vn_layer(archive: Any, layer: Dict) -> VNLayer:
    """
    Read specified layer and return their attributes as class.
    """
    # children
    elements = layer["elements"]
    element_list: List[VNBaseElement] = []
    for element in elements:
        if element is not None:
            element_list.append(read_vn_element(archive, element))

    # properties
    if layer.get("properties") is not None:
        properties = layer["properties"]

    # properties did not exist in 4.9.0, format 7.
    else:
        properties = layer

    return VNLayer(
        name=properties.get("name", "Unnamed Layer"),
        opacity=properties.get("opacity", 1.0),
        isVisible=properties.get("isVisible", True),
        isLocked=properties.get("isLocked", False),
        isExpanded=properties.get("isExpanded", False),
        elements=element_list,
    )


def read_vn_element(archive: Any, element: Dict) -> VNBaseElement:
    """Traverse specified element and extract their attributes."""
    base_element_data = {
        "name": element.get("name", "Unnamed Element"),
        "blur": element.get("blur", 0.0),
        "opacity": element.get("opacity", 1.0),
        "blendMode": element.get("blendMode", 0),
        "isHidden": element.get("isHidden", False),
        "isLocked": element.get("isLocked", False),
        "localTransform": None,
    }

    try:
        # localTransform (BaseElement)
        local_transform = element.get("localTransform")
        if local_transform is not None:
            base_element_data["localTransform"] = VNTransform(**local_transform)

        # Guide (GuideElement)
        guide = element.get("subElement", {}).get("guideLine", {}).get("_0")
        if guide is not None:
            return VNGuideElement(**guide, **base_element_data)

        # Group (GroupElement)
        group = element.get("subElement", {}).get("group", {}).get("_0")
        if group is not None:
            return read_vn_group(archive, group, base_element_data)

        # Image (ImageElement)
        image = element.get("subElement", {}).get("image", {}).get("_0")
        if image is not None:
            return read_vn_image(archive, image, base_element_data)

        # Stylable (either PathElement or TextElement)
        stylable = element.get("subElement", {}).get("stylable", {}).get("_0")
        if stylable is not None:
            # clipping mask
            mask = stylable.get("mask", 0)

            # Stroke Style
            stroke_style = read_vn_stroke(stylable)

            # fill
            fill_color = None
            fill_gradient = None
            fill_result = read_vn_fill(stylable)
            if isinstance(fill_result, VNGradient):
                fill_gradient = fill_result
            elif isinstance(fill_result, VNColor):
                fill_color = fill_result

            # singleStyles (Vectornator 4.13.6, format 19)
            abstract_path = None
            single_style = (
                stylable.get("subElement", {}).get("singleStyle", {}).get("_0")
            )
            if single_style is not None:
                fill_result = read_vn_fill(stylable, single_style)
                if isinstance(fill_result, VNGradient):
                    fill_gradient = fill_result
                elif isinstance(fill_result, VNColor):
                    fill_color = fill_result

                # use single_style as abstract path
                abstract_path = single_style.get("subElement")

            # Abstract Path (PathElement)
            sub_element = stylable.get("subElement", {})
            if "abstractPath" in sub_element:
                abstract_path = sub_element["abstractPath"].get("_0")
            if abstract_path is not None:
                path_element_data = styledElementData(
                    styled_data=abstract_path,
                    mask=mask,
                    stroke=stroke_style,
                    color=fill_color,
                    grad=fill_gradient,
                )
                return read_vn_abst_path(path_element_data, base_element_data)

            # Abstract Text (TextElement)
            text_data = stylable.get("subElement", {}).get("text", {}).get("_0")
            if text_data is not None:
                text_element_data = styledElementData(
                    styled_data=text_data,
                    mask=mask,
                    stroke=stroke_style,
                    color=fill_color,
                    grad=fill_gradient,
                )
                return read_vn_abst_text(text_element_data, base_element_data)

        # if the element is unknown type:
        raise NotImplementedError(
            f"{base_element_data['name']}: This element has unknown type."
        )

    except Exception as e:
        # if the element is unknown type or an error occurred:
        logging.error(f"Error reading element: {e}", exc_info=True)
        return VNBaseElement(**base_element_data)


def read_vn_group(archive: Any, group: Dict, base_element: Dict) -> VNGroupElement:
    """Reads elements inside group and returns as VNGroupElement."""
    # get elements inside group
    group_elements = group["elements"]
    group_element_list: List[VNBaseElement] = []

    for group_element in group_elements:
        if group_element is not None:
            # get group elements recursively
            group_element_list.append(read_vn_element(archive, group_element))

    return VNGroupElement(groupElements=group_element_list, **base_element)


def read_vn_image(archive: Any, image: Dict, base_element: Dict) -> VNImageElement:
    """Reads image element, encodes image in Base64 and returns VNImageElement."""
    # relativePath contains *.dat (bitmap data)
    transform = image["transform"]
    image_file = image["imageData"]["relativePath"]
    image_data = ext.read_dat_from_zip(archive, image_file)

    # cropping
    crop_rect = None
    if image.get("cropRect") is not None:
        crop_rect = tuple(map(tuple, image.get("cropRect")))
    return VNImageElement(
        imageData=image_data, transform=transform, cropRect=crop_rect, **base_element
    )


def read_vn_abst_path(
    path_element: styledElementData, base_element: Dict
) -> VNPathElement:
    """Reads path element and returns VNPathElement."""

    def _add_path(path_data: Dict, path_geometry_list: List[pathGeometry]) -> None:
        """appends path data to list"""
        geometry = path_data.get("geometry")
        # AbstractPath
        if path_data.get("nodes") is not None:
            path_geometry_list.append(
                pathGeometry(closed=path_data["closed"], nodes=path_data["nodes"])
            )
        # SingleStyle
        elif geometry is not None:
            path_geometry_list.append(
                pathGeometry(closed=geometry["closed"], nodes=geometry["nodes"])
            )

    # Path
    path_data = (
        path_element.styled_data.get("subElement", {}).get("pathData", {}).get("_0")
    )
    path_geometry_list: List[pathGeometry] = []

    if path_data is not None:
        text_path = path_data.get("subElement", {}).get("textPath", {}).get("_0")
        if text_path is not None:
            inkex.utils.debug(f"{base_element['name']}: textOnPath is not supported.")
        _add_path(path_data, path_geometry_list)

    # compoundPath
    compound_path_data = (
        path_element.styled_data.get("subElement", {})
        .get("compoundPathData", {})
        .get("_0")
    )
    if compound_path_data is not None:
        # Path Geometries (subpath)
        subpaths = compound_path_data.get("subpaths")
        if subpaths is not None:
            for sub_element in subpaths:
                sub_stylable = (
                    sub_element.get("subElement", {}).get("stylable", {}).get("_0")
                )
                # Vectornator 4.13.5, format 16
                if sub_stylable is not None:
                    sub_path = sub_stylable["subElement"]["abstractPath"]["_0"][
                        "subElement"
                    ]["pathData"]["_0"]

                # else (Vectornator 4.13.6, format 19)
                else:
                    sub_path = sub_element

                _add_path(sub_path, path_geometry_list)

    return VNPathElement(
        mask=path_element.mask,
        fillColor=path_element.color,
        fillGradient=path_element.grad,
        strokeStyle=path_element.stroke,
        pathGeometries=path_geometry_list,
        **base_element,
    )


def read_vn_abst_text(
    text_element: styledElementData, base_element: Dict
) -> VNBaseElement:
    """
    Reads legacy text element and returns VNTextElement.
    """
    text_data = text_element.styled_data

    # will be used to return TextElement
    text_property = None
    styled_text = None

    # I cannot replicate textProperty in legacy format
    transform = text_data.get("transform")  # matrix
    # resize_mode = text_data.get("resizeMode")
    # height = text_data.get("height")
    # width = text_data.get("width")

    # styledText
    styled_text = NSKeyedUnarchiver(base64.b64decode(text_data["attributedText"]))
    string = styled_text["NSString"]
    styles = t.decode_old_text(styled_text)
    styled_text_list = read_styled_text(styles, text_element)

    return VNTextElement(
        string=string,
        transform=transform,
        styledText=styled_text_list,
        textProperty=text_property,  # TODO Illegal format
        **base_element,
    )


def read_styled_text(
    styles: List[Dict], global_style: styledElementData
) -> List[singleStyledText]:
    styled_text_list: List[singleStyledText] = []
    # inkex.utils.debug(styles)
    for style in styles:
        color = None
        # color
        if style.get("fillColor") is not None:
            color = VNColor(style["fillColor"])
        # if there's no styles in text data, use global style
        elif global_style.color is not None:
            color = global_style.color

        # stroke # TODO text strokestyle(fix reader/text.py)
        # if style.get("strokeStyle") is not None:
        #    stroke = style["strokeStyle"]
        #    stroke = pathStrokeStyle(stroke_style, stroke)

        styled_text = singleStyledText(
            length=style.get("length"),
            fontName=style.get("fontName"),
            fontSize=style.get("fontSize"),
            alignment=style.get("alignment"),
            kerning=style.get("kerning"),
            lineHeight=style.get("lineHeight"),
            fillColor=color,
            fillGradient=None,  # TODO gradient applies globally
            strokeStyle=None,
            strikethrough=style.get("strikethrough"),
            underline=style.get("underline"),
        )
        styled_text_list.append(styled_text)

    return styled_text_list


def read_vn_stroke(stylable: Dict) -> pathStrokeStyle:
    """Reads stroke style and returns as class."""
    stroke_style = stylable.get("strokeStyle")
    if stroke_style is not None:
        stroke_style["basicStrokeStyle"] = {
            "cap": stroke_style["cap"],
            "dashPattern": stroke_style["dashPattern"],
            "join": stroke_style["join"],
            "position": stroke_style["position"],
        }
        return pathStrokeStyle(
            basicStrokeStyle=basicStrokeStyle(**stroke_style["basicStrokeStyle"]),
            color=VNColor(color_dict=stroke_style["color"]),
            width=stroke_style["width"],
        )


def read_vn_fill(
    stylable: Dict, single_style: Dict = None
) -> Union[VNGradient, VNColor, None]:
    """Reads fill data and returns as class."""
    # fill
    fill_data = stylable.get("fill")
    fill_color = stylable.get("fillColor")
    fill_gradient = stylable.get("fillGradient")

    # Vectornator 4.13.2, format 13
    if fill_data is not None:
        color: Dict = fill_data.get("color", {}).get("_0")
        gradient: Dict = fill_data.get("gradient", {}).get("_0")
        if gradient is not None:
            return VNGradient(
                fill_transform=stylable["fillTransform"],
                transform_matrix=stylable["fillTransform"]["transform"],
                stops=gradient["stops"],
                typeRawValue=gradient["typeRawValue"],
            )
        elif color is not None:
            return VNColor(color_dict=color)

    # singleStyles (Vectornator 4.13.6, format 19)
    if single_style is not None and single_style.get("fill") is not None:
        fill_data = single_style["fill"]
        color: Dict = fill_data.get("color", {}).get("_0")
        gradient: Dict = fill_data.get("gradient", {}).get("_0")
        if gradient is not None:
            return VNGradient(
                fill_transform=stylable["fillTransform"],
                transform_matrix=stylable["fillTransform"]["transform"],
                stops=gradient["stops"],
                typeRawValue=gradient["typeRawValue"],
            )
        elif color is not None:
            return VNColor(color_dict=color)

    # Vectornator 4.10.4, format 8
    elif fill_color is not None or fill_gradient is not None:
        if fill_gradient is not None:
            return VNGradient(
                fill_transform=stylable["fillTransform"],
                transform_matrix=stylable["fillTransform"]["transform"],
                stops=fill_gradient["stops"],
                typeRawValue=fill_gradient["typeRawValue"],
            )
        elif fill_color is not None:
            return VNColor(color_dict=fill_color)
