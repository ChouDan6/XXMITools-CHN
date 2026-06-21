import bpy
import math
import hashlib
import numpy as np
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import EnumProperty, IntProperty, PointerProperty

# =============================================================================
# 1. 属性定义
# =============================================================================
class XXMI_MergeUVProperties(PropertyGroup):
    workflow_tab: EnumProperty(
        name="工作流模式",
        items=[('AUTO', "全自动", ""), ('VISUAL', "交互式", "")],
        default='AUTO'
    )
    size_mode_w: EnumProperty(
        name="宽度规则",
        items=[
            ('POT', "2的幂次方", "128, 256, 512, 1024, 2048, 4096…"),
            ('MULTI_OF_4', "4的倍数", "兼容 BC/DXT/ETC/ASTC 压缩格式"),
            ('EVEN', "2的倍数", "任意偶数，最灵活"),
        ],
        default='POT'
    )
    size_mode_h: EnumProperty(
        name="高度规则",
        items=[
            ('POT', "2的幂次方", ""),
            ('MULTI_OF_4', "4的倍数", ""),
            ('EVEN', "2的倍数", ""),
        ],
        default='POT'
    )
    target_width: IntProperty(name="宽 (W)", default=2048, min=2)
    target_height: IntProperty(name="高 (H)", default=2048, min=2)

# =============================================================================
# 2. 核心算法与辅助函数
# =============================================================================
def get_pot(x):
    return 2 ** math.ceil(math.log2(max(1, x)))

def get_multi_of_4(x):
    """向上取整到最近的 4 的倍数（兼容 BC/DXT/ETC/ASTC 压缩格式）。"""
    x = int(math.ceil(x))
    rem = x % 4
    return x if rem == 0 else x + (4 - rem)

def get_even(x):
    x = int(math.ceil(x))
    return x if x % 2 == 0 else x + 1

def apply_size_rule(x, mode):
    """根据规则模式对尺寸进行强制对齐。"""
    if mode == 'POT':
        return get_pot(x)
    elif mode == 'MULTI_OF_4':
        return get_multi_of_4(x)
    else:
        return get_even(x)

def get_image_hash(img):
    if not img or not img.pixels: return None
    if not img.has_data: img.update()
    sample_size = min(len(img.pixels), 10000)
    raw_pixels = np.array(img.pixels[:sample_size])
    return hashlib.md5(raw_pixels.tobytes() + str(img.size[0]).encode()).hexdigest()

def find_node_by_type(material, node_type):
    if not material or not material.use_nodes: return None
    return next((n for n in material.node_tree.nodes if n.type == node_type), None)

def _trace_tex_image(node, visited=None):
    """递归回溯节点链，找到第一个有 image 的 TEX_IMAGE 节点。"""
    if visited is None:
        visited = set()
    if node in visited:
        return None, None
    visited.add(node)
    if node.type == 'TEX_IMAGE' and node.image:
        return node, node.image
    for inp in node.inputs:
        for link in inp.links:
            result = _trace_tex_image(link.from_node, visited)
            if result[0] is not None:
                return result
    return None, None

def get_base_color_node(material):
    """获取材质 Base Color 通道上的贴图节点，支持中间节点。"""
    if not material or not material.use_nodes:
        return None, None
    for node in material.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            base_color_input = node.inputs.get("Base Color")
            if base_color_input and base_color_input.is_linked:
                return _trace_tex_image(base_color_input.links[0].from_node)
    # 兜底：从 Material Output 回溯
    for node in material.node_tree.nodes:
        if node.type == 'OUTPUT_MATERIAL':
            surf = node.inputs.get("Surface")
            if surf and surf.is_linked:
                shader = surf.links[0].from_node
                for input_name in ("Base Color", "Color"):
                    inp = shader.inputs.get(input_name)
                    if inp and inp.is_linked:
                        return _trace_tex_image(inp.links[0].from_node)
    return None, None

