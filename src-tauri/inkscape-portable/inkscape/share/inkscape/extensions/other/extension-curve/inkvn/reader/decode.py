"""
VI decoders

converts Linearity Curve (5.x) JSON data to inkvn.
This handles Curve documents with flattened JSON (IDs).

Needs files that are fileFormatVersion 30~39.
"""

import base64
import logging
from typing import Any, Dict, List, Optional, Union

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
from ..elements.text import VNTextElement, singleStyledText, textProperty
from ..elements.styles import VNColor, VNGradient, pathStrokeStyle, basicStrokeStyle


def get_json_element(json_data: Dict, list_key: str, index: str) -> Optional[Dict]:
    """
    Retrieve an attribute according to key and index from gid_json.

    Linearity Curve makes use of IDs and lists of elements.
    This function makes it easier to get Curve elements.
    """

    elements = json_data.get(list_key)

    if elements is None:  # return None if the top-level key doesn't exist.
        return None

    return elements[index]


def read_artboard(archive: Any, gid_json: Dict) -> VNArtboard:
    """
    Reads gid.json(artboard) and returns artboard class.

    Argument `archive` is needed for image embedding.
    """
    # Layers
    # "layer_ids" contain layer indexes, while "layers" contain existing layers
    artboard = gid_json["artboards"][0]
    layer_ids = artboard["layerIds"]
    existing_layers = gid_json["layers"]
    layer_list: List[VNLayer] = []
    for layer_id in layer_ids:
        layer = get_json_element(gid_json, "layers", layer_id)
        if layer is not None:
            layer_list.append(read_layer(archive, gid_json, layer))

    # Guides
    # the very last layer has the guide
    guide_layer = existing_layers[len(layer_list)]
    guide_ids = guide_layer["elementIds"]
    guide_list: List[VNGuideElement] = []
    for guide_id in guide_ids:
        guide = get_json_element(gid_json, "elements", guide_id)
        if guide is not None:
            guide_list.append(read_element(archive, gid_json, guide))

    # Background Color
    fill_color = None
    fill_gradient = None
    fill_id = artboard.get("fillId")
    if fill_id is not None:
        fill_result = read_fill(gid_json, artboard, fill_id)
        if isinstance(fill_result, VNGradient):
            fill_gradient = fill_result
        elif isinstance(fill_result, VNColor):
            fill_color = fill_result

    return VNArtboard(
        title=artboard["title"],
        frame=Frame(**artboard["frame"]),
        layers=layer_list,
        guides=guide_list,
        fillColor=fill_color,
        fillGradient=fill_gradient,
    )


def read_layer(archive: Any, gid_json: Dict, layer: Dict) -> VNLayer:
    """
    Read specified layer and return their attributes as class.

    gid_json is used for finding elements inside the layer.
    """
    layer_element_ids = layer["elementIds"]
    element_list: List[VNBaseElement] = []
    for element_id in layer_element_ids:
        element = get_json_element(gid_json, "elements", element_id)
        if element is not None:
            element_list.append(read_element(archive, gid_json, element))

    return VNLayer(
        name=layer.get("name", "Unnamed Layer"),
        opacity=layer.get("opacity", 1.0),
        isVisible=layer.get("isVisible", True),
        isLocked=layer.get("isLocked", False),
        isExpanded=layer.get("isExpanded", False),
        elements=element_list,
    )


