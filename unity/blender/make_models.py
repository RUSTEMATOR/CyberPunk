"""Low-poly models of the Unity client, made by Blender in the background (owner request 31.10, docs/PLAN_FIGHT3D.md
phase 3). Run by the bot on the Mac (app/services/unity_jobs.py, job "blender_models"):

    Blender -b --factory-startup --python unity/blender/make_models.py -- <out dir>

Every model is built from boxes in code, so a change here is a new model on the next run. The human is made of
separate parts with their pivots at the joints (hips, waist, neck, shoulders, hips of the legs), so the client swings
the arms and legs itself. The figures face -Y in Blender; the FBX export turns them to face +Z in Unity.
"""

import os
import sys

import bmesh
import bpy
from mathutils import Vector

OUT = sys.argv[sys.argv.index("--") + 1] if "--" in sys.argv else os.path.join(os.getcwd(), "models")
os.makedirs(OUT, exist_ok=True)


def clear() -> None:
    bpy.ops.wm.read_factory_settings(use_empty=True)


def box_mesh(name: str, parts: list[tuple[tuple[float, float, float], tuple[float, float, float]]], bevel: float = 0.012):
    """A mesh of several boxes: (size, center) each, in the object's own space (its origin is the joint)."""
    bm = bmesh.new()
    for size, center in parts:
        res = bmesh.ops.create_cube(bm, size=1.0)
        verts = res["verts"]
        bmesh.ops.scale(bm, vec=Vector(size), verts=verts)
        bmesh.ops.translate(bm, vec=Vector(center), verts=verts)
    if bevel > 0:
        bmesh.ops.bevel(bm, geom=list(bm.edges), offset=bevel, segments=1, affect="EDGES", profile=0.5)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return me


def part(name: str, parent, origin: tuple[float, float, float], parts, bevel: float = 0.012):
    obj = bpy.data.objects.new(name, box_mesh(name, parts, bevel))
    bpy.context.scene.collection.objects.link(obj)
    obj.location = origin
    if parent is not None:
        obj.parent = parent
    return obj


def empty(name: str):
    e = bpy.data.objects.new(name, None)
    bpy.context.scene.collection.objects.link(e)
    return e


def export(path: str) -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.fbx(filepath=path, use_selection=True, object_types={"EMPTY", "MESH"},
                             axis_forward="-Z", axis_up="Y", apply_scale_options="FBX_SCALE_ALL",
                             bake_space_transform=True, add_leaf_bones=False, mesh_smooth_type="FACE",
                             use_mesh_modifiers=True)
    print("exported", path)


def human() -> None:
    """1.8 m: the parts the client knows by name: hips, torso, head, visor, arm_l, arm_r, leg_l, leg_r, jacket."""
    clear()
    root = empty("human")
    part("hips", root, (0, 0, 0.95), [((0.34, 0.2, 0.18), (0, 0, 0))])
    torso = part("torso", root, (0, 0, 1.03), [((0.4, 0.23, 0.52), (0, 0, 0.27)), ((0.2, 0.12, 0.08), (0, 0, 0.56))])
    # a jacket: wider, open at the front, with a high collar
    part("jacket", torso, (0, 0, 0), [((0.46, 0.27, 0.42), (0, 0.01, 0.31)), ((0.3, 0.2, 0.12), (0, 0.04, 0.58))], 0.02)
    head = part("head", root, (0, 0, 1.63), [((0.21, 0.23, 0.25), (0, 0, 0.14)), ((0.23, 0.1, 0.1), (0, 0.08, 0.2))])
    part("visor", head, (0, 0, 0), [((0.2, 0.03, 0.055), (0, -0.117, 0.16))], 0.006)
    for side, x in (("l", 0.255), ("r", -0.255)):
        part(f"arm_{side}", root, (x, 0, 1.52), [((0.12, 0.13, 0.34), (0, 0, -0.16)), ((0.1, 0.11, 0.3), (0, -0.01, -0.47)),
                                                 ((0.09, 0.11, 0.1), (0, -0.02, -0.66))])
        part(f"leg_{side}", root, (x * 0.42, 0, 0.92), [((0.15, 0.16, 0.46), (0, 0, -0.22)), ((0.13, 0.14, 0.42), (0, 0, -0.64)),
                                                        ((0.14, 0.27, 0.08), (0, -0.05, -0.88))])
    export(os.path.join(OUT, "human.fbx"))


def pistol() -> None:
    """The gun in hand: the barrel along -Y, the origin at the grip."""
    clear()
    root = empty("pistol")
    part("frame", root, (0, 0, 0), [((0.034, 0.2, 0.045), (0, -0.07, 0.06)), ((0.03, 0.06, 0.12), (0, 0.0, -0.01))], 0.004)
    part("slide", root, (0, 0, 0), [((0.036, 0.21, 0.03), (0, -0.075, 0.095))], 0.004)
    part("glow", root, (0, 0, 0), [((0.008, 0.16, 0.008), (0.019, -0.07, 0.095))], 0.0)
    part("muzzle", root, (0, -0.18, 0.08), [((0.02, 0.01, 0.02), (0, 0, 0))], 0.0)
    export(os.path.join(OUT, "pistol.fbx"))


def rifle() -> None:
    clear()
    root = empty("rifle")
    part("frame", root, (0, 0, 0), [((0.05, 0.55, 0.07), (0, -0.15, 0.05)), ((0.04, 0.07, 0.13), (0, 0.02, -0.03)),
                                    ((0.04, 0.22, 0.09), (0, 0.22, 0.03)), ((0.035, 0.05, 0.14), (0, -0.12, -0.03))], 0.005)
    part("slide", root, (0, 0, 0), [((0.03, 0.32, 0.03), (0, -0.48, 0.07)), ((0.04, 0.12, 0.05), (0, -0.05, 0.11))], 0.004)
    part("glow", root, (0, 0, 0), [((0.008, 0.3, 0.008), (0.027, -0.2, 0.06))], 0.0)
    part("muzzle", root, (0, -0.66, 0.07), [((0.02, 0.01, 0.02), (0, 0, 0))], 0.0)
    export(os.path.join(OUT, "rifle.fbx"))


def blade() -> None:
    clear()
    root = empty("blade")
    part("frame", root, (0, 0, 0), [((0.03, 0.03, 0.14), (0, 0, 0)), ((0.07, 0.02, 0.02), (0, 0, 0.08))], 0.004)
    part("glow", root, (0, 0, 0), [((0.012, 0.035, 0.62), (0, 0, 0.4))], 0.002)
    export(os.path.join(OUT, "blade.fbx"))


human()
pistol()
rifle()
blade()
print("models done")