def get_tex_uv_layer_name(tex_node):
    """获取贴图节点实际使用的 UV 层名称。
    如果 Vector 输入连接了 UV Map 节点则返回其指定名称，否则返回 None（使用 active 层）。"""
    if not tex_node:
        return None
    vec_input = tex_node.inputs.get("Vector")
    if vec_input and vec_input.is_linked:
        from_node = vec_input.links[0].from_node
        if from_node.type == 'UVMAP' and from_node.uv_map:
            return from_node.uv_map
    return None

def calculate_packing(images_info, target_w, mode_w, mode_h):
    sorted_images = sorted(images_info, key=lambda x: x[2], reverse=True)
    max_img_w = max((img[1] for img in sorted_images), default=0)
    actual_w = apply_size_rule(max(target_w, max_img_w), mode_w)

    placements = {}
    current_x, current_y = 0, 0
    row_height = 0
    max_y = 0
    for img_hash, w, h in sorted_images:
        if current_x + w > actual_w and current_x > 0:
            current_y += row_height
            current_x = 0
            row_height = 0
        placements[img_hash] = (current_x, current_y)
        current_x += w
        row_height = max(row_height, h)
        max_y = max(max_y, current_y + h)

    final_h = apply_size_rule(max_y, mode_h)
    return actual_w, final_h, placements

def find_best_packing(images_info, mode_w, mode_h):
    """多候选宽度试探，返回利用率（已用像素/总像素）最高的打包方案。
    候选宽度来源：
      1. sqrt(总面积) × 常见长宽比 (0.5~2.0)
      2. 游戏引擎常用尺寸 (512, 1024, 2048, 4096)
      3. 最大贴图宽度落在规则边界上的对齐值
    返回: (best_w, best_h, best_placements, best_efficiency)
    """
    total_area = sum(w * h for _, w, h in images_info)
    max_img_w = max((img[1] for img in images_info), default=0)
    base_area = max(total_area, 1)

    candidates = set()
    # 1) 基于总面积的不同长宽比
    for ratio in (0.5, 0.625, 0.75, 0.875, 1.0, 1.125, 1.25, 1.5, 1.75, 2.0):
        candidate = max(max_img_w, int(math.sqrt(base_area * ratio)))
        candidates.add(candidate)
    # 2) 游戏引擎常用尺寸
    for size in (512, 1024, 2048, 4096, 8192):
        if size >= max_img_w:
            candidates.add(size)
    # 3) 最大贴图宽度 + 各种对齐边界
    for extra in (1, 4, 8, 16, 32, 64, 128, 256):
        candidates.add(max_img_w + extra)

    best = None
    for w in sorted(candidates):
        w_aligned = apply_size_rule(w, mode_w)
        _, h, placements = calculate_packing(images_info, w_aligned, mode_w, mode_h)
        atlas_px = w_aligned * h
        efficiency = total_area / atlas_px if atlas_px > 0 else 0.0
        if best is None or efficiency > best[3]:
            best = (w_aligned, h, placements, efficiency)

    return best

def get_assets_from_objects(objs):
    """返回:
    - objs: 原始对象列表
    - unique_imgs: {hash: image}
    - mat_info: {mat_name: (hash, uv_layer_name_or_None)}
    """
    unique_imgs = {}
    mat_info = {}
    for obj in objs:
        if obj.type != 'MESH': continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat and mat.name not in mat_info:
                tex_node, img = get_base_color_node(mat)
                if img:
                    h = get_image_hash(img)
                    uv_name = get_tex_uv_layer_name(tex_node)
                    mat_info[mat.name] = (h, uv_name)
                    if h not in unique_imgs:
                        unique_imgs[h] = img
    return objs, unique_imgs, mat_info

def get_unique_assets(context):
    selected_objs = [obj for obj in context.selected_objects if obj.type == 'MESH']
    return get_assets_from_objects(selected_objs)

# --- UX 状态存储变量 ---
_stored_settings = {}

