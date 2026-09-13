#!/usr/bin/env python
# coding=utf-8
#
# Copyright (C) 2020 Ellen Wasboe, ellen@wasbo.net
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor,
# Boston, MA  02110-1301, USA.
"""
Clean up path by removing duplicate nodes and interpolate close nodes.

Optionally:
    join start and end node of each subpath if distance < threshold
    join separate subpaths if end nodes closer than threshold
Joining subpaths can be done either by interpolating or straight line segment.
"""

import numpy as np

import inkex
from inkex import bezier
from inkex.paths import Move, Curve, Line, ZoneClose, Path
from inkex.localization import inkex_gettext as _


def get_length_bez_segment(prev_xy, bez):
    """Get bezier segment length.

    Parameters
    ----------
    prev_xy : list of float
        first node
        [x, y]
    bez : list of float
        command to second node
        [p2,p3,p4] as returned by Curve.to_bez()

    Returns
    -------
    length : float
    """
    # csp [p3, p0, p1]
    csp1 = [prev_xy, prev_xy, bez[0]]
    csp2 = [bez[1], bez[2], bez[2]]
    length = bezier.cspseglength(csp1, csp2)

    return length


def finish_subpath(path, prev_bez):
    """Finish subpath if stop before threshold met."""
    last_added = path[-1]
    if len(last_added.args) == 6:
        x2, y2, x3, y3, __, __ = last_added.args
        # pos of end node, ctrl points of last added
        path.append(Curve(x2, y2, x3, y3, prev_bez[2][0], prev_bez[2][1]))
    else:
        if last_added.letter == "M":
            path = Path()  # remove zero length path

    return path


def remove_last_subpath(path):
    """Remove last subpath from list of PathCommands."""
    while len(path) > 0:
        cmd = path.pop()
        if cmd.letter == "M":
            break
    return path


def get_distance_xy(xy1, xy2):
    """Calculate distance between coordiates [x1,y1] and [x2,y2]."""
    xdiff = xy2[0] - xy1[0]
    ydiff = xy2[1] - xy1[1]
    distance = np.sqrt(np.add(np.power(xdiff, 2), np.power(ydiff, 2)))

    return distance


