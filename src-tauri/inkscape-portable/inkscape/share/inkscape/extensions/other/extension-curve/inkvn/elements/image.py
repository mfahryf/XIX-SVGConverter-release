"""
VNImageElement
"""

import base64
from dataclasses import dataclass
from io import BytesIO
from typing import List, Optional, Tuple, Union

import inkex
from PIL import Image

from .base import VNBaseElement


@dataclass
class VNImageElement(VNBaseElement):
    """
    Holds imageData as base64 texts.

    transform contains matrix (old format).
    """

    imageData: str
    transform: Optional[List[float]]
    cropRect: Optional[Tuple[Tuple[float, float], Tuple[float, float]]]

    def image_format(self) -> str:
        """Detect the image format of b64 encoded image."""
        binary_data = base64.b64decode(self.imageData)
        image = Image.open(BytesIO(binary_data))
        return image.format

    def image_dimension(self) -> Tuple[int, int]:
        """Detect the dimension format of b64 encoded image."""
        binary_data = base64.b64decode(self.imageData)
        image = Image.open(BytesIO(binary_data))
        return image.width, image.height

    def convert_crop_rect(self) -> Union[inkex.Rectangle, None]:
        if self.cropRect is not None:
            width, height = self.cropRect[1]
            x, y = self.cropRect[0]

            clip_rect = inkex.Rectangle.new(x, y, width, height)
            clip_rect.label = f"{self.name}_crop"
            return clip_rect
