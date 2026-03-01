import bpy
import os
import re

# --- 核心逻辑函数 ---

def clean_texture_name(original_name):
    """
    双重清洗名称：
    1. 去除 Blender 自动生成的数字后缀 (如 .001)
    2. 去除文件扩展名 (如 .png)
    例子: "Skin.png.001" -> "Skin.png" -> "Skin"
    """
    if not original_name:
        return "Unknown"
        
    # 1. 使用正则去掉末尾的 .数字 (例如 .001, .012)
    name_no_number = re.sub(r'\.\d+$', '', original_name)
    
    # 2. 去除文件扩展名
    clean_name = os.path.splitext(name_no_number)[0]
    
    return clean_name

def get_base_color_image_name(obj):
    """
    提取物体材质的基础色贴图名称
    """
    if obj.type != 'MESH' or not obj.active_material:
        return None
    mat = obj.active_material
    if not mat.use_nodes:
        return None
    
    node_tree = mat.node_tree
    bsdf_node = None
    for node in node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            bsdf_node = node
            break
    
    if not bsdf_node:
        return None

    base_color_socket = bsdf_node.inputs.get('Base Color')
    if base_color_socket and base_color_socket.is_linked:
        link_node = base_color_socket.links[0].from_node
        if link_node.type == 'TEX_IMAGE' and link_node.image:
            return link_node.image.name
            
    return None

def organize_object_into_subcollection(obj, texture_name, collection_cache):
    """
    在物体当前的父集合中创建子集合，并移动物体
    """
    final_col_name = clean_texture_name(texture_name)
    
    # 获取物体当前所在的所有集合
    current_collections = list(obj.users_collection)
    
    for parent_col in current_collections:
        # 如果已经在目标名字的集合里，跳过
        if parent_col.name == final_col_name:
            continue
            
        # --- 核心逻辑：获取或创建子集合 ---
        target_sub_col = None
        # 缓存Key使用清洗后的名字
        cache_key = (parent_col.name, final_col_name)
        
        if cache_key in collection_cache:
            target_sub_col = collection_cache[cache_key]
        else:
            if final_col_name in parent_col.children:
                target_sub_col = parent_col.children[final_col_name]
            else:
                target_sub_col = bpy.data.collections.new(final_col_name)
                parent_col.children.link(target_sub_col)
            
            collection_cache[cache_key] = target_sub_col
            
        # --- 移动物体 ---
        if obj.name not in target_sub_col.objects:
            target_sub_col.objects.link(obj)
            
        try:
            parent_col.objects.unlink(obj)
        except RuntimeError:
            pass

# --- Blender 操作类 (Operator) ---

class XXMI_OT_MMDTextureGroup(bpy.types.Operator):
    """根据贴图名称将模型部件分组"""
    bl_idname = "xxmi.mmd_texture_group"
    bl_label = "MMD材质分组"
    bl_description = "根据贴图自动分组物体。\n注意：请确保选中模型材质已通过MMDTools转换为Blender原理化BSDF材质节点"
    bl_options = {'REGISTER', 'UNDO'}


    def execute(self, context):
        # 初始化缓存（每次运行清空，防止跨次运行的残留）
        collection_cache = {}
        
        selected_objects = context.selected_objects

        if not selected_objects:
            self.report({'WARNING'}, "请先选中要分组的模型部件！")
            return {'CANCELLED'}
        
        count = 0
        success_count = 0
        
        for obj in selected_objects:
            img_name = get_base_color_image_name(obj)
            
            if img_name:
                organize_object_into_subcollection(obj, img_name, collection_cache)
                success_count += 1
            else:
                pass
            count += 1

        if success_count > 0:
            self.report({'INFO'}, f"整理完成！成功处理 {success_count}/{count} 个物体。")
            # 强制更新视图层以刷新集合层级显示
            context.view_layer.update()
            return {'FINISHED'}
        else:
            self.report({'WARNING'}, "未找到有效的贴图节点，请检查材质是否为原理化BSDF且连接了贴图。")
            return {'CANCELLED'}



class XXMI_PT_MMDTextureGroup(bpy.types.Panel):
    """在N-Panel中显示按钮"""
    bl_label = "MMD 辅助工具"
    bl_idname = "XXMI_PT_mmd_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "XXMI_PT_Sidebar" 
    bl_order = 7
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        
        box = layout.box()
        box.label(text="模型整理", icon='OUTLINER_COLLECTION')
        box.operator("xxmi.mmd_texture_group", icon='GROUP')