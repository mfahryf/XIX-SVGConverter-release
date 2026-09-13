#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2024 Manpreet Singh <manpreet.singh.dev@proton.me>
#
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from io import BytesIO
import math
import os
from typing import Optional, Union

from PIL import Image
import numpy as np

from inkaf.parser.types import AFObject
from inkaf.parser.extract import AFExtractor
from inkaf.utils import extract


@dataclass
class AFBitmap:
    format: int
    width: int
    height: int
    emb_file: Optional[Union[str, AFObject]]

    @classmethod
    def from_af(cls, child: AFObject) -> AFBitmap:
        if "Bckg" in child:
            bckg = child.get("Bckg")
        else:
            bckg = None

        # TODO:
        # icc_profiles: dict = child["Prof"]
        # rgb_profile: bytes = icc_profiles["RGBP"]
        # rgu_profile: bytes = icc_profiles["RGUP"]
        # cmyk_profile: bytes = icc_profiles["CMYP"]
        # ia_profile: bytes = icc_profiles["IAPr"]
        # lab_profile: bytes = icc_profiles["LABP"]
        # dela=child["DelA"],  # TODO: What is this?

        return AFBitmap(
            format=child["Frmt"].id,
            emb_file=bckg.data if bckg else child,
            width=child["BmpW"],
            height=child["BmpH"],
        )

    @classmethod
    def bitmap_from_blocks(cls, child: AFObject, extractor: AFExtractor):
        """Assemble a bitmap from 256² blocks"""
        size = (child["BmpH"], child["BmpW"])
        components = []
        for component in range(1, 5):
            if f"TWi{component}" in child:
                block_layout = (child[f"THi{component}"], child[f"TWi{component}"])
            else:
                block_layout = (math.ceil(size[0] / 256), math.ceil(size[1] / 256))

            data = np.zeros(
                (256 * block_layout[0], 256 * block_layout[1]), dtype=np.uint8
            )
            block_types = child[f"Sta{component}"]
            j = 0
            for i, block in enumerate(block_types):
                col = i % block_layout[1]
                row = i // block_layout[1]
                if block == 0:
                    blkdata = np.zeros((256, 256), dtype=np.uint8)
                elif block == 1:
                    blkdata = np.zeros((256, 256), dtype=np.uint8)
                elif block == 2:
                    blkdata = (np.ones((256, 256), dtype=np.uint8) * 255).astype(
                        np.uint8
                    )
                elif block == 4:
                    block = child[f"Idx{component}"][j]
                    assert block.get_type() == "Blck"
                    blkdata = np.frombuffer(
                        extract(extractor, block["Data"].data), dtype=np.uint8
                    ).reshape(256, 256)
                    j += 1
                else:
                    raise RuntimeError(f"Unknown block type {block}")
                data[row * 256 : (row + 1) * 256, col * 256 : (col + 1) * 256] = blkdata
            assert j == len(child[f"Idx{component}"])
            components.append(data[0 : size[0], 0 : size[1]])
        imagefile = BytesIO()
        image = Image.fromarray(np.stack(components, axis=-1), "RGBA")
        image.save(imagefile, format="PNG")
        imagefile.seek(0)
        return imagefile.read()


class AFImageTypes(Enum):
    JPEG = 0
    PNG = 1
    TIFF = 2
    WebP = 3
    HD_Photo = 4
    GIF = 6
    JPEG_2000 = 7
    BMP = 8
    OpenEXR = 10
    HDR = 11  # https://en.wikipedia.org/wiki/RGBE_image_format
    HEIF = 14
    JXL = 16  # file_type_str = plugin


media_types = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "TIFF": "image/tiff",
    "WebP": "image/webp",
    "HD_Photo": "image/jxr",
    "GIF": "image/gif",
    "JPEG_2000": "image/jp2",
    "BMP": "image/bmp",
    "HEIF": "image/heif",
    "JXL": "image/jxl",
}


@dataclass
class AFImage:
    bitmap: AFBitmap

    file_path_abs: Optional[str]
    file_path_rel: Optional[str]
    file_size: int
    file_type_enum: Optional[AFImageTypes]
    file_type_str: Optional[str]

    @property
    def media_type(self) -> Optional[str]:
        if self.file_type_enum is not None and self.file_type_enum.name in media_types:
            return media_types[self.file_type_enum.name]

        if self.file_type_str in media_types:
            return media_types[self.file_type_str]

        path = self.file_path_rel or self.file_path_abs
        if path:
            file_ext = os.path.splitext(path)[1][1:].lower()
            for k, v in media_types.items():
                if file_ext == k.lower() or file_ext == v.split("/")[1]:
                    return v
        return None

    @classmethod
    def from_af(cls, child: AFObject) -> AFImage:
        assert child.get_type() in ("ImgN", "Rstr"), "Not an image"
        # modification_time=child["MfTm"],
        # file_name=child["IRFN"],
        return cls(
            AFBitmap.from_af(child["Bitm"]),
            file_path_abs=(child.get("IRDS") or {}).get("Filn"),
            file_path_rel=(child.get("RPth") or {}).get("Path"),
            file_size=child.get("Flsz"),
            file_type_enum=AFImageTypes(child["FTyp"].id)
            if "FTyp" in child and child["FTyp"].id in [t.value for t in AFImageTypes]
            else None,
            file_type_str=child.get("FTyN"),
        )
