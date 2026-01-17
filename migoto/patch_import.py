import bpy
import os
import functools
import sys

# =============================================================================
# 0. 辅助功能：模型清理逻辑
# =============================================================================
def remove_unused_vertex_groups(obj):
    '''
    移除给定obj的未使用的顶点组 (基于用户提供的算法)
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
    """
    植入到原版插件的逻辑
    """
    
    # --- 阶段 0: 参数拦截 (Pre-Execution) ---
    # 如果开启了镜像翻转，强制覆盖原插件的参数
    # self 就是 import operator 的实例，直接修改它的属性即可生效
    if getattr(context.scene, "xxmi_flip_mesh_enabled", False):
        # 只要这里设为 True，原插件就会去执行 X 轴翻转 + 面朝向修正
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
        # 功能 A: 模型清理 (同步执行)
        if getattr(context.scene, "xxmi_cleanup_enabled", False):
            perform_cleanup_job(context)

        # 功能 B: 路径填充 (异步执行)
        if getattr(context.scene, "xxmi_auto_fill_enabled", True):
            try:
                filepath = getattr(self, "filepath", "")
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
    bl_order = 0 

    def draw(self, context):
        layout = self.layout
        
        op_id = "import_mesh.migoto_frame_analysis"
        
        if hasattr(bpy.ops.import_mesh, "migoto_frame_analysis"):
            # 选项区
            col = layout.column(align=True)
            col.prop(context.scene, "xxmi_auto_fill_enabled", text="启用路径自动填充")
            col.prop(context.scene, "xxmi_cleanup_enabled", text="清理模型 (孤立点+无效权重)")
            col.prop(context.scene, "xxmi_flip_mesh_enabled", text="X轴镜像并翻转面 (Flip Mesh)")
            
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
        description="导入时强制应用 Flip Mesh (X轴镜像并翻转缠绕顺序)",
        default=False 
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
    pass
