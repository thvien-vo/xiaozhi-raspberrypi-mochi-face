#!/usr/bin/env python3
"""Build a simplified 3D model of the pan/tilt robot (fallback, not used by default).

The web panel loads web_assets/xiaozhi_robot.glb, the assembled CAD model. This script
writes web_assets/xiaozhi_robot_simple.glb unless given another path; copy it over
xiaozhi_robot.glb only if the CAD model is missing. Its pivots carry glTF extras
{"sign": ...} because its tilt axis turns the other way (+tilt = rotation.x +).

The printable parts (robot_xiaozhi_v8 STL set) are laid out for printing, not assembled,
so this model is made of simple shapes with the real outer dimensions (mm):
base 220 x 160 x 60, head 244 x 147 x 46, pan axis at the center of the base top, tilt
axis through the middle of the head.

Node tree (names are what the web panel looks for):

    robot              rotation -90 deg about X: design Z-up -> three.js Y-up
      base             fixed
      pan_pivot        on the pan axis; rotation.z = +pan (robot turns to its own left)
        turn           turntable
        upper          two arms, tilt servo bracket, 15T servo gear
        tilt_pivot     on the tilt axis; rotation.x = +tilt (head looks up)
          head         shell, screen frame, eyes, webcam, cable duct, 30T gear

Design axes: X = robot's right, Y = forward (where the face looks), Z = up. In three.js
the robot therefore looks toward -Z. Run with no arguments to rewrite the file.
"""

from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "web_assets" / "xiaozhi_robot_simple.glb"

BASE_TOP = 60.0
TURN_H = 10.0
TILT_Z = 185.0  # Tilt axis height above the table
HEAD_W, HEAD_D, HEAD_H = 244.0, 46.0, 147.0
ARM_X = HEAD_W / 2 + 14  # Arm center, just outside the head


# ----- Geometry (numpy, each returns positions, normals, indices) -----

def box(size, center=(0, 0, 0)):
    sx, sy, sz = (s / 2 for s in size)
    cx, cy, cz = center
    faces = [
        ((1, 0, 0), [(sx, -sy, -sz), (sx, sy, -sz), (sx, sy, sz), (sx, -sy, sz)]),
        ((-1, 0, 0), [(-sx, sy, -sz), (-sx, -sy, -sz), (-sx, -sy, sz), (-sx, sy, sz)]),
        ((0, 1, 0), [(sx, sy, -sz), (-sx, sy, -sz), (-sx, sy, sz), (sx, sy, sz)]),
        ((0, -1, 0), [(-sx, -sy, -sz), (sx, -sy, -sz), (sx, -sy, sz), (-sx, -sy, sz)]),
        ((0, 0, 1), [(-sx, -sy, sz), (sx, -sy, sz), (sx, sy, sz), (-sx, sy, sz)]),
        ((0, 0, -1), [(-sx, sy, -sz), (sx, sy, -sz), (sx, -sy, -sz), (-sx, -sy, -sz)]),
    ]
    pos, nrm, idx = [], [], []
    for n, quad in faces:
        base = len(pos)
        for p in quad:
            pos.append((p[0] + cx, p[1] + cy, p[2] + cz))
            nrm.append(n)
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    return np.array(pos, np.float32), np.array(nrm, np.float32), np.array(idx, np.uint32)