class CleanUpPath(inkex.EffectExtension):
    def add_arguments(self, pars):
        pars.add_argument("--name", default="options")
        pars.add_argument("--minlength", type=float, default=0)
        pars.add_argument("--minUse", type=inkex.Boolean, default=False)
        pars.add_argument("--minlengthSub", type=float, default=0)
        pars.add_argument("--minUseSub", type=inkex.Boolean, default=False)
        pars.add_argument("--maxdistClose", type=float, default=0)
        pars.add_argument("--closeSub", type=inkex.Boolean, default=False)
        pars.add_argument("--maxdistJoin", type=float, default=0)
        pars.add_argument("--joinEndSub", type=inkex.Boolean, default=False)
        pars.add_argument("--allow_reverse", type=inkex.Boolean, default=True)
        pars.add_argument("--option_join", default="1")

    def effect(self):
        """Clean up path."""
        self.min_length = self.options.minlength
        self.min_length_sub = self.options.minlengthSub
        self.max_dist_close = self.options.maxdistClose
        self.max_dist_join = self.options.maxdistJoin
        if self.options.minUse is False:
            self.min_length = 0
        if self.options.minUseSub is False:
            self.min_length_sub = 0
        if self.options.closeSub is False:
            self.max_dist_close = -1
        if self.options.joinEndSub is False:
            self.max_dist_join = -1

        nInkEffect = 0
        for elem in self.svg.selection.filter_nonzero(inkex.PathElement):
            thisIsPath = True
            if elem.get("inkscape:path-effect") is not None:
                thisIsPath = False
                nInkEffect += 1

            if thisIsPath:
                self.path_d = elem.path.to_absolute()
                # split into dict of subpaths
                self.subpaths_dict = {
                    "subpaths": [sub for sub in self.path_d.subpath_iterator()]
                }
                self.remove_minlength_nodes()  # to curves and fill subpaths_dict
                if self.max_dist_close > -1:
                    self.close_subpaths()
                if self.max_dist_join > -1:
                    self.join_subpaths()

                # join dict of subpaths
                path = []
                for sub in self.subpaths_dict["subpaths"]:
                    path.extend(sub)
                elem.path = Path(path)

        if nInkEffect > 0:
            inkex.errormsg(
                _(
                    f"""{nInkEffect} selected elements have an
                inkscape:path-effect applied. These elements will be
                ignored to avoid confusing results. Apply Paths->Object
                to path (Shift+Ctrl+C) and retry .""",
                )
            )

    def remove_minlength_nodes(self):
        """Remove node if curve-length < threshold and fill subpaths_dict info."""
        threshold = self.min_length
        self.subpaths_dict["open"] = []
        self.subpaths_dict["xy_start"] = []
        self.subpaths_dict["xy_end"] = []

        pop_sub_dicts = []  # ids to remove due to shorter path than threshold
        for subno, sub in enumerate(self.subpaths_dict["subpaths"]):
            new_sub = Path()
            summed_length = 0
            total_sub_length = 0
            sum_next = False
            prev_bez = []
            for cmd in sub.proxy_iterator():
                if cmd.letter == "M":  # only first element
                    new_sub.append(Move(*cmd.args))
                elif cmd.letter == "Z":  # last element if closed path
                    if sum_next:  # last segment of subpath not added still
                        new_sub = finish_subpath(new_sub, prev_bez)
                    new_sub.append(ZoneClose(*cmd.args))
                else:
                    c = cmd.to_curve()
                    prev = cmd.previous_end_point
                    length = get_length_bez_segment([prev.x, prev.y], c.to_bez())
                    total_sub_length += length

                    if sum_next is False:  # fresh start, irrelevant history
                        if length > threshold:
                            new_sub.append(Curve(*c.args))  # add as is
                        else:
                            # keep including segments until total length >= threshold
                            summed_length = length
                            sum_next = True
                    else:
                        summed_length += length

                    if summed_length >= threshold and sum_next:
                        if new_sub[-1].letter == "M":
                            if threshold > 0:
                                new_sub.append(Curve(*c.args))  # add as is keep start
                            else:
                                pass  # ignore
                        else:
                            last_added = new_sub[-1]
                            # end of group of segments - correct last added node p4
                            # if sum > threshold avg pos from last+prev, add current
                            # if summ = threshold avg pos from last+current node
                            x2, y2, x3, y3, x4, y4 = last_added.args
                            if summed_length > threshold:
                                prev_end = cmd.previous_end_point
                                edit_x4 = 0.5 * (prev_end.x + x4)
                                edit_y4 = 0.5 * (prev_end.y + y4)
                            else:
                                __, __, __, __, x_4, y_4 = c.args
                                edit_x4 = 0.5 * (x_4 + x4)
                                edit_y4 = 0.5 * (y_4 + y4)
                            new_sub[-1] = Curve(x2, y2, x3, y3, edit_x4, edit_y4)
                            if summed_length > threshold:
                                new_sub.append(Curve(*c.args))

                        sum_next = False
                        summed_length = 0
                    prev_bez = c.to_bez()

            if sum_next:  # last segment of last subpath not added still
                new_sub = finish_subpath(new_sub, prev_bez)
            if total_sub_length > self.min_length_sub:
                self.subpaths_dict["subpaths"][subno] = new_sub
                if isinstance(new_sub[-1], ZoneClose):
                    self.subpaths_dict["open"].append(False)
                    last_args = new_sub[-2].args
                else:
                    self.subpaths_dict["open"].append(True)
                    last_args = new_sub[-1].args
                self.subpaths_dict["xy_start"].append(new_sub[0].args)
                self.subpaths_dict["xy_end"].append(last_args[-2:])
            else:
                pop_sub_dicts.append(subno)

        if len(pop_sub_dicts) > 0:
            pop_sub_dicts.sort(reverse=True)
            for no in pop_sub_dicts:
                self.subpaths_dict["subpaths"].pop(no)

    def close_subpaths(self):
        """Join end nodes of each subpath by interpolating coordinates."""
        for s, sub in enumerate(self.subpaths_dict["subpaths"]):
            if self.subpaths_dict["open"][s]:
                xy_start = self.subpaths_dict["xy_start"][s]
                xy_end = self.subpaths_dict["xy_end"][s]
                dist = get_distance_xy(xy_start, xy_end)
                if dist < self.max_dist_close:
                    avg_x = 0.5 * (xy_start[0] + xy_end[0])
                    avg_y = 0.5 * (xy_start[1] + xy_end[1])
                    sub[0] = Move(avg_x, avg_y)
                    end_cmd = sub[-1]
                    x2, y2, x3, y3, __, __ = end_cmd.args
                    sub[-1] = Curve(x2, y2, x3, y3, avg_x, avg_y)
                    sub.append(ZoneClose())
                    self.subpaths_dict["xy_start"][s] = [avg_x, avg_y]
                    self.subpaths_dict["xy_end"][s] = [avg_x, avg_y]
                    self.subpaths_dict["open"][s] = False
                    self.subpaths_dict["subpaths"][s] = sub

    def find_join_matches(self):
        """Find which subpath to join to which and to which end."""

        def find_id_min_dist(subno, join_to, xy_this, xy_all):
            id_min_dist = None
            min_distance = self.max_dist_join
            ids_test = np.where(join_to == -1)
            # avoid join to self
            id2test = np.delete(ids_test[0], np.where(ids_test[0] == subno))
            if id2test.size > 0:
                # calculate distances in x/y direction
                # find shortest distance if less than minimum
                xy_search = xy_all[id2test]
                xdiff = np.subtract(xy_search[:, 0], xy_this[0])
                ydiff = np.subtract(xy_search[:, 1], xy_this[1])
                distance = np.sqrt(np.add(np.power(xdiff, 2), np.power(ydiff, 2)))
                min_distance = np.nanmin(distance)
                if min_distance < self.max_dist_join:
                    id_min_list = np.where(distance == min_distance)
                    id_min = id_min_list[0]
                    id_min_dist = id2test[id_min[0]]

            return (id_min_dist, min_distance)

        def join_to(subno, j_to1, j_to2, xy1, xy2):
            id_join = None
            reverse = False
            if j_to1[subno] == -1:  # not joined yet
                xy_this = xy1[subno]
                id_join, min_distance = find_id_min_dist(subno, j_to2, xy_this, xy2)
                if self.options.allow_reverse:
                    id_join_r, min_dist_r = find_id_min_dist(subno, j_to1, xy_this, xy1)
                    if id_join_r is not None:
                        if id_join is not None:
                            if min_dist_r < min_distance:
                                reverse = True
                        else:
                            reverse = True
                    if reverse:
                        min_distance = min_dist_r
                        id_join = id_join_r

            return (id_join, reverse)

        def switch_start_end(xy_start, xy_end, id_join):
            x, y = xy_start[id_join]
            xy_start[id_join] = xy_end[id_join]
            xy_end[id_join] = [x, y]
            return (xy_start, xy_end)

        nSub = len(self.subpaths_dict["subpaths"])
        # initiate arrays
        j_end2 = np.full(nSub, -1)  # sub start index to join
        j_start2 = np.copy(j_end2)
        j_end2end = np.zeros(nSub, dtype=bool)
        j_start2start = np.zeros(nSub, dtype=bool)
        ids_closed = []
        xy_start = self.subpaths_dict["xy_start"]
        xy_end = self.subpaths_dict["xy_end"]
        for s in range(nSub):
            if self.subpaths_dict["open"][s] is False:
                ids_closed.append(s)
                xy_start[s] = [np.nan, np.nan]
                xy_end[s] = [np.nan, np.nan]
        if len(ids_closed) > 0:
            ids_closed = np.array(ids_closed)
            j_end2[ids_closed] = -2  # avoid searching these
            j_start2[ids_closed] = -2
        xy_start = np.array(xy_start)
        xy_end = np.array(xy_end)

        for s in range(nSub):
            # end of current to start of other?
            id_join, reverse = join_to(s, j_end2, j_start2, xy_end, xy_start)
            if id_join is not None:
                j_end2[s] = id_join
                if reverse:
                    j_end2[id_join] = s
                    j_end2end[s] = True
                    j_end2end[id_join] = True
                else:
                    j_start2[id_join] = s

            # start of current to end of other?
            id_join, reverse = join_to(s, j_start2, j_end2, xy_start, xy_end)
            if id_join is not None:
                j_start2[s] = id_join
                if reverse:
                    j_start2[id_join] = s
                    j_start2start[s] = True
                    j_start2start[id_join] = True
                else:
                    j_end2[id_join] = s

        self.subpaths_dict["join_start2"] = j_start2
        self.subpaths_dict["join_end2"] = j_end2
        self.subpaths_dict["join_start2start"] = j_start2start
        self.subpaths_dict["join_end2end"] = j_end2end

    def join_sub(self, sub1, sub2):
        """Join Path() by interpolation or straight line segment."""
        if self.options.option_join == "1":
            # generate new interpolated join node from end/start nodes
            x2, y2, x3, y3, x_end, y_end = sub1[-1].args
            x_start, y_start = sub2[0].args
            join_x, join_y = 0.5 * (x_end + x_start), 0.5 * (y_end + y_start)
            # remove end/start + input new joined node
            sub1[-1] = Curve(x2, y2, x3, y3, join_x, join_y)
            sub2.pop(0)  # pop M
        elif self.options.option_join == "2":
            # straight line
            x, y = sub2[0].args
            sub2[0] = Line(x, y)

        new_sub = sub1 + sub2

        return new_sub

    def join_subpaths(self):
        """Join different subpaths where end nodes closer than threshold."""

        def join_sub(id_this, end=True):
            # join one by one until -1 or back to s (closed)
            close_this = False
            sub_this = self.subpaths_dict["subpaths"][id_this]
            if end:
                id_join = self.subpaths_dict["join_end2"][id_this]
                reverse = self.subpaths_dict["join_end2end"][id_this]
            else:
                id_join = self.subpaths_dict["join_start2"][id_this]
                reverse = self.subpaths_dict["join_start2start"][id_this]
            sub_join = self.subpaths_dict["subpaths"][id_join]
            if reverse:
                sub_join = sub_join.reverse()
            if end:
                joined_sub = self.join_sub(sub_this, sub_join)
            else:
                joined_sub = self.join_sub(sub_join, sub_this)
            moved_to[id_join] = id_this
            prev = id_this
            # continue if id_join joined to more
            if (
                self.subpaths_dict["join_end2"][id_join] > -1
                and self.subpaths_dict["join_start2"][id_join] > -1
            ):
                # already joined so both joined if continue
                proceed = True
                while proceed:
                    if self.subpaths_dict["join_end2"][id_join] != prev:
                        id_next = self.subpaths_dict["join_end2"][id_join]
                        reverse_join = self.subpaths_dict["join_end2end"][id_join]
                    else:
                        id_next = self.subpaths_dict["join_start2"][id_join]
                        reverse_join = self.subpaths_dict["join_start2start"][id_join]
                    if reverse_join:
                        reverse = not reverse
                    if moved_to[id_next] == id_this:
                        close_this = True
                        proceed = False
                    else:
                        sub_join = self.subpaths_dict["subpaths"][id_next]
                        if reverse:
                            sub_join = sub_join.reverse()
                        if end:
                            joined_sub = self.join_sub(joined_sub, sub_join)
                        else:
                            joined_sub = self.join_sub(sub_join, joined_sub)
                        moved_to[id_next] = id_this
                        if (
                            self.subpaths_dict["join_end2"][id_next] > -1
                            and self.subpaths_dict["join_start2"][id_next] > -1
                        ):
                            prev = id_join
                            id_join = id_next
                        else:
                            proceed = False
            self.subpaths_dict["subpaths"][s] = joined_sub

            return close_this

        if self.subpaths_dict["open"].count(True) > 1:  # any subpaths to join
            # fill logics arrays
            self.find_join_matches()

            # fill new_path according to logics
            n_sub = len(self.subpaths_dict["subpaths"])
            moved_to = np.arange(n_sub)
            for s in range(n_sub):
                if moved_to[s] == s:  # not joined yet
                    close_this = False
                    if self.subpaths_dict["join_end2"][s] > -1:
                        close_this = join_sub(s, end=True)

                    if (
                        self.subpaths_dict["join_start2"][s] > -1
                        and close_this is False
                    ):
                        close_this = join_sub(s, end=False)

                    # close the new subpath if start/end node is closer than maxdist
                    if close_this:
                        this_sub = self.subpaths_dict["subpaths"][s]
                        if self.options.option_join == "1":
                            x_start, y_start = this_sub[0].args
                            x2, y2, x3, y3, x_end, y_end = this_sub[-1].args
                            join_x = 0.5 * (x_start + x_end)
                            join_y = 0.5 * (y_start + y_end)
                            this_sub[0] = Move(join_x, join_y)
                            this_sub[-1] = Curve(x2, y2, x3, y3, join_x, join_y)
                        this_sub.append(ZoneClose())

        # remove all subpaths that are joined to others
        remove_ids = []
        for subno, m_to in enumerate(moved_to):
            if m_to != subno:
                remove_ids.append(subno)
        remove_ids.sort(reverse=True)
        for rem_no in remove_ids:
            self.subpaths_dict["subpaths"].pop(rem_no)


if __name__ == "__main__":
    CleanUpPath().run()