def store_and_setup_viewport(context):
    global _stored_settings
    scene = context.scene
    space = None
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            break
    if space:
        _stored_settings['shading_type'] = space.shading.type
        space.shading.type = 'MATERIAL'
    _stored_settings['use_snap'] = scene.tool_settings.use_snap
    _stored_settings['snap_elements'] = set(scene.tool_settings.snap_elements)
    _stored_settings['snap_target'] = scene.tool_settings.snap_target
    scene.tool_settings.use_snap = True
    scene.tool_settings.snap_elements = {'VERTEX', 'EDGE'}
    scene.tool_settings.snap_target = 'CLOSEST'
    bpy.ops.view3d.view_axis(type='TOP')

def restore_viewport(context):
    global _stored_settings
    if not _stored_settings: return
    scene = context.scene
    for area in context.screen.areas:
        if area.type == 'VIEW_3D':
            space = area.spaces.active
            if 'shading_type' in _stored_settings:
                space.shading.type = _stored_settings['shading_type']
            break
    if 'use_snap' in _stored_settings:
        scene.tool_settings.use_snap = _stored_settings['use_snap']
    if 'snap_elements' in _stored_settings:
        scene.tool_settings.snap_elements = _stored_settings['snap_elements']
    if 'snap_target' in _stored_settings:
        scene.tool_settings.snap_target = _stored_settings['snap_target']
    _stored_settings.clear()

def _wrap_uv(val):
    """将 UV 坐标 wrap 到 [0, 1) 范围，等效 REPEAT 寻址。"""
    return val % 1.0

def apply_results(objs, flat_pixels, fw, fh, mat_info, unique_imgs, placements):
    # ---- 只创建一张合并贴图，所有材质共享 ----
    merged_img_name = "MergedAtlas"
    old_img = bpy.data.images.get(merged_img_name)
    if old_img:
        old_img.name = merged_img_name + "_Trash"
    merged_img = bpy.data.images.new(merged_img_name, width=fw, height=fh, alpha=True)
    merged_img.pixels.foreach_set(flat_pixels)
    merged_img.update()
    if old_img:
        bpy.data.images.remove(old_img)

    # 1. 更新 UV 坐标 —— 只变换贴图实际使用的 UV 层
    for obj in objs:
        mesh = obj.data
        for poly in mesh.polygons:
            if poly.material_index >= len(obj.material_slots):
                continue
            mat = obj.material_slots[poly.material_index].material
            if not mat or mat.name not in mat_info:
                continue
            img_hash, uv_layer_name = mat_info[mat.name]
            if img_hash not in placements:
                continue

            px, py = placements[img_hash]
            img = unique_imgs[img_hash]
            s_u = img.size[0] / fw
            s_v = img.size[1] / fh
            o_u = px / fw
            o_v = py / fh

            # 确定要变换的 UV 层：仅贴图引用的那一层
            if uv_layer_name and uv_layer_name in mesh.uv_layers:
                target_layers = [mesh.uv_layers[uv_layer_name]]
            elif mesh.uv_layers.active:
                target_layers = [mesh.uv_layers.active]
            else:
                continue

            for uv_layer in target_layers:
                for idx in poly.loop_indices:
                    uv = uv_layer.data[idx].uv
                    # 先 wrap 到 [0,1)，消除 REPEAT 越界
                    uv.x = _wrap_uv(uv.x) * s_u + o_u
                    uv.y = _wrap_uv(uv.y) * s_v + o_v

    # 2. 所有材质指向同一张合并贴图
    for mat_name in mat_info:
        mat = bpy.data.materials.get(mat_name)
        tex_node, _ = get_base_color_node(mat)
        if tex_node:
            tex_node.image = merged_img

