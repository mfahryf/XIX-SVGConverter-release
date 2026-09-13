"""
inkvn (extension-curve) (2025/1/21)

description: Linearity Curve / Vectornator file importer for Inkscape

! what DOESN'T work (2025/03/15): textOnPath, grid, brush stroke, marker(arrow)
! and other features I missed
"""

import os
import sys

import inkex

from .reader.read import CurveReader
from .svg.convert import CurveConverter


HERE = os.path.dirname(__file__) or "."
# This is suggested by https://docs.python-guide.org/writing/structure/.
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))


class CurveInput(inkex.InputExtension):
    """Open and convert .curve / .vectornator files."""

    # copied from inkaf
    def load(self, stream):
        converter = CurveConverter()
        converter.convert(CurveReader(stream))
        return converter.doc


def main():
    CurveInput().run()