def rounded_box(size, radius, center=(0, 0, 0), segments=6):
    """Box rounded around its Y axis (front/back faces are flat rounded rectangles)."""
    sx, sy, sz = (s / 2 for s in size)
    r = min(radius, sx, sz)
    outline = []
    for cx_, cz_, start in ((sx - r, sz - r, 0), (-sx + r, sz - r, 90), (-sx + r, -sz + r, 180), (sx - r, -sz + r, 270)):
        for i in range(segments + 1):
            a = math.radians(start + 90 * i / segments)
            outline.append((cx_ + r * math.cos(a), cz_ + r * math.sin(a)))
    n = len(outline)
    pos, nrm, idx = [], [], []
    # Sides
    for i in range(n):
        x0, z0 = outline[i]
        x1, z1 = outline[(i + 1) % n]
        ex, ez = x1 - x0, z1 - z0
        length = math.hypot(ex, ez) or 1.0
        normal = (ez / length, 0.0, -ex / length)
        base = len(pos)
        for x, y, z in ((x0, -sy, z0), (x1, -sy, z1), (x1, sy, z1), (x0, sy, z0)):
            pos.append((x, y, z))
            nrm.append(normal)
        idx += [base, base + 2, base + 1, base, base + 3, base + 2]
    # Front (+Y) and back (-Y) caps as fans
    for y, ny in ((sy, 1.0), (-sy, -1.0)):
        center_index = len(pos)
        pos.append((0.0, y, 0.0))
        nrm.append((0.0, ny, 0.0))
        for x, z in outline:
            pos.append((x, y, z))
            nrm.append((0.0, ny, 0.0))
        for i in range(n):
            a, b = center_index + 1 + i, center_index + 1 + (i + 1) % n
            idx += [center_index, b, a] if ny > 0 else [center_index, a, b]
    p = np.array(pos, np.float32) + np.array(center, np.float32)
    return p, np.array(nrm, np.float32), np.array(idx, np.uint32)


def cylinder(radius, height, center=(0, 0, 0), axis="z", segments=32, teeth=0, tooth=0.0):
    """Closed cylinder; teeth > 0 gives it a gear-like outline."""
    count = max(segments, teeth * 2)
    ring = []
    for i in range(count):
        a = 2 * math.pi * i / count
        r = radius + (tooth if teeth and i % 2 == 0 else 0.0)
        ring.append((r * math.cos(a), r * math.sin(a)))
    h = height / 2
    pos, nrm, idx = [], [], []
    for i in range(count):
        (x0, y0), (x1, y1) = ring[i], ring[(i + 1) % count]
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        length = math.hypot(mx, my) or 1.0
        normal = (mx / length, my / length, 0.0)
        base = len(pos)
        for x, y, z in ((x0, y0, -h), (x1, y1, -h), (x1, y1, h), (x0, y0, h)):
            pos.append((x, y, z))
            nrm.append(normal)
        idx += [base, base + 1, base + 2, base, base + 2, base + 3]
    for z, nz in ((h, 1.0), (-h, -1.0)):
        c = len(pos)
        pos.append((0.0, 0.0, z))
        nrm.append((0.0, 0.0, nz))
        for x, y in ring:
            pos.append((x, y, z))
            nrm.append((0.0, 0.0, nz))
        for i in range(count):
            a, b = c + 1 + i, c + 1 + (i + 1) % count
            idx += [c, a, b] if nz > 0 else [c, b, a]
    p = np.array(pos, np.float32)
    n = np.array(nrm, np.float32)
    # Proper rotations (determinant +1), so the triangle winding stays outward
    if axis == "x":  # (x, y, z) -> (z, y, -x): the cylinder axis becomes X
        p, n = p[:, [2, 1, 0]] * [1, 1, -1], n[:, [2, 1, 0]] * [1, 1, -1]
    elif axis == "y":  # (x, y, z) -> (x, z, -y): the cylinder axis becomes Y
        p, n = p[:, [0, 2, 1]] * [1, 1, -1], n[:, [0, 2, 1]] * [1, 1, -1]
    idx_arr = np.array(idx, np.uint32)
    return (p + np.array(center, np.float32)).astype(np.float32), n.astype(np.float32), idx_arr


# ----- glTF writer -----