# =============================================================================
# 3. 操作符 (Operators)
# =============================================================================
class XXMI_OT_MUV_CalculateSize(Operator):
    bl_idname = "xxmi.muv_calculate_size"
    bl_label = "预计算最佳尺寸"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.xxmi_merge_uv_props
        _, unique_imgs, _ = get_unique_assets(context)
        if not unique_imgs: return {'CANCELLED'}
        images_info = [(h_id, img.size[0], img.size[1]) for h_id, img in unique_imgs.items()]
        total_area = sum(w * h for _, w, h in images_info)
        mode_w, mode_h = props.size_mode_w, props.size_mode_h
        calc_w, calc_h, _, efficiency = find_best_packing(images_info, mode_w, mode_h)
        props.target_width = calc_w
        props.target_height = calc_h
        waste_pct = round((1.0 - efficiency) * 100, 1)
        self.report({'INFO'},
            f"最佳尺寸: {calc_w}×{calc_h}  |  "
            f"利用率: {round(efficiency * 100, 1)}%  |  "
            f"浪费: {waste_pct}%"
        )
        return {'FINISHED'}

class XXMI_OT_MUV_RecommendRules(Operator):
    bl_idname = "xxmi.muv_recommend_rules"
    bl_label = "推荐最优规则"
    bl_description = "遍历 9 种规则组合，自动选中利用率最高的方案"
    bl_options = {'REGISTER', 'UNDO'}

    RULES = ('POT', 'MULTI_OF_4', 'EVEN')
    RULE_LABELS = {'POT': "2的幂", 'MULTI_OF_4': "4的倍数", 'EVEN': "2的倍数"}

    def execute(self, context):
        props = context.scene.xxmi_merge_uv_props
        _, unique_imgs, _ = get_unique_assets(context)
        if not unique_imgs:
            self.report({'WARNING'}, "未发现贴图")
            return {'CANCELLED'}
        images_info = [(h_id, img.size[0], img.size[1]) for h_id, img in unique_imgs.items()]

        best = None  # (mode_w, mode_h, w, h, efficiency)
        for mw in self.RULES:
            for mh in self.RULES:
                bw, bh, _, eff = find_best_packing(images_info, mw, mh)
                if best is None or eff > best[4]:
                    best = (mw, mh, bw, bh, eff)

        mw_best, mh_best, calc_w, calc_h, efficiency = best
        props.size_mode_w = mw_best
        props.size_mode_h = mh_best
        props.target_width = calc_w
        props.target_height = calc_h

        waste_pct = round((1.0 - efficiency) * 100, 1)
        self.report(
            {'INFO'},
            f"推荐: 宽={self.RULE_LABELS[mw_best]}，"
            f"高={self.RULE_LABELS[mh_best]}  |  "
            f"{calc_w}×{calc_h}  |  "
            f"利用率: {round(efficiency * 100, 1)}%  |  "
            f"浪费: {waste_pct}%"
        )
        return {'FINISHED'}

class XXMI_OT_MUV_ExecuteAutoMerge(Operator):
    bl_idname = "xxmi.muv_execute_auto_merge"
    bl_label = "执行自动合并"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.xxmi_merge_uv_props
        objs, unique_imgs, mat_info = get_unique_assets(context)
        if not unique_imgs: return {'CANCELLED'}
        mode_w, mode_h = props.size_mode_w, props.size_mode_h
        final_w = props.target_width
        images_info = [(h_id, img.size[0], img.size[1]) for h_id, img in unique_imgs.items()]
        fw, fh, placements = calculate_packing(images_info, final_w, mode_w, mode_h)

        atlas = np.zeros((fh, fw, 4), dtype=np.float32)
        for h, (px, py) in placements.items():
            img = unique_imgs[h]
            iw, ih = img.size[0], img.size[1]
            src = np.empty(iw * ih * 4, dtype=np.float32)
            img.pixels.foreach_get(src)
            if py + ih <= fh and px + iw <= fw:
                atlas[py : py + ih, px : px + iw] = src.reshape((ih, iw, 4))

        apply_results(objs, atlas.flatten(), fw, fh, mat_info, unique_imgs, placements)
        return {'FINISHED'}

