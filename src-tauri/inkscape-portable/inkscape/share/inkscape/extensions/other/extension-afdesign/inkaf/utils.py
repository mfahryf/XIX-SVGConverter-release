# SPDX-FileCopyrightText: 2023 Software Freedom Conservancy <info@sfconservancy.org>
#
# SPDX-License-Identifier: GPL-2.0-or-later
"""Commonly used functions."""

from inkaf.parser.extract import AFExtractor, NormalFATFile
from inkaf.parser.parse import AFParser
from inkaf.parser.types import Document
from lxml import etree
from io import BytesIO


def to_pretty_xml(xml_string: bytes) -> bytes:
    """Return a pretty xml string with newlines and indentation."""
    # from https://stackoverflow.com/a/3974112/1320237
    # and https://stackoverflow.com/a/9612463/1320237
    parser = etree.XMLParser(remove_blank_text=True)
    file = BytesIO()
    file.write(xml_string)
    file.seek(0)
    element = etree.parse(file, parser)
    return etree.tostring(element, pretty_print=True)


def extract(extractor: AFExtractor, doc: str = "doc.dat") -> bytes:
    assert extractor.content is not None, (
        f"Extractor content not initialized. '{doc}' extraction failed."
    )

    if doc not in extractor.content.files:
        revision = extractor.get_head_revision(doc)
        if revision is None:
            raise KeyError(doc)
        if not isinstance(revision, NormalFATFile):
            raise TypeError(f"Unexpected revision type: {type(revision)}")
        if revision.compressed_size == 0:
            raise RuntimeError(f"Empty revision: {revision}")

        file_content = extractor.extract(revision)

        assert extractor.content is not None
        extractor.content.files[doc] = file_content

    return extractor.content.files[doc]


def parse(extractor: AFExtractor, doc: str = "doc.dat") -> Document:
    file_content = extract(extractor, doc)
    result = AFParser(BytesIO(file_content))
    return result.parse()


class DuplicateFilter(object):
    def __init__(self):
        self.msgs = set()

    def filter(self, record):
        rv = record.msg not in self.msgs
        self.msgs.add(record.msg)
        return rv


__all__ = ["to_pretty_xml", "parse", "DuplicateFilter"]
