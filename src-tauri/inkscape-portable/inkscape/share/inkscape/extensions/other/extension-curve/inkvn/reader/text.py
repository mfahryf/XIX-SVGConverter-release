"""
VI text tools

Decodes Linearity Curve styledTexts and turn them into simpler format.
"""

from typing import Dict, List

from inkex.utils import debug


def decode_new_text(styled_text: Dict) -> List[Dict]:
    """Decodes upperBound system used by Newer text format."""
    # scanning upperBounds in styledText
    upper_bounds: List[int] = []
    for key in styled_text.keys():
        # TODO skipped strokeStyle
        # debug(f"KeyName:{key}")
        if (
            isinstance(styled_text[key], dict)
            and styled_text[key].get("values") is not None
        ):
            values = styled_text[key]["values"]
            for child_value in values:
                upper_bound = child_value["upperBound"]
                if upper_bound not in upper_bounds:
                    upper_bounds.append(upper_bound)
    upper_bounds.sort()

    # unifying each styles in styledText
    styles: List[Dict] = [{} for _ in range(len(upper_bounds))]
    for key in styled_text.keys():
        if (
            isinstance(styled_text[key], dict)
            and styled_text[key].get("values") is not None
        ):
            values = styled_text[key]["values"]
            for child_value in values:
                upper_bound = child_value["upperBound"]
                index = upper_bounds.index(upper_bound)
                styles[index][key] = child_value["value"]
                if index == 0:
                    styles[index]["length"] = upper_bound
                else:
                    styles[index]["length"] = upper_bound - upper_bounds[index - 1]

    # Propagate last known values forward
    last_known_values = {}
    for i in range(len(styles) - 1, -1, -1):
        for key in styled_text.keys():
            if key in styles[i]:
                last_known_values[key] = styles[i][key]
            elif key in last_known_values:
                styles[i][key] = last_known_values[key]

    return styles


def decode_old_text(unserialized: Dict) -> List[Dict]:
    """Decodes legacy text unpacked by NSKeyUnarchiver."""
    # string has already been processed
    # debug(f"here:{unserialized}")
    string = unserialized["NSString"]
    lengths = unserialized.get("NSAttributeInfo")
    styles = unserialized["NSAttributes"]

    formatted_data: List[Dict] = []

    # if text only contains one style
    if isinstance(styles, dict):
        lengths = [{"length": len(string), "attribute_id": 0}]
        styles = [styles]

    for length_info in lengths:
        length = length_info["length"]
        attribute_id = length_info["attribute_id"]

        # checking if NSAttributeInfo has successfully parsed
        if not styles or attribute_id < 0 or attribute_id >= len(styles):
            debug(
                f"Error: attribute_id {attribute_id} is out of range. styles length: {len(styles)}"
            )

        attribute = styles[attribute_id]

        # color
        # "NSColorSpace": 2 ?
        color_data = None
        ns_color = attribute.get("NSColor")
        if ns_color and isinstance(ns_color, dict):
            color_data = {
                "rgba": {
                    "red": ns_color.get("UIRed", 0),
                    "green": ns_color.get("UIGreen", 0),
                    "blue": ns_color.get("UIBlue", 0),
                    "alpha": ns_color.get("UIAlpha", 1),
                }
            }

        # paragraph
        paragraph_style = attribute.get("NSParagraphStyle")

        # font
        ns_font = attribute.get("NSFont")

        # TODO Include strokeStyle, lineHeight
        formatted_data.append(
            {
                # alignment might be wrong
                "alignment": paragraph_style.get("NSAlignment", 1) + 1,
                "length": length,
                "fillColor": color_data,
                "fontName": ns_font["NSName"],
                "fontSize": ns_font["NSSize"],
                "kerning": 0,
                "lineHeight": None,
                "strikethrough": False,
                "underline": False,
            }
        )

    return formatted_data