class XXMI_OT_MUV_StartLayout(Operator):
    bl_idname = "xxmi.muv_start_layout"
    bl_label = "1. ��成面片网格"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        objs, unique_imgs, _ = get_unique_assets(context)
        if not unique_imgs:
            self.report({'WARNING'}, "未发现带有贴图的有效选中对象")
            return {'CANCELLED'}

        col_name = "Texture_Layout_Canvas"
        if col_name in bpy.data.collections:
            bpy.data.collections.remove(bpy.data.collections[col_name], do_unlink=True)
        layout_col = bpy.data.collections.new(col_name)
        context.scene.collection.children.link(layout_col)
        layout_col["muv_target_objs"] = ",".join([obj.name for obj in objs])

        cols = math.ceil(math.sqrt(len(unique_imgs)))
        for i, (h_id, img) in enumerate(unique_imgs.items()):
            row, col = divmod(i, cols)
            w, h = img.size[0], img.size[1]
            scale_x, scale_y = w / 1000.0, h / 1000.0
            loc = (col * 3.0, row * -3.0, 0.0)
            bpy.ops.mesh.primitive_plane_add(size=1, location=loc)
            plane = context.active_object
            plane.name = f"L_{img.name}"
            plane["muv_hash"] = h_id
            for v in plane.data.vertices:
                v.co.x += 0.5
                v.co.y += 0.5
            plane.scale[0] = scale_x
            plane.scale[1] = scale_y
            for old_col in list(plane.users_collection): old_col.objects.unlink(plane)
            layout_col.objects.link(plane)
            mat = bpy.data.materials.new(name=f"PREV_{img.name}")
            mat.use_nodes = True
            bsdf = find_node_by_type(mat, 'BSDF_PRINCIPLED')
            if bsdf:
                tex = mat.node_tree.nodes.new('ShaderNodeTexImage')
                tex.image = img
                mat.node_tree.links.new(tex.outputs[0], bsdf.inputs[0])
            plane.data.materials.append(mat)

        store_and_setup_viewport(context)
        bpy.ops.object.select_all(action='DESELECT')
        for p in layout_col.objects:
            p.select_set(True)
            context.view_layer.objects.active = p
        self.report({'INFO'}, "已记录原模型。请使用 'G' 键移动面片拼合，完全无缝吸附。")
        return {'FINISHED'}

class XXMI_OT_MUV_ConfirmLayout(Operator):
    bl_idname = "xxmi.muv_confirm_layout"
    bl_label = "2. 确认合并"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.xxmi_merge_uv_props
        col = bpy.data.collections.get("Texture_Layout_Canvas")
        if not col:
            self.report({'ERROR'}, "未找到拼图画布，请先生成面片！")
            return {'CANCELLED'}
        target_names_str = col.get("muv_target_objs", "")
        if not target_names_str:
            self.report({'ERROR'}, "未找到原始合并对象记录，请重新执行'生成面片网格'")
            return {'CANCELLED'}
        target_names = target_names_str.split(",")
        target_objs = [bpy.data.objects.get(name) for name in target_names if bpy.data.objects.get(name)]
        if not target_objs:
            self.report({'ERROR'}, "场景中丢失了原始模型！无法合并。")
            return {'CANCELLED'}

        planes = [p for p in col.objects if "muv_hash" in p]
        if not planes: return {'CANCELLED'}

        min_x = min(p.location.x for p in planes)
        min_y = min(p.location.y for p in planes)
        max_x = max(p.location.x + p.scale[0] for p in planes)
        max_y = max(p.location.y + p.scale[1] for p in planes)
        total_w_px = int(round((max_x - min_x) * 1000))
        total_h_px = int(round((max_y - min_y) * 1000))

        mode_w, mode_h = props.size_mode_w, props.size_mode_h
        final_w = apply_size_rule(total_w_px, mode_w)
        final_h = apply_size_rule(total_h_px, mode_h)

        atlas = np.zeros((final_h, final_w, 4), dtype=np.float32)
        placements = {}
        objs, unique_imgs, mat_info = get_assets_from_objects(target_objs)

        for p in planes:
            h_id = p["muv_hash"]
            img = unique_imgs.get(h_id)
            if not img: continue
            p_left = p.location.x
            p_bottom = p.location.y
            px = int(round((p_left - min_x) * 1000))
            py = int(round((p_bottom - min_y) * 1000))
            placements[h_id] = (px, py)
            iw, ih = img.size[0], img.size[1]
            src = np.empty(iw * ih * 4, dtype=np.float32)
            img.pixels.foreach_get(src)
            if py + ih <= final_h and px + iw <= final_w and py >= 0 and px >= 0:
                atlas[py : py + ih, px : px + iw] = src.reshape((ih, iw, 4))

        apply_results(objs, atlas.flatten(), final_w, final_h, mat_info, unique_imgs, placements)
        bpy.data.collections.remove(col, do_unlink=True)
        restore_viewport(context)
        bpy.ops.object.select_all(action='DESELECT')
        for obj in objs:
            obj.select_set(True)
            context.view_layer.objects.active = obj
        self.report({'INFO'}, f"交互式合成完成！尺寸: {final_w}x{final_h}")
        return {'FINISHED'}

