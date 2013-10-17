import math
import json
import struct
import time
from __init__ import *

class weighted_storage_cube:
    cube = None
    order = ["latitude", "longitude", "altitude", "time", "accuracy"]
    expire = None
    def __init__(self, expire = 86400 * 30):
        self.expire = expire
        self.cube = {
            "latitude": [0, 0, 0],
            "longitude": [0, 0, 0],
            "altitude": [100000, 0, -100000],
            "time": [0, 0, 0],
            "accuracy": [40000000, 0, 0],
            "count": 0
        }
    def add_point(self, point):
        point = {
            "latitude": point.get("latitude", point.get("lat")),
            "longitude": point.get("longitude", point.get("lon")),
            "altitude": point.get("altitude", point.get("alt")),
            "time": point.get("time", point.get("timestamp", time.time())),
            "accuracy": point.get("accuracy", point.get("acc", 90))
        }
        # timestamp filtering - we don't need old points
        if self.cube["count"]:
            if point["time"] and (point["time"] + self.expire) < self.cube["time"][0]:
                return False
            if point["time"] and (point["time"] - self.expire) > self.cube["time"][2]:
                self.cube["count"] = 0 # reset cube with new point
        for k,v in point.iteritems():
            if v:
                v = float(v)
                if not self.cube["count"]:
                    self.cube[k] = [v, v, v]
                elif self.cube[k][2] < self.cube[k][0]:
                    self.cube[k] = [v, v, v]
                else:
                    self.cube[k] = [min(v, self.cube[k][0]),
                               (self.cube[k][1] * self.cube["count"] + v) / (self.cube["count"] + 1),
                               max(v, self.cube[k][0])]
        self.cube["count"] += 1
        return True
    def get_average(self):
        return {
            "altitude": self.cube["altitude"][1], 
            "longitude": self.cube["longitude"][1], 
            "time": self.cube["time"][1], 
            "latitude": self.cube["latitude"][1],
            "accuracy": max(
                self.cube["accuracy"][1], 
                Distance((self.cube["longitude"][0], self.cube["latitude"][0]), (self.cube["longitude"][2], self.cube["latitude"][2]))/2),
            "count": self.cube["count"]
            }
    def get_bbox(self):
        return [self.cube["longitude"][0], self.cube["latitude"][0], self.cube["longitude"][2], self.cube["latitude"][2]]
    def is_valid(self, timestamp = None):
        if not timestamp:
            timestamp = time.time()
        return self.cube["count"] and (self.cube["time"][2] + self.expire) > timestamp and (self.cube["time"][0] - self.expire) < timestamp
    def dumps(self):
        dump = struct.pack("!L", self.cube["count"])
        for k in self.order:
            dump += struct.pack("!dff", self.cube[k][0], self.cube[k][1] - self.cube[k][0], self.cube[k][1] - self.cube[k][0])
      #  print len(dump)
        return dump
    def loads(self, string, offset = 0):
        self.cube["count"] = struct.unpack_from("!L", string)[0]
        offset += 4
        for k in self.order:
            (smin, savg, smax) = struct.unpack_from("!dff", string, offset)
            offset += struct.calcsize("!dff")
            self.cube[k] = [smin, smin + savg, smin + smax]
        return offset
    def __repr__(self):
        return repr(self.cube)

class shelf:
    expire = None
    zoom = 14
    shelf = {}
    order = ["latitude", "longitude", "altitude", "time", "accuracy"]

    def __init__(self, expire = 86400 * 30, zoom = 14):
        self.expire = expire
        self.zoom = 14
        self.shelf = {}

    def add_point(self, point):
        lat = point.get("latitude", point.get("latitude"))
        lon = point.get("longitude", point.get("longitude"))
        tile = tile_number(lon, lat, self.zoom)
        self.sweep()
        if tile not in self.shelf:
            self.shelf[tile] = weighted_storage_cube(expire = self.expire)
        self.shelf[tile].add_point(point)

    def sweep(self):
        if self.shelf:
            for k, v in self.shelf.items():
                if not v.is_valid():
                    del self.shelf[k]

    def get_average(self):
        if not self.shelf:
            return False
        avg = {}
        bbox = [999,999,-999,-999]
        for cube in self.shelf.itervalues():
            cube_avg = cube.get_average()
            cube_box = cube.get_bbox()
            bbox = [min(bbox[0], cube_box[0]), min(bbox[1], cube_box[1]), max(bbox[2], cube_box[2]), max(bbox[3], cube_box[3])]
            for item in self.order:
                avg[item] = ((avg.get(item, 0) * avg.get("count", 0)) + (cube_avg[item] * cube_avg["count"])) / (avg.get("count", 0) + cube_avg["count"])
            avg["count"] = avg.get("count", 0) + cube_avg["count"]
        avg["accuracy"] = max(avg.get("accuracy", 10), Distance((bbox[0], bbox[1]), (bbox[2], bbox[3]))/2)
        return avg
    def dumps(self):
        return "".join([cube.dumps() for cube in self.shelf.itervalues()])
    def loads(self, string):
        if len(string) < 84:
            try:
                lon, lat = struct.unpack("!dd", string)
                self.add_point({"latitude": lat, "longitude": lon, "timestamp": 0})
                return
            except struct.error:
                return
        offset = 0
        while offset < len(string):
            cube = weighted_storage_cube(expire = self.expire)
            offset = cube.loads(string, offset)
            center = cube.get_average()
            tile = tile_number(center["longitude"], center["latitude"], self.zoom)
            self.shelf[tile] = cube

if __name__ == "__main__":
    print {"altitude": 52.5, "longitude": 121.51784396, "time": 1381266175, "latitude": 25.00435646, "speed": 0.0, "accuracy": 33.0}
    cube = weighted_storage_cube()
    cube = shelf()
    cube.add_point({"altitude": 52.5, "longitude": 121.51784396, "time": 1381266175, "latitude": 25.00435646, "speed": 0.0, "accuracy": 33.0})
    cube.add_point({"altitude": 62.29999923706055, "longitude": 121.44472196, "time": 1381266174, "latitude": 25.00428507, "speed": 0.0, "accuracy": 5.0})
    print cube
    print cube.get_average()
    print cube.loads(cube.dumps())
    print cube.get_average()
    print cube.is_valid()