class Gltf:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.doc = {
            "asset": {"version": "2.0", "generator": "py-xiaozhi build_robot_glb.py"},
            "scene": 0,
            "scenes": [{"name": "xiaozhi", "nodes": []}],
            "nodes": [],
            "meshes": [],
            "materials": [],
            "accessors": [],
            "bufferViews": [],
            "buffers": [],
        }
        self.materials: dict[str, int] = {}

    def material(self, name, rgb, metallic=0.0, roughness=0.6, emissive=None):
        if name not in self.materials:
            m = {
                "name": name,
                "pbrMetallicRoughness": {
                    "baseColorFactor": [*rgb, 1.0],
                    "metallicFactor": metallic,
                    "roughnessFactor": roughness,
                },
            }
            if emissive:
                m["emissiveFactor"] = list(emissive)
            self.materials[name] = len(self.doc["materials"])
            self.doc["materials"].append(m)
        return self.materials[name]

    def _view(self, data: bytes, target: int) -> int:
        while len(self.buffer) % 4:
            self.buffer.append(0)
        offset = len(self.buffer)
        self.buffer += data
        self.doc["bufferViews"].append(
            {"buffer": 0, "byteOffset": offset, "byteLength": len(data), "target": target}
        )
        return len(self.doc["bufferViews"]) - 1

    def _accessor(self, array: np.ndarray, kind: str, target: int, minmax=False) -> int:
        view = self._view(array.tobytes(), target)
        acc = {
            "bufferView": view,
            "componentType": 5126 if array.dtype == np.float32 else 5125,
            "count": int(array.shape[0]),
            "type": kind,
        }
        if minmax:
            acc["min"] = array.min(0).tolist()
            acc["max"] = array.max(0).tolist()
        self.doc["accessors"].append(acc)
        return len(self.doc["accessors"]) - 1

    def mesh(self, name, parts) -> int:
        """parts: list of ((pos, nrm, idx), material index)."""
        primitives = []
        for (pos, nrm, idx), mat in parts:
            primitives.append(
                {
                    "attributes": {
                        "POSITION": self._accessor(pos, "VEC3", 34962, minmax=True),
                        "NORMAL": self._accessor(nrm, "VEC3", 34962),
                    },
                    "indices": self._accessor(idx.astype(np.uint32), "SCALAR", 34963),
                    "material": mat,
                }
            )
        self.doc["meshes"].append({"name": name, "primitives": primitives})
        return len(self.doc["meshes"]) - 1

    def node(self, name, parent=None, mesh=None, translation=None, rotation=None, extras=None) -> int:
        node = {"name": name}
        if extras:
            node["extras"] = extras
        if mesh is not None:
            node["mesh"] = mesh
        if translation is not None:
            node["translation"] = [float(v) for v in translation]
        if rotation is not None:
            node["rotation"] = [float(v) for v in rotation]
        self.doc["nodes"].append(node)
        index = len(self.doc["nodes"]) - 1
        if parent is None:
            self.doc["scenes"][0]["nodes"].append(index)
        else:
            self.doc["nodes"][parent].setdefault("children", []).append(index)
        return index

    def write(self, path: Path) -> int:
        while len(self.buffer) % 4:
            self.buffer.append(0)
        self.doc["buffers"].append({"byteLength": len(self.buffer)})
        text = json.dumps(self.doc, separators=(",", ":")).encode()
        text += b" " * ((4 - len(text) % 4) % 4)
        total = 12 + 8 + len(text) + 8 + len(self.buffer)
        data = struct.pack("<III", 0x46546C67, 2, total)
        data += struct.pack("<II", len(text), 0x4E4F534A) + text
        data += struct.pack("<II", len(self.buffer), 0x004E4942) + bytes(self.buffer)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return total


