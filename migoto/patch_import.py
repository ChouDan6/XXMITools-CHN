import bpy
import os
import functools
import sys

# =============================================================================
# 0. 辅助功能：模型清理逻辑 & 材质贴图逻辑
# =============================================================================
def remove_unused_vertex_groups(obj):
    '''
    移除给定obj的未使用的顶点组
    '''
    if obj.type == "MESH":
        obj.update_from_editmode()
        vgroup_used = {i: False for i, k in enumerate(obj.vertex_groups)}

        for v in obj.data.vertices:
            for g in v.groups:
                if g.weight > 0.0:
                    vgroup_used[g.group] = True

        for i, used in sorted(vgroup_used.items(), reverse=True):
            if not used:
                obj.vertex_groups.remove(obj.vertex_groups[i])

def perform_cleanup_job(context):
    """
    执行清理任务
    """
    target_objs = [obj for obj in context.selected_objects if obj.type == 'MESH']
    
    if not target_objs:
        return

    print(f"[XXMI] 开始清理 {len(target_objs)} 个导入物体...")

    if context.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

    for obj in target_objs:
        try:
            context.view_layer.objects.active = obj
            
            # --- A. 清理孤立几何体 ---
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.delete_loose()
            bpy.ops.object.mode_set(mode='OBJECT')
            
            # --- B. 移除未使用顶点组 ---
            remove_unused_vertex_groups(obj)
            
        except Exception as e:
            print(f"[XXMI Error] 清理物体 {obj.name} 失败: {e}")

    print("[XXMI] 清理完成")


def perform_material_texture_job(context, filepath):
    """
    执行材质与贴图处理逻辑:
    1. 材质名使用网格名
    2. 如果有对应贴图则连接 (按 Diffuse, LightMap 等后缀匹配)
    3. 如果找不到图片也新建对应网格名的材质
    """
    target_objs = [obj for obj in context.selected_objects if obj.type == 'MESH']
    if not target_objs:
        return

    dump_dir = os.path.dirname(filepath) if filepath else ""
    if not dump_dir or not os.path.exists(dump_dir):
        return

    files = os.listdir(dump_dir)
    # 参照原项目的贴图类型后缀优先级
    texture_types = ["Diffuse", "LightMap", "NormalMap", "StockingMap", "MaterialMap", "Skill", "DiffuseUlt", "idle", "Back"]
    valid_exts = ('.dds', '.png', '.jpg', '.jpeg', '.tga', '.bmp')

    print(f"[XXMI] 开始为 {len(target_objs)} 个物体处理材质与纹理...")

    for obj in target_objs:
        mesh_name = obj.name
        mat_name = mesh_name  # 强制材质名使用真实的网格名
        
        # 1. 新建或获取以网格名命名的材质
        mat = bpy.data.materials.get(mat_name)
        if not mat:
            mat = bpy.data.materials.new(name=mat_name)
        
        mat.use_nodes = True
        obj.data.materials.clear()
        obj.data.materials.append(mat)
        
        # 2. 文件名去后缀后，检查物体名是否以其开头
        # 例如文件是 BodyDiffuse.dds，前缀是 Body，而物体名可能是 Body.001
        target_img_name = None
        for tex_type in texture_types:
            for f in files:
                fname, ext = os.path.splitext(f)
                if ext.lower() not in valid_exts:
                    continue
                
                # 如果文件名以贴图类型结尾 (如 Diffuse)
                if fname.endswith(tex_type):
                    base_mesh_name = fname[:-len(tex_type)]
                    # 匹配规则：物体名以基础网格名开头即可
                    if mesh_name.startswith(base_mesh_name):
                        target_img_name = f
                        break
            if target_img_name:
                break
        
        # 3. 加载图片并连接节点
        if target_img_name:
            img_path = os.path.join(dump_dir, target_img_name)
            fname_no_ext = os.path.splitext(target_img_name)[0]
            
            # 尝试获取已加载的图片 (原项目可能会把名字去掉扩展名)
            img = bpy.data.images.get(target_img_name) or bpy.data.images.get(fname_no_ext)
            
            # 如果没加载，则尝试加载
            if not img:
                try:
                    from .texturehandling import TextureHandler
                    TextureHandler.convert_dds(context, file=img_path)
                    img = bpy.data.images.get(target_img_name) or bpy.data.images.get(fname_no_ext)
                except ImportError:
                    pass
                except Exception as e:
                    print(f"[XXMI] 原生 TextureHandler 失败，尝试常规加载: {e}")
                    
            if not img:
                try:
                    img = bpy.data.images.load(img_path)
                except Exception as e:
                    print(f"[XXMI Warning] 无法加载图片 {img_path}: {e}")
                    img = None
                    
            if img:
                nodes = mat.node_tree.nodes
                links = mat.node_tree.links
                nodes.clear()
                
                out_node = nodes.new('ShaderNodeOutputMaterial')
                out_node.location = (300, 300)
                
                bsdf = nodes.new('ShaderNodeBsdfPrincipled')
                bsdf.location = (0, 300)
                links.new(bsdf.outputs[0], out_node.inputs[0])
                
                tex_node = nodes.new('ShaderNodeTexImage')
                tex_node.location = (-300, 300)
                tex_node.image = img
                
                # 防止透明度问题，Alpha设为NONE并使用sRGB
                img.alpha_mode = "NONE"
                if hasattr(img, 'colorspace_settings'):
                    img.colorspace_settings.name = 'sRGB'
                
                links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
                print(f"[XXMI] 材质 {mat_name} 已成功连接贴图: {target_img_name}")
            else:
                print(f"[XXMI] 找到图片文件 {target_img_name} 但加载失败，可能需要 dds 插件。")
        else:
            print(f"[XXMI] 材质 {mat_name} 未找到对应图片，已创建纯材质。")


