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
    size_mode: EnumProperty(
        name="尺寸规则",
        items=[('POT', "2的幂次方", ""), ('EVEN', "2的倍数", "")], 
        default='POT'
    )
    target_width: IntProperty(
        name="宽 (W)", 
        default=2048, 
        min=2
    )
    target_height: IntProperty(
        name="高 (H)", 
        default=2048, 
        min=2
    )

# =============================================================================
# 2. 核心算法与辅助函数
# =============================================================================
def get_pot(x):
    return 2 ** math.ceil(math.log2(max(1, x)))

def get_even(x):
    x = int(math.ceil(x))
    return x if x % 2 == 0 else x + 1

def get_image_hash(img):
    if not img or not img.pixels: return None
    if not img.has_data: img.update()
    sample_size = min(len(img.pixels), 10000)
    raw_pixels = np.array(img.pixels[:sample_size])
    return hashlib.md5(raw_pixels.tobytes() + str(img.size[0]).encode()).hexdigest()

def find_node_by_type(material, node_type):
    if not material or not material.use_nodes: return None
    return next((n for n in material.node_tree.nodes if n.type == node_type), None)

def get_base_color_node(material):
    if not material or not material.use_nodes:
        return None, None
    for node in material.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            base_color_input = node.inputs.get("Base Color")
            if base_color_input and base_color_input.is_linked:
                link = base_color_input.links[0]
                if link.from_node.type == 'TEX_IMAGE' and link.from_node.image:
                    return link.from_node, link.from_node.image
    return None, None

def calculate_packing(images_info, target_w, mode):
    sorted_images = sorted(images_info, key=lambda x: x[2], reverse=True)
    max_img_w = max((img[1] for img in sorted_images), default=0)
    actual_w = max(target_w, max_img_w)
    
    if mode == 'POT': actual_w = get_pot(actual_w)
    else: actual_w = get_even(actual_w)
        
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
        
    final_h = max_y
    if mode == 'POT': final_h = get_pot(final_h)
    else: final_h = get_even(final_h)
        
    return actual_w, final_h, placements

def get_assets_from_objects(objs):
    unique_imgs = {}
    mat_to_hash = {}
    for obj in objs:
        if obj.type != 'MESH': continue
        for slot in obj.material_slots:
            mat = slot.material
            if mat and mat.name not in mat_to_hash:
                node, img = get_base_color_node(mat)
                if img:
                    h = get_image_hash(img)
                    mat_to_hash[mat.name] = h
                    if h not in unique_imgs: unique_imgs[h] = img
    return objs, unique_imgs, mat_to_hash

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

def apply_results(objs, flat_pixels, fw, fh, mat_to_hash, unique_imgs, placements):
    # 1. 更新 UV 坐标
    for obj in objs:
        mesh = obj.data
        for poly in mesh.polygons:
            mat = obj.material_slots[poly.material_index].material
            if not mat or mat.name not in mat_to_hash: continue
            h = mat_to_hash[mat.name]
            if h not in placements: continue
            px, py = placements[h]
            img = unique_imgs[h]
            s_u, s_v = img.size[0] / fw, img.size[1] / fh
            o_u, o_v = px / fw, py / fh
            for uv_layer in mesh.uv_layers:
                for idx in poly.loop_indices:
                    uv = uv_layer.data[idx].uv
                    uv.x, uv.y = uv.x * s_u + o_u, uv.y * s_v + o_v

    # 2. 替换材质贴图节点
    for mat_name, img_hash in mat_to_hash.items():
        mat = bpy.data.materials.get(mat_name)
        node, _ = get_base_color_node(mat)
        if node:
            merged_img_name = f"{mat.name}_Merged"
            old_img = bpy.data.images.get(merged_img_name)
            
            # 重命名旧图避免 Blender 缓存与命名冲突
            if old_img:
                old_img.name = merged_img_name + "_Trash"
                
            # 建立尺寸吻合、同名的新图
            new_img = bpy.data.images.new(merged_img_name, width=fw, height=fh, alpha=True)
            new_img.pixels.foreach_set(flat_pixels)
            new_img.update()
            
            # 强制替换节点引用
            node.image = new_img
            
            # 删掉旧数据
            if old_img:
                bpy.data.images.remove(old_img)

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
        
        mode = props.size_mode
        guess_w = get_pot(math.ceil(math.sqrt(total_area))) if mode == 'POT' else get_even(math.ceil(math.sqrt(total_area)))
        
        calc_w, calc_h, _ = calculate_packing(images_info, guess_w, mode)
        props.target_width = calc_w
        props.target_height = calc_h
        return {'FINISHED'}

class XXMI_OT_MUV_ExecuteAutoMerge(Operator):
    bl_idname = "xxmi.muv_execute_auto_merge"
    bl_label = "执行自动合并"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.xxmi_merge_uv_props
        objs, unique_imgs, mat_to_hash = get_unique_assets(context)
        if not unique_imgs: return {'CANCELLED'}

        mode = props.size_mode
        final_w = props.target_width
        
        images_info = [(h_id, img.size[0], img.size[1]) for h_id, img in unique_imgs.items()]
        fw, fh, placements = calculate_packing(images_info, final_w, mode)
        
        atlas = np.zeros((fh, fw, 4), dtype=np.float32)
        for h, (px, py) in placements.items():
            img = unique_imgs[h]
            iw, ih = img.size[0], img.size[1]
            src = np.empty(iw * ih * 4, dtype=np.float32)
            img.pixels.foreach_get(src)
            if py + ih <= fh and px + iw <= fw:
                atlas[py : py + ih, px : px + iw] = src.reshape((ih, iw, 4))
        
        apply_results(objs, atlas.flatten(), fw, fh, mat_to_hash, unique_imgs, placements)
        return {'FINISHED'}


class XXMI_OT_MUV_StartLayout(Operator):
    bl_idname = "xxmi.muv_start_layout"
    bl_label = "1. 生成面片网格"
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
        
        mode = props.size_mode
        final_w = get_pot(total_w_px) if mode == 'POT' else get_even(total_w_px)
        final_h = get_pot(total_h_px) if mode == 'POT' else get_even(total_h_px)

        atlas = np.zeros((final_h, final_w, 4), dtype=np.float32)
        placements = {}
        
        objs, unique_imgs, mat_to_hash = get_assets_from_objects(target_objs)

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

        apply_results(objs, atlas.flatten(), final_w, final_h, mat_to_hash, unique_imgs, placements)
        
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

        layout.prop(props, "size_mode")
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