def build(path: Path = OUT) -> int:
    g = Gltf()
    dark = g.material("base_dark", (0.16, 0.17, 0.19), roughness=0.7)
    shell = g.material("shell_white", (0.92, 0.93, 0.95), roughness=0.45)
    accent = g.material("accent_orange", (0.98, 0.55, 0.15), roughness=0.5)
    metal = g.material("servo_black", (0.08, 0.08, 0.09), roughness=0.4)
    gear = g.material("gear_blue", (0.25, 0.55, 0.95), roughness=0.5)
    screen = g.material("screen", (0.02, 0.02, 0.03), roughness=0.2)
    eye = g.material("eye_glow", (0.85, 0.95, 1.0), roughness=0.3, emissive=(0.55, 0.75, 0.85))
    lens = g.material("lens", (0.05, 0.08, 0.12), metallic=0.3, roughness=0.15)

    s45 = math.sin(math.radians(-45))
    robot = g.node("robot", rotation=(s45, 0.0, 0.0, math.cos(math.radians(-45))))

    # Base: body plus a front "chin" so the front is obvious even from the base
    base_parts = [
        (rounded_box((220, 160, BASE_TOP - 4), 18, (0, 0, (BASE_TOP - 4) / 2)), dark),
        (box((200, 140, 4), (0, 0, BASE_TOP - 2)), dark),
        (box((60, 3, 10), (0, 81, 20)), accent),  # Front marker (robot's front, +Y)
    ]
    g.node("base", robot, g.mesh("base", base_parts))

    pan = g.node("pan_pivot", robot, translation=(0, 0, BASE_TOP), extras={"sign": 1})
    turn_parts = [
        (cylinder(70, TURN_H, (0, 0, TURN_H / 2), teeth=24, tooth=2.0), accent),
        (cylinder(30, 6, (0, 0, TURN_H + 3)), dark),
        (box((14, 30, 4), (0, 52, TURN_H + 2)), shell),  # Arrow toward the front
    ]
    g.node("turn", pan, g.mesh("turn", turn_parts))

    arm_h = TILT_Z - BASE_TOP - TURN_H + 12
    arm_z = TURN_H + arm_h / 2
    tilt_local_z = TILT_Z - BASE_TOP
    upper_parts = [
        (box((12, 50, arm_h), (-ARM_X, 0, arm_z)), shell),
        (box((12, 50, arm_h), (ARM_X, 0, arm_z)), shell),
        (box((2 * ARM_X + 12, 60, 8), (0, 0, TURN_H + 4)), shell),  # Cross plate
        (cylinder(12, 16, (-ARM_X, 0, tilt_local_z), axis="x"), dark),  # Bearings
        (cylinder(12, 16, (ARM_X, 0, tilt_local_z), axis="x"), dark),
        # Tilt servo on the robot's left arm (-X), with its 15T gear meshing the 30T
        (box((20, 40, 41), (-ARM_X - 18, -12, tilt_local_z - 52)), metal),
        (cylinder(8.5, 8, (-ARM_X - 12, -2, tilt_local_z - 38), axis="x", teeth=15, tooth=1.2), gear),
    ]
    g.node("upper", pan, g.mesh("upper", upper_parts))

    tilt = g.node("tilt_pivot", pan, translation=(0, 0, tilt_local_z), extras={"sign": 1})
    front = HEAD_D / 2
    head_parts = [
        (rounded_box((HEAD_W, HEAD_D, HEAD_H), 18), shell),
        (rounded_box((HEAD_W - 30, 3, HEAD_H - 26), 8, (0, front + 1.5, 0)), screen),
        # Eyes like the robot face, so the facing direction is unmistakable
        (rounded_box((34, 2, 44), 10, (-38, front + 3.2, 6)), eye),
        (rounded_box((34, 2, 44), 10, (38, front + 3.2, 6)), eye),
        # Webcam on top, lens toward the front
        (rounded_box((70, 22, 20), 6, (0, 6, HEAD_H / 2 + 10)), metal),
        (cylinder(6, 4, (0, 18, HEAD_H / 2 + 10), axis="y"), lens),
        # Cable duct down the back, robot's right side
        (cylinder(9, 110, (HEAD_W / 2 - 40, -front - 8, -10)), dark),
        # 30T gear on the head's left axle (-X), outside the arm
        (cylinder(17, 6, (-ARM_X - 12, 0, 0), axis="x", teeth=30, tooth=1.2), gear),
        (cylinder(5, 2 * ARM_X + 8, (0, 0, 0), axis="x"), dark),  # Axle
    ]
    g.node("head", tilt, g.mesh("head", head_parts))
    return g.write(path)


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else OUT
    size = build(out)
    print(f"Wrote {out} ({size} bytes)")
