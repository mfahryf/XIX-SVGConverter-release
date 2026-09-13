"""
inkvn Reader

Reads Linearity Curve / Vectornator files and convert them into intermediate data.
"""

import logging
import zipfile
from typing import List

from packaging import version

import inkvn.reader.decode as d
import inkvn.reader.decode_vn as dvn
import inkvn.reader.extract as ext
from ..elements.artboard import VNArtboard


class CurveReader:
    """
    inkvn CurveReader

    A Linearity Curve / Vectornator file reader to convert Curve documents into dataclasses.
    """

    def __init__(self, stream):
        self.archive = zipfile.ZipFile(stream, "r")
        self.file_version: int = 44  # main support
        self.app_version: str
        self.artboards: List[VNArtboard] = []

        self.read()

    def read(self):
        manifest = ext.extract_manifest(self.archive)
        document = ext.extract_document(self.archive, manifest)
        drawing_data = ext.extract_drawing_data(document)

        units = drawing_data["settings"]["units"]
        self.app_version = document["appVersion"]
        self.file_version = manifest["fileFormatVersion"]
        artboard_paths = drawing_data["artboardPaths"]

        # different file versions have incompatible structure, reporting App version & File version greatly helps
        # inkex.utils.debug(f"App version: {self.app_version}, File version: {self.file_version}, File name: {self.archive.filename}")

        assert len(artboard_paths), "No artboard paths found in the document."

        # will be used later (as Inkscape attribute)
        # print(f"Unit: {units}")

        # Read Artboard (GUID JSON)
        for artboard_path in artboard_paths:
            gid_json = ext.extract_gid_json(self.archive, artboard_path)

            # if the file is Linearity Curve
            if self.check_if_curve(self.app_version):
                artboard = d.read_artboard(self.archive, gid_json)
                self.artboards.append(artboard)

            # if the file is Vectornator
            else:
                artboard = dvn.read_vn_artboard(self.archive, gid_json)
                self.artboards.append(artboard)

    @staticmethod
    def check_if_curve(input_version: str):
        """check if the app version is 5.x or not"""
        required_version = version.parse("5.1.0")
        try:
            current_version = version.parse(input_version)
        except version.InvalidVersion:
            logging.warning(f"Invalid version string: {input_version}")
            return False

        return current_version >= required_version
