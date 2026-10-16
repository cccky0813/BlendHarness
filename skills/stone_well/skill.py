import bpy
import bmesh
import math


def build_stone_well(location=(0.0, 0.0, 0.0), name='Well',
                     rim_radius=0.55, rim_height=1.0, wall_thickness=0.21,
                     water_drop=0.15, post_span=0.4, post_size=0.12,
                     post_height=1.2, post_sink=0.1,
                     roof_height=0.5, roof_overhang=0.05, segments=12):
    'Stone well with wooden roof; lowest geometry sits exactly at location z.'
    lx, ly, lz = location
    objs = []

    def get_mat(mname, color, roughness=0.9):
        if mname in bpy.data.materials:
            return bpy.data.materials[mname]
        m = bpy.data.materials.new(mname)
        m.use_nodes = True
        b = m.node_tree.nodes['Principled BSDF']
        b.inputs['Base Color'].default_value = (color[0], color[1], color[2], 1.0)
        b.inputs['Roughness'].default_value = roughness
        return m

    def box(oname, center, size, material):
        cx, cy, cz = center
        hx, hy, hz = size[0] * 0.5, size[1] * 0.5, size[2] * 0.5
        bm = bmesh.new()
        co = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
              (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
        vs = [bm.verts.new((cx + x, cy + y, cz + z)) for (x, y, z) in co]
        for f in ((0, 1, 2, 3), (7, 6, 5, 4), (0, 4, 5, 1),
                  (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)):
            bm.faces.new([vs[i] for i in f])
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        me = bpy.data.meshes.new(oname)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(oname, me)
        bpy.context.collection.objects.link(ob)
        ob.data.materials.append(material)
        objs.append(ob)
        return ob

    def ring(oname, center, r_out, r_in, depth, n, material):
        cx, cy, cz = center
        top = cz + depth * 0.5
        bot = cz - depth * 0.5
        bm = bmesh.new()
        to_, bo_, ti_, bi_ = [], [], [], []
        for i in range(n):
            a = 2.0 * math.pi * i / n
            c, s = math.cos(a), math.sin(a)
            to_.append(bm.verts.new((cx + r_out * c, cy + r_out * s, top)))
            bo_.append(bm.verts.new((cx + r_out * c, cy + r_out * s, bot)))
            ti_.append(bm.verts.new((cx + r_in * c, cy + r_in * s, top)))
            bi_.append(bm.verts.new((cx + r_in * c, cy + r_in * s, bot)))
        for i in range(n):
            j = (i + 1) % n
            bm.faces.new([bo_[i], bo_[j], to_[j], to_[i]])
            bm.faces.new([bi_[j], bi_[i], ti_[i], ti_[j]])
            bm.faces.new([to_[i], to_[j], ti_[j], ti_[i]])
            bm.faces.new([bi_[i], bi_[j], bo_[j], bo_[i]])
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        me = bpy.data.meshes.new(oname)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(oname, me)
        bpy.context.collection.objects.link(ob)
        ob.data.materials.append(material)
        objs.append(ob)
        return ob

    def cylinder(oname, center, radius, depth, n, material):
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=n,
                              radius1=radius, radius2=radius, depth=depth)
        for v in bm.verts:
            v.co.x += center[0]
            v.co.y += center[1]
            v.co.z += center[2]
        me = bpy.data.meshes.new(oname)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(oname, me)
        bpy.context.collection.objects.link(ob)
        ob.data.materials.append(material)
        objs.append(ob)
        return ob

    def prism(oname, origin, sx, sy, height, material):
        ox, oy, oz = origin
        bm = bmesh.new()
        v = [bm.verts.new((ox - sx, oy - sy, oz)),
             bm.verts.new((ox + sx, oy - sy, oz)),
             bm.verts.new((ox + sx, oy + sy, oz)),
             bm.verts.new((ox - sx, oy + sy, oz)),
             bm.verts.new((ox, oy - sy, oz + height)),
             bm.verts.new((ox, oy + sy, oz + height))]
        bm.faces.new([v[0], v[1], v[4]])
        bm.faces.new([v[2], v[3], v[5]])
        bm.faces.new([v[0], v[3], v[5], v[4]])
        bm.faces.new([v[1], v[2], v[5], v[4]])
        bm.faces.new([v[0], v[1], v[2], v[3]])
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        me = bpy.data.meshes.new(oname)
        bm.to_mesh(me)
        bm.free()
        ob = bpy.data.objects.new(oname, me)
        bpy.context.collection.objects.link(ob)
        ob.data.materials.append(material)
        objs.append(ob)
        return ob

    m_stone = get_mat('Stone', (0.55, 0.52, 0.48))
    m_wood = get_mat('Wood', (0.50, 0.28, 0.10))
    m_water = get_mat('WellWater', (0.04, 0.12, 0.17), roughness=0.15)

    r_in = rim_radius - wall_thickness

    # stone shaft, bottom rim on location z
    ring(name + '_Ring', (lx, ly, lz + rim_height * 0.5),
         rim_radius, r_in, rim_height, segments, m_stone)

    # water, top surface water_drop below the rim
    water_h = rim_height - water_drop
    cylinder(name + '_Water', (lx, ly, lz + water_h * 0.5),
             r_in - 0.01, water_h, segments, m_water)

    # two posts straddling the opening
    post_bottom = rim_height - post_sink
    for tag, sgn in (('L', -1.0), ('R', 1.0)):
        box(name + '_Post_' + tag,
            (lx + sgn * post_span, ly, lz + post_bottom + post_height * 0.5),
            (post_size, post_size, post_height), m_wood)

    # small pitched roof on the post tops
    rw = rim_radius + roof_overhang
    prism(name + '_Roof', (lx, ly, lz + post_bottom + post_height),
          rw, rw, roof_height, m_wood)

    return objs