# =============================================================================
# 4. UI 面板
# =============================================================================
class XXMI_PT_MergeUVPanel(Panel):
    bl_label = "UV贴图合并工具"
    bl_idname = "XXMI_PT_MergeUVPanel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "XXMI_PT_Sidebar"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 5

    def draw(self, context):
        layout = self.layout
        if not hasattr(context.scene, "xxmi_merge_uv_props"):
            layout.label(text="需重启插件或重载脚本", icon="ERROR")
            return
        props = context.scene.xxmi_merge_uv_props
        # 尺寸规则 + 一键推荐
        rule_row = layout.row(align=True)
        rule_row.prop(props, "size_mode_w", text="宽")
        rule_row.prop(props, "size_mode_h", text="高")
        rule_row.operator("xxmi.muv_recommend_rules", text="", icon='LIGHT')
        layout.separator()
        layout.prop(props, "workflow_tab", expand=True)
        if props.workflow_tab == 'AUTO':
            box = layout.box()
            box.operator("xxmi.muv_calculate_size", icon='FILE_REFRESH')
            row = box.row(align=True)
            row.prop(props, "target_width")
            row.prop(props, "target_height")
            box.operator("xxmi.muv_execute_auto_merge", icon='PLAY')
        else:
            box = layout.box()
            box.operator("xxmi.muv_start_layout", icon='VIEW_ORTHO')
            box.operator("xxmi.muv_confirm_layout", icon='CHECKMARK')
            box.separator()
            box.label(text="操作提示:", icon='INFO')
            box.label(text="贴图面片摆放完毕后，")
            box.label(text="选中所有面片执行合并。")

# =============================================================================
# 5. 注册
# =============================================================================
classes = (
    XXMI_MergeUVProperties,
    XXMI_OT_MUV_CalculateSize,
    XXMI_OT_MUV_RecommendRules,
    XXMI_OT_MUV_ExecuteAutoMerge,
    XXMI_OT_MUV_StartLayout,
    XXMI_OT_MUV_ConfirmLayout,
    XXMI_PT_MergeUVPanel,
)

def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass
    if not hasattr(bpy.types.Scene, "xxmi_merge_uv_props"):
        bpy.types.Scene.xxmi_merge_uv_props = PointerProperty(type=XXMI_MergeUVProperties)

def unregister():
    if hasattr(bpy.types.Scene, "xxmi_merge_uv_props"):
        del bpy.types.Scene.xxmi_merge_uv_props
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass

if __name__ == "__main__":
    register()