def read_element(archive: Any, gid_json: Dict, element: Dict) -> VNBaseElement:
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
        local_transform_id = element["localTransformId"]
        if local_transform_id is not None:
            base_element_data["localTransform"] = VNTransform(
                **get_json_element(gid_json, "localTransforms", local_transform_id)
            )

        # Guide (GuideElement)
        guide_id = element.get("subElement", {}).get("guideLine", {}).get("_0")
        if guide_id is not None:
            guide = get_json_element(gid_json, "guideLines", guide_id)
            return VNGuideElement(**guide, **base_element_data)

        # Group (GroupElement)
        group_id = element.get("subElement", {}).get("group", {}).get("_0")
        if group_id is not None:
            return read_group(archive, gid_json, group_id, base_element_data)

        # Image (ImageElement)
        image_id = element.get("subElement", {}).get("image", {}).get("_0")
        if image_id is not None:
            return read_image(archive, gid_json, image_id, base_element_data)

        # abstractImage in Curve 5.13.0, format 40
        abst_image_id = element.get("subElement", {}).get("abstractImage", {}).get("_0")
        if abst_image_id is not None:
            return read_image(archive, gid_json, abst_image_id, base_element_data)

        # Stylable (either PathElement or TextElement)
        stylable_id = element.get("subElement", {}).get("stylable", {}).get("_0")
        if stylable_id is not None:
            stylable = get_json_element(gid_json, "stylables", stylable_id)

            # clipping mask
            mask = stylable.get("mask", 0)

            # will be used to return PathElement
            fill_id = None
            stroke_style_id = None
            abstract_path_id = None

            # singleStyles (based on Curve 5.1.2)
            single_style_id = (
                stylable.get("subElement", {}).get("singleStyle", {}).get("_0")
            )
            if single_style_id is not None:
                single_style = get_json_element(
                    gid_json, "singleStyles", single_style_id
                )
                # old abstractPath lacks these ids below
                abstract_path_id = single_style.get("subElement")
                stroke_style_id = stylable.get("strokeStyleId")
                fill_id = single_style.get("fillId")

            # Abstract Path (PathElement)
            sub_element = stylable.get("subElement", {})
            if "abstractPath" in sub_element:
                abstract_path_id = sub_element["abstractPath"].get("_0")
            if abstract_path_id is not None:
                path_element_data = {
                    "abst_path": abstract_path_id,
                    "stylable": stylable,
                    "mask": mask,
                    "stroke": stroke_style_id,
                    "fill": fill_id,
                }
                return read_abst_path(gid_json, path_element_data, base_element_data)

            # Abstract Text (TextElement)
            abstract_text_id = (
                stylable.get("subElement", {}).get("abstractText", {}).get("_0")
            )
            if abstract_text_id is not None:
                return read_abst_text(gid_json, abstract_text_id, base_element_data)

        # if the element is unknown type:
        raise NotImplementedError(
            f"{base_element_data['name']}: This element has unknown type."
        )

    except Exception as e:
        logging.error(f"Error reading element: {e}", exc_info=True)
        return VNBaseElement(**base_element_data)


def read_group(
    archive: Any, gid_json: Dict, group_id: int, base_element: Dict
) -> VNGroupElement:
    """Reads elements inside group and returns as VNGroupElement."""
    # get elements inside group
    group = get_json_element(gid_json, "groups", group_id)
    group_element_ids = group["elementIds"]
    group_element_list: List[VNBaseElement] = []

    for group_element_id in group_element_ids:
        group_element = get_json_element(gid_json, "elements", group_element_id)
        if group_element is not None:
            # get group elements recursively
            group_element_list.append(read_element(archive, gid_json, group_element))

    return VNGroupElement(groupElements=group_element_list, **base_element)