# =============================================================================
# 1. 核心逻辑：路径强制清洗与更新
# =============================================================================
def force_update_dump_path(scene_name, new_path):
    try:
        scene = bpy.data.scenes.get(scene_name)
        if not scene or not hasattr(scene, "xxmi"): 
            return

        scene.xxmi.dump_path = ""
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
        
        final_path = os.path.normpath(new_path)
        scene.xxmi.dump_path = final_path
        print(f"[XXMI] Dump Folder 已自动更新为: {final_path}")
        
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                area.tag_redraw()
                
    except Exception as e:
        print(f"[XXMI Error] 路径更新过程崩溃: {e}")

# =============================================================================
# 2. Hook (钩子) 逻辑
# =============================================================================
OriginalExecute = None

def execute_hook(self, context):
    # --- 阶段 0: 参数拦截 (Pre-Execution) ---
    if getattr(context.scene, "xxmi_flip_mesh_enabled", False):
        self.flip_mesh = True
        print("[XXMI] 已应用 Flip Mesh (X轴镜像+翻转面)")

    # --- 阶段 1: 执行原版导入 ---
    if OriginalExecute:
        try:
            result = OriginalExecute(self, context)
        except Exception as e:
            print(f"[XXMI Error] 原版导入器报错: {e}")
            return {'CANCELLED'}
    else:
        return {'CANCELLED'}

    # --- 阶段 2: 后处理逻辑 (Post-Execution) ---
    if 'FINISHED' in result:
        filepath = getattr(self, "filepath", "")
        
        # 功能 A: 模型清理 (同步执行)
        if getattr(context.scene, "xxmi_cleanup_enabled", False):
            perform_cleanup_job(context)

        # 功能 B: 材质贴图生成与连接 (同步执行)
        if getattr(context.scene, "xxmi_import_textures_enabled", False):
            perform_material_texture_job(context, filepath)

        # 功能 C: 路径填充 (异步执行)
        if getattr(context.scene, "xxmi_auto_fill_enabled", True):
            try:
                if filepath:
                    dump_dir = os.path.dirname(filepath)
                    bpy.app.timers.register(
                        functools.partial(force_update_dump_path, context.scene.name, dump_dir),
                        first_interval=0.1
                    )
            except Exception as e:
                print(f"[XXMI Warning] 路径钩子注册失败: {e}")

    return result

