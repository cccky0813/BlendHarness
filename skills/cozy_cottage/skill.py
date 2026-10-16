import bpy
import bmesh


def build_cozy_cottage(location=(0.0, 0.0, 0.0), name='Cottage',
                       width=3.0, depth=4.0, wall_height=2.5,
                       roof_height=1.2, roof_overhang=0.2,
                       door_width=0.7, door_height=1.4,
                       window_size=0.72, window_height=1.4,
                       chimney_size=0.4):
    'Low-poly cottage whose lowest geometry sits exactly at location z.'
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

    m_wall = get_mat('Wall', (0.85, 0.78, 0.62))
    m_roof = get_mat('Roof', (0.60, 0.18, 0.12))
    m_door = get_mat('Door', (0.35, 0.20, 0.08))
    m_window = get_mat('Window', (0.55, 0.78, 0.95), roughness=0.1)
    m_frame = get_mat('WindowFrame', (0.22, 0.14, 0.07))
    m_chimney = get_mat('Chimney', (0.45, 0.30, 0.22))

    # walls: bottom face on location z
    box(name + '_Walls', (lx, ly, lz + wall_height * 0.5),
        (width, depth, wall_height), m_wall)

    front_y = ly - depth * 0.5

    # door, centred on the -Y facade
    box(name + '_Door', (lx, front_y - 0.01, lz + door_height * 0.5),
        (door_width, 0.05, door_height), m_door)

    # two windows flanking the door
    for tag, sgn in (('L', -1.0), ('R', 1.0)):
        wx = lx + sgn * (width / 3.0)
        box(name + '_WindowFrame_' + tag,
            (wx, front_y - 0.06, lz + window_height),
            (window_size, 0.10, window_size), m_frame)
        box(name + '_Window_' + tag,
            (wx, front_y - 0.03, lz + window_height),
            (window_size * 0.75, 0.06, window_size * 0.75), m_window)

    # pitched roof, ridge running along Y, base resting on the wall top
    prism(name + '_Roof', (lx, ly, lz + wall_height),
          width * 0.5 + roof_overhang, depth * 0.5 + roof_overhang,
          roof_height, m_roof)

    # chimney embedded in the roof
    ch_h = roof_height * 0.9 + 0.3
    box(name + '_Chimney',
        (lx + width * 0.2, ly + depth * 0.125,
         lz + wall_height + roof_height * 0.55),
        (chimney_size, chimney_size, ch_h), m_chimney)

    return objs