def read_image(
    archive: Any, gid_json: Dict, image_id: int, base_element: Dict
) -> VNImageElement:
    """Reads image element, encodes image in Base64 and returns VNImageElement."""
    # relativePath contains *.dat (bitmap data)
    # sharedFileImage doesn't exist in 5.1.1 (file version 21) document
    # Curve 5.13.0, format 40 uses abstractImage
    # TODO format 40 support lags behind other versions(14, 19, 44)
    transform = None
    image_data_id = None
    crop_rect = None

    image = get_json_element(gid_json, "images", image_id)
    abst_image = get_json_element(gid_json, "abstractImages", image_id)

    if image is not None:
        if image.get("imageData") is not None:
            image_data_id = (
                image.get("imageData", {}).get("sharedFileImage", {}).get("_0")
            )
            # cropping
            if image.get("cropRect") is not None:
                crop_rect = tuple(map(tuple, image.get("cropRect")))
        else:  # legacy image
            image_data_id = image.get("imageDataId")
            transform = image.get("transform")

    elif abst_image is not None:
        image_data_id = abst_image.get("subElement", {}).get("image", {}).get("_0")
        transform = abst_image.get("transform")

        # cropping
        if abst_image.get("cropRect") is not None:
            crop_rect = tuple(map(tuple, abst_image.get("cropRect")))

    image_file = get_json_element(gid_json, "imageDatas", image_data_id)["relativePath"]
    image_data = ext.read_dat_from_zip(archive, image_file)
    return VNImageElement(
        imageData=image_data, transform=transform, cropRect=crop_rect, **base_element
    )


def read_abst_path(
    gid_json: Dict, path_element: Dict, base_element: Dict
) -> VNPathElement:
    """Reads path element and returns VNPathElement."""
    abstract_path = get_json_element(
        gid_json, "abstractPaths", path_element["abst_path"]
    )
    stroke_id = path_element["stroke"]
    fill_id = path_element["fill"]
    fill_color = None
    fill_gradient = None
    stroke_style = None

    # Stroke Style
    if "strokeStyleId" in abstract_path:
        stroke_id = abstract_path["strokeStyleId"]
    if stroke_id is not None:
        stroke_style = read_stroke(gid_json, stroke_id)

    # fill
    if "fillId" in abstract_path:
        fill_id = abstract_path["fillId"]
    if fill_id is not None:
        fill_result = read_fill(gid_json, path_element["stylable"], fill_id)
        if isinstance(fill_result, VNGradient):
            fill_gradient = fill_result
        elif isinstance(fill_result, VNColor):
            fill_color = fill_result

    # Path
    path_id = abstract_path.get("subElement", {}).get("path", {}).get("_0")
    path_geometry_list: List[pathGeometry] = []
    if path_id is not None:
        path = get_json_element(gid_json, "paths", path_id)

        # Path Geometry
        geometry_id = path.get("geometryId")
        if geometry_id is not None:
            path_geometry_list.append(
                pathGeometry(
                    **get_json_element(gid_json, "pathGeometries", geometry_id)
                )
            )

    # compoundPath
    compound_path_id = (
        abstract_path.get("subElement", {}).get("compoundPath", {}).get("_0")
    )
    if compound_path_id is not None:
        compound_path = get_json_element(gid_json, "compoundPaths", compound_path_id)

        # Path Geometries (subpath)
        subpath_ids = compound_path.get("subpathIds")
        if subpath_ids is not None:
            for id in subpath_ids:
                path_geometry_list.append(
                    pathGeometry(**get_json_element(gid_json, "pathGeometries", id))
                )

    return VNPathElement(
        mask=path_element["mask"],
        fillColor=fill_color,
        fillGradient=fill_gradient,
        strokeStyle=stroke_style,
        pathGeometries=path_geometry_list,
        **base_element,
    )