# =============================================================================
# 3. UI 面板
# =============================================================================
class XXMI_PT_ImportPanel(bpy.types.Panel):
    bl_label = "导入辅助"
    bl_idname = "XXMI_PT_ImportPanel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "XXMI_PT_Sidebar"
    bl_order = 1

    def draw(self, context):
        layout = self.layout
        
        op_id = "import_mesh.migoto_frame_analysis"
        
        if hasattr(bpy.ops.import_mesh, "migoto_frame_analysis"):
            # 选项区
            col = layout.column(align=True)
            col.prop(context.scene, "xxmi_auto_fill_enabled", text="启用路径自动填充")
            col.prop(context.scene, "xxmi_cleanup_enabled", text="清理模型 (孤立点+无效权重)")
            col.prop(context.scene, "xxmi_flip_mesh_enabled", text="X轴镜像并翻转面")
            col.prop(context.scene, "xxmi_import_textures_enabled", text="导入材质与贴图")
            
            layout.separator()
            
            # 按钮
            row = layout.row()
            row.scale_y = 1.5
            row.operator(op_id, text="导入模型 (ib.txt+vb.txt)", icon='IMPORT')
        else:
            box = layout.box()
            box.label(text="未检测到 3DMigoto 插件", icon="ERROR")

# =============================================================================
# 4. 注册与注入
# =============================================================================
def register():
    global OriginalExecute
    
    # 1. 注册属性
    bpy.types.Scene.xxmi_auto_fill_enabled = bpy.props.BoolProperty(
        name="Auto-Fill Dump Path",
        description="导入完成后自动将 Dump Folder 设置为文件所在目录",
        default=True
    )
    
    bpy.types.Scene.xxmi_cleanup_enabled = bpy.props.BoolProperty(
        name="Cleanup Imported Mesh",
        description="导入后自动清理孤立点并移除未使用的顶点组",
        default=False 
    )

    bpy.types.Scene.xxmi_flip_mesh_enabled = bpy.props.BoolProperty(
        name="Flip Mesh on Import",
        description="导入时强制应用 Flip Mesh (3DMigoto非镜像)",
        default=False 
    )
    
    bpy.types.Scene.xxmi_import_textures_enabled = bpy.props.BoolProperty(
        name="Import Materials and Textures",
        description="导入后自动创建与网格同名的材质并连接贴图",
        default=True 
    )
    
    # 2. 寻找目标类
    TargetClass = None
    try:
        from . import import_ops
        if hasattr(import_ops, "Import3DMigotoFrameAnalysis"):
            TargetClass = import_ops.Import3DMigotoFrameAnalysis
    except ImportError:
        pass

    if TargetClass is None:
        for name, module in sys.modules.items():
            if 'migoto' in name and 'import_ops' in name:
                if hasattr(module, "Import3DMigotoFrameAnalysis"):
                    TargetClass = getattr(module, "Import3DMigotoFrameAnalysis")
                    break
    
    # 3. 执行注入
    if TargetClass:
        if not hasattr(TargetClass, "xxmi_hooked"):
            OriginalExecute = TargetClass.execute
            TargetClass.execute = execute_hook
            TargetClass.xxmi_hooked = True
            print("[XXMI] 3DMigoto 导入钩子挂载成功")
        else:
            OriginalExecute = TargetClass.execute
            if OriginalExecute != execute_hook:
                 TargetClass.execute = execute_hook
    else:
        print("[XXMI Error] 严重错误：未找到导入类，辅助功能不可用")

def unregister():
    del bpy.types.Scene.xxmi_auto_fill_enabled
    del bpy.types.Scene.xxmi_cleanup_enabled
    del bpy.types.Scene.xxmi_flip_mesh_enabled
    del bpy.types.Scene.xxmi_import_textures_enabled
    pass