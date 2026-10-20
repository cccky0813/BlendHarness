"""Rendering: the studio script appended after the agent's script to render orbit views inside Blender."""

RENDER_PREFIX = "BA_RENDER_RESULT="

# Appended after the agent's script; frames all meshes and renders N orbit views (+ top view).
# Studio setup: dark backdrop for contrast, ground plane for contact shadows, 3-point area lights
# scaled to the object size. Everything added here is render-only (not in the Scene-Inspector).
RENDER_SCRIPT = """
import bpy as _bpy, json as _json, math as _m, os as _os
from mathutils import Vector as _V
_out = r\"\"\"{out_dir}\"\"\"
_os.makedirs(_out, exist_ok=True)
_s = _bpy.context.scene
_s.render.engine = 'BLENDER_EEVEE_NEXT'
_s.render.resolution_x = _s.render.resolution_y = {res}
_s.render.image_settings.file_format = 'PNG'
try:
    _s.view_settings.view_transform = 'AgX'
    _s.view_settings.look = 'AgX - Medium High Contrast'
except Exception:
    pass
if _s.world is None:
    _s.world = _bpy.data.worlds.new("BA_World")
_s.world.use_nodes = True
_bg = _s.world.node_tree.nodes.get("Background")
if _bg:
    _bg.inputs["Color"].default_value = (0.05, 0.055, 0.065, 1.0)
    _bg.inputs["Strength"].default_value = 1.0

_meshes = [o for o in _s.objects if o.type == 'MESH']
_paths = []
if _meshes:
    _pts = [o.matrix_world @ _V(c) for o in _meshes for c in o.bound_box]
    _lo = _V((min(p.x for p in _pts), min(p.y for p in _pts), min(p.z for p in _pts)))
    _hi = _V((max(p.x for p in _pts), max(p.y for p in _pts), max(p.z for p in _pts)))
    _center, _radius = (_lo + _hi) / 2, max((_hi - _lo).length / 2, 0.05)

    # ground plane just under the object
    _gm = _bpy.data.meshes.new("BA_Ground")
    _r = _radius * 8
    _gm.from_pydata([(-_r, -_r, 0), (_r, -_r, 0), (_r, _r, 0), (-_r, _r, 0)], [], [(0, 1, 2, 3)])
    _ground = _bpy.data.objects.new("BA_Ground", _gm)
    _ground.location = (_center.x, _center.y, _lo.z - _radius * 0.002)
    _s.collection.objects.link(_ground)
    _gmat = _bpy.data.materials.new("BA_GroundMat")
    _gmat.use_nodes = True
    _gb = _gmat.node_tree.nodes.get("Principled BSDF")
    _gb.inputs["Base Color"].default_value = (0.32, 0.33, 0.35, 1.0)
    _gb.inputs["Roughness"].default_value = 0.8
    _ground.data.materials.append(_gmat)

    # 3-point lighting; power grows with distance^2 so brightness is size-independent
    _ld = _radius * 4.0
    for _name, _az, _el, _k in (("Key", 35, 45, 27), ("Fill", 160, 25, 9), ("Rim", 250, 55, 18)):
        _ldata = _bpy.data.lights.new("BA_" + _name, 'AREA')
        _ldata.energy = _k * _ld * _ld
        _ldata.size = _radius * 2.0
        _lobj = _bpy.data.objects.new("BA_" + _name, _ldata)
        _a, _e = _m.radians(_az), _m.radians(_el)
        _lobj.location = _center + _V((_ld * _m.cos(_a) * _m.cos(_e), _ld * _m.sin(_a) * _m.cos(_e), _ld * _m.sin(_e)))
        _lobj.rotation_euler = (_center - _lobj.location).to_track_quat('-Z', 'Y').to_euler()
        _s.collection.objects.link(_lobj)

    _cam = _bpy.data.objects.new("BA_Camera", _bpy.data.cameras.new("BA_Camera"))
    _s.collection.objects.link(_cam)
    _s.camera = _cam
    _cam.data.lens = 50
    _dist = _radius * 3.2
    _views = [(2 * _m.pi * i / {num_views} + _m.radians(30), _m.radians(20)) for i in range({num_views})]
    if {top_view}:
        _views.append((_m.radians(30), _m.radians(65)))
    for _i, (_a, _e) in enumerate(_views):
        _cam.location = _center + _V((_dist * _m.cos(_a) * _m.cos(_e), _dist * _m.sin(_a) * _m.cos(_e), _dist * _m.sin(_e)))
        _cam.rotation_euler = (_center - _cam.location).to_track_quat('-Z', 'Y').to_euler()
        _s.render.filepath = _os.path.join(_out, "{tag}_%02d.png" % _i)
        _bpy.ops.render.render(write_still=True)
        _paths.append(_s.render.filepath)
print("{prefix}" + _json.dumps({{"renders": _paths}}))
"""