def read_abst_text(
    gid_json: Dict, abstract_text_id: int, base_element: Dict
) -> Union[VNTextElement, VNBaseElement]:
    """
    Reads Curve text element and returns VNTextElement.
    """
    # TODO Improve Text support, new format
    abstract_text = get_json_element(gid_json, "abstractTexts", abstract_text_id)
    # check if the text is new format
    if abstract_text.get("attributedText") is None:
        # Which one is which?
        styled_text_id = abstract_text.get("textId")
        text_id = abstract_text.get("subElement", {}).get("text", {}).get("_0")
        stroke_style_id = abstract_text.get("strokeStyleId")

        # will be used to return TextElement
        text_property = None
        styled_text = None

        # textPath
        text_path_id = abstract_text.get("subElement", {}).get("textPath", {}).get("_0")
        if text_path_id is not None:
            inkex.utils.debug(f"{base_element['name']}: textOnPath is not supported.")

        # texts(layout??), will be named textProperty internally
        if text_id is not None:
            text_property = get_json_element(gid_json, "texts", text_id)
            text_property = textProperty(
                textFrameLimits=text_property.get("textFrameLimits"),
                textFramePivot=text_property.get("textFramePivot"),
            )

        # text stroke_style only contains basicStrokeStyle
        if stroke_style_id is not None:
            stroke_style = get_json_element(
                gid_json, "textStrokeStyles", stroke_style_id
            )
            stroke_style = basicStrokeStyle(**stroke_style)

        # styledTexts
        if styled_text_id is not None:
            styled_text = get_json_element(gid_json, "styledTexts", styled_text_id)
            string = styled_text["string"]
            styles = t.decode_new_text(styled_text)
            styled_text_list = read_styled_text(styles)

        return VNTextElement(
            string=string,
            transform=None,
            styledText=styled_text_list,
            textProperty=text_property,
            **base_element,
        )

    # legacy text
    # TODO implement Legacy Text decoder
    else:
        # will be used to return TextElement
        transform = None
        text_property = None
        styled_text = None

        # I cannot replicate textProperty in legacy format
        text_id = abstract_text.get("subElement", {}).get("text", {}).get("_0")
        if text_id is not None:
            text_property = get_json_element(gid_json, "texts", text_id)
            transform = text_property.get("transform")  # matrix
            # resize_mode = text_property.get("resizeMode")
            # height = text_property.get("height")
            # width = text_property.get("width")

        # styledText
        styled_text = NSKeyedUnarchiver(
            base64.b64decode(abstract_text["attributedText"])
        )
        string = styled_text["NSString"]
        styles = t.decode_old_text(styled_text)
        styled_text_list = read_styled_text(styles)

        return VNTextElement(
            string=string,
            transform=transform,
            styledText=styled_text_list,
            textProperty=text_property,  # TODO Illegal format
            **base_element,
        )


def read_styled_text(styles: List[Dict]) -> List[singleStyledText]:
    styled_text_list: List[singleStyledText] = []
    for style in styles:
        color = None
        # color
        if style.get("fillColor") is not None:
            color = VNColor(style["fillColor"])
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


def read_stroke(gid_json: Dict, stroke_id: int) -> pathStrokeStyle:
    """Reads stroke style and returns as class."""
    stroke_style = get_json_element(gid_json, "pathStrokeStyles", stroke_id)

    if stroke_style is None:  # Try legacy "strokeStyles" if not found
        stroke_style = get_json_element(gid_json, "strokeStyles", stroke_id)

    if (
        "dashPattern" in stroke_style
        and "join" in stroke_style
        and "cap" in stroke_style
    ):
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


def read_fill(
    gid_json: Dict, stylable: Dict, fill_id: int
) -> Union[VNGradient, VNColor, None]:
    """Reads fill data and returns as class."""
    color: Dict = (
        get_json_element(gid_json, "fills", fill_id).get("color", {}).get("_0")
    )
    gradient: Dict = (
        get_json_element(gid_json, "fills", fill_id).get("gradient", {}).get("_0")
    )

    if gradient is not None:
        # Newer Curve
        if gradient.get("transform") is not None:
            return VNGradient(
                fill_transform=gradient["transform"],
                transform_matrix=None,
                stops=gradient["gradient"]["stops"],
                typeRawValue=gradient["gradient"]["typeRawValue"],
            )
        # Old Curve like 5.1.1
        else:
            fill_transform_id = stylable.get("fillTransformId")
            fill_transform = get_json_element(
                gid_json, "fillTransforms", fill_transform_id
            )
            return VNGradient(
                fill_transform=fill_transform,
                transform_matrix=fill_transform["transform"],
                stops=gradient["stops"],
                typeRawValue=gradient["typeRawValue"],
            )
    elif color is not None:
        return VNColor(color_dict=color)
