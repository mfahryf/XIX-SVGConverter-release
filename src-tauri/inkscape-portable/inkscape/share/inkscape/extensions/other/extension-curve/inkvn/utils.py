# This file is derived from
# https://github.com/avibrazil/NSKeyedUnArchiver
# License: LGPL3

import copy
import datetime
import plistlib
import struct
from typing import Dict, List


def _unserialize(
    o: dict, serialized: dict, removeClassName: bool, plist_top: bool = True
):
    if plist_top:
        reassembled = copy.deepcopy(o)
    else:
        reassembled = o

    finished = False
    while not finished:
        finished = True

        cursor = None
        if isinstance(reassembled, bytes):
            try:
                return NSKeyedUnarchiver(reassembled)
            except:
                # Not plist data, just plain binary
                return reassembled
        elif isinstance(reassembled, dict):
            cursor = reassembled.keys()
        elif isinstance(reassembled, list):
            cursor = range(len(reassembled))
        else:  # str, int etc
            print("reassembled is a " + str(type(reassembled)) + ":" + str(reassembled))
            return reassembled

        for k in cursor:
            #  print(f"cursor={k}")
            # UIDs references items in "serialized" ($objects)
            if isinstance(reassembled[k], plistlib.UID):
                reassembled[k] = copy.deepcopy(serialized[reassembled[k].data])

                # $null gets replaced by None
                if str(reassembled[k]) == "$null":
                    reassembled[k] = None

                finished = False

            elif isinstance(reassembled[k], dict) or isinstance(reassembled[k], list):
                reassembled[k] = _unserialize(
                    reassembled[k], serialized, removeClassName, plist_top=False
                )

                if (
                    "$class" in reassembled[k]
                    and "$classes" in reassembled[k]["$class"]
                ):
                    # Specialized handler for common class types
                    if "NSArray" in reassembled[k]["$class"]["$classes"]:
                        reassembled[k] = reassembled[k]["NS.objects"]

                    elif any(
                        c in reassembled[k]["$class"]["$classes"]
                        for c in ["NSMutableDictionary", "NSDictionary"]
                    ):
                        reassembled[k] = dict(
                            zip(reassembled[k]["NS.keys"], reassembled[k]["NS.objects"])
                        )

                    elif any(
                        c in reassembled[k]["$class"]["$classes"]
                        for c in ["NSMutableString", "NSString"]
                    ):
                        reassembled[k] = reassembled[k]["NS.string"]

                    elif any(
                        c in reassembled[k]["$class"]["$classes"]
                        for c in ["NSMutableData", "NSData"]
                    ):
                        reassembled[k] = reassembled[k]["NS.data"]

                    elif "NSDate" in reassembled[k]["$class"]["$classes"]:
                        apple2001reference = datetime.datetime(
                            2001, 1, 1, tzinfo=datetime.timezone.utc
                        )
                        reassembled[k] = datetime.datetime.fromtimestamp(
                            reassembled[k]["NS.time"] + apple2001reference.timestamp(),
                            datetime.timezone.utc,
                        )
                    if removeClassName and isinstance(reassembled[k], dict):
                        # Remove visual polution
                        if "$class" in reassembled[k]:  # check if it's there
                            del reassembled[k]["$class"]

                finished = True
            else:
                # strings will remain unchanged.
                pass

    return reassembled


def _decode_attrib_info(attr_bytes: bytes) -> List[Dict]:
    """
    Tries to decode NSAttributeInfo in KeyedArchived NSAttributedString.
    """
    # FIXME decode_attrib_info(legacy text) does not work for longer texts
    attr_data = []
    offset = 0
    while offset < len(attr_bytes):
        # 2bytes()
        if offset + 2 <= len(attr_bytes):
            length, attr_id = struct.unpack_from("<BB", attr_bytes, offset)
            attr_data.append(
                {
                    "length": length,
                    "attribute_id": attr_id,
                }
            )
            offset += 2
        else:
            break

    return attr_data


def NSKeyedUnarchiver(plist, removeClassName=True):
    """
    plist can be:
    • bytes      ⟹ pass it through plistlib.loads()
    • dict       ⟹ unserialize
    """
    # removed other formats as they are not needed

    if isinstance(plist, bytes):
        plistdata = plistlib.loads(plist)
    elif isinstance(plist, dict):
        # plist is already a plistlib-parsed dict
        plistdata = plist
    else:
        raise TypeError(
            "Trying to plist-parse something that is neither a PurePath, file name, XML text, plist bytes stream nor a dict."
        )

    if "$top" in plistdata:
        o = copy.deepcopy(plistdata["$top"])
        unserialized = _unserialize(o, plistdata["$objects"], removeClassName)
    else:
        raise TypeError("Passed object is not an NSKeyedArchiver")

    if len(unserialized) == 1 and "root" in unserialized:
        # Unserialized data contains only 1 object, so no need to nest it under 'root'
        unserialized = unserialized["root"]

    if unserialized.get("NSAttributeInfo") is not None:
        attr_info = unserialized["NSAttributeInfo"]
        attr_bytes = attr_info if isinstance(attr_info, bytes) else bytes(attr_info)
        unserialized["NSAttributeInfo"] = _decode_attrib_info(attr_bytes)

    return unserialized
