import bpy
import traceback

# =============================================================================
# 1. 核心操作类：全场景自动替换导出
# =============================================================================
class XXMI_OT_ExportWithAutoFill(bpy.types.Operator):
    bl_idname = "xxmi.export_with_autofill"
    bl_label = "导出可见模型 (自动补全顶点组)"
    bl_description = "处理当前场景所有可见的网格（补全缺失的顶点组序号并排序），然后调用导出器，最后还原场景。"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # --- 0. 初始化与记录状态 ---
        # 记录原始选择，以便最后恢复
        original_selection = context.selected_objects
        original_active = context.active_object
        
        # 记录并强制修改插件的导出设置
        scene = context.scene
        if not hasattr(scene, "xxmi"):
             self.report({'ERROR'}, "未找到 XXMI 插件设置，请确保插件已正确加载。")
             return {'CANCELLED'}
             
        xxmi_settings = scene.xxmi
        
        # 记录原始设置
        original_only_selected_setting = xxmi_settings.only_selected
        original_ignore_hidden_setting = xxmi_settings.ignore_hidden
        
        # 强制设置为：仅导出选中 (因为我们将手动选中处理后的临时物体)
        xxmi_settings.only_selected = True 
        
        # 确保处于物体模式
        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        # 用于追踪修改过的物体对列表： [(original_obj, temp_obj, original_name), ...]
        processed_pairs = []
        
        try:
            # --- 1. 扫描目标：当前场景所有可见的网格 ---
            target_objects = [
                obj for obj in context.view_layer.objects 
                if obj.type == 'MESH' and obj.visible_get()
            ]
            
            if not target_objects:
                self.report({'ERROR'}, "场景中没有可见的网格物体可导出。")
                return {'CANCELLED'}

            self.report({'INFO'}, f"正在处理 {len(target_objects)} 个可见网格...")

            # --- 2. 偷天换日：创建替身 ---
            for obj in target_objects:
                # A. 记录原始名字
                original_name = obj.name
                
                # B. 将原始物体改名“隐身”
                # 这一步至关重要：防止导出器通过名字找到原始物体
                obj.name = original_name + "_XXMI_HIDDEN"
                
                # C. 创建临时副本 (深拷贝 Mesh 数据)
                temp_mesh = obj.data.copy()
                temp_obj = obj.copy()
                temp_obj.data = temp_mesh
                
                # D. 让临时副本“顶包”：使用原始名字
                temp_obj.name = original_name 
                
                # E. 链接到原始物体所在的集合
                # 这样可以保持层级结构，防止导出器因集合结构改变而报错
                for col in obj.users_collection:
                    if temp_obj.name not in col.objects:
                        col.objects.link(temp_obj)
                
                # 记录这一对，方便后续清理
                processed_pairs.append((obj, temp_obj, original_name))
                
                # --- 3. 核心逻辑 (补全 & 排序) ---
                
                # 3.1 补全顶点组 (0 ~ Max)
                max_id = -1
                existing_names = set()
                
                # 扫描现有数字组
                for vg in temp_obj.vertex_groups:
                    if vg.name.isdigit():
                        gid = int(vg.name)
                        existing_names.add(gid)
                        if gid > max_id:
                            max_id = gid
                
                # 填补空缺 (默认权重为0，不影响模型外观)
                if max_id >= 0:
                    for i in range(max_id + 1):
                        if i not in existing_names:
                            temp_obj.vertex_groups.new(name=str(i))
                
                # 3.2 排序 (安全检查：只有当顶点组数量大于1时才需要排序)
                if len(temp_obj.vertex_groups) > 1:
                    # 必须选中当前物体才能执行 ops
                    bpy.ops.object.select_all(action='DESELECT')
                    temp_obj.select_set(True)
                    context.view_layer.objects.active = temp_obj
                    
                    try:
                        # Blender 按名称排序能正确处理数字 (0, 1, 2, 10...)
                        bpy.ops.object.vertex_group_sort(sort_type='NAME')
                    except Exception as e:
                        print(f"[XXMI] 警告: {temp_obj.name} 顶点组排序跳过 ({str(e)})")

            # --- 4. 准备导出环境 ---
            # 此时场景里，原始物体名字变成了 _HIDDEN，临时物体名字是正常的。
            # 我们只选中所有临时物体。
            bpy.ops.object.select_all(action='DESELECT')
            for orig, temp, name in processed_pairs:
                temp.select_set(True)
            
            # 设置激活物体 (防止导出器需要上下文)
            if processed_pairs:
                context.view_layer.objects.active = processed_pairs[0][1]

            # --- 5. 调用原始导出器 ---
            self.report({'INFO'}, "正在调用原始导出器...")
            
            # 调用插件原本的导出命令
            # 因为 xxmi_settings.only_selected = True，且只有临时物体被选中
            # 导出器会认为这些临时物体就是我们要导出的内容
            bpy.ops.xxmi.exportadvanced('INVOKE_DEFAULT')
            
            self.report({'INFO'}, "导出流程完成！")
            
        except Exception as e:
            self.report({'ERROR'}, f"自动填充导出失败: {str(e)}")
            traceback.print_exc()
            
        finally:
            # --- 6. 战场打扫 (还原一切) ---
            # 无论成功失败，必须执行，否则场景会乱套
            
            if context.mode != 'OBJECT':
                bpy.ops.object.mode_set(mode='OBJECT')
            
            # A. 销毁临时物体，还原原始物体名字
            for orig_obj, temp_obj, original_name in processed_pairs:
                # 1. 删除临时物体及其 Mesh 数据
                try:
                    temp_mesh = temp_obj.data
                    bpy.data.objects.remove(temp_obj, do_unlink=True)
                    if temp_mesh:
                        bpy.data.meshes.remove(temp_mesh, do_unlink=True)
                except:
                    print(f"警告: 删除临时物体失败 {original_name}")

                # 2. 还原原始物体名字
                try:
                    orig_obj.name = original_name
                except:
                    print(f"警告: 还原原始物体名称失败 {original_name}")

            # B. 恢复原始选择状态
            try:
                bpy.ops.object.select_all(action='DESELECT')
                for obj in original_selection:
                    # 检查物体是否还存在
                    if obj.name in context.scene.objects:
                        obj.select_set(True)
                if original_active and original_active.name in context.scene.objects:
                    context.view_layer.objects.active = original_active
            except:
                pass
            
            # C. 恢复插件设置
            try:
                xxmi_settings.only_selected = original_only_selected_setting
                xxmi_settings.ignore_hidden = original_ignore_hidden_setting
            except:
                pass

        return {'FINISHED'}


# =============================================================================
# 2. 独立 UI 面板
# =============================================================================
class XXMI_PT_AutoFillPanel(bpy.types.Panel):
    """Creates a separate panel for Auto-Fill Export"""
    bl_label = "自动补充顶点组导出"
    bl_idname = "XXMI_PT_AutoFillPanel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "XXMI_PT_Sidebar" 
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        
        # 安全获取属性
        if hasattr(context.scene, "xxmi"):
            xxmi = context.scene.xxmi
            # 检查是否有导出动作被勾选
            if not xxmi.write_buffers and not xxmi.write_ini and not xxmi.copy_textures:
                layout.label(text="请先配置导出设置", icon="INFO")
                layout.enabled = False
        
        row = layout.row()
        row.scale_y = 1.5
        row.operator("xxmi.export_with_autofill", text="导出可见模型", icon='ARMATURE_DATA')
        
        layout.label(text="* 自动补全 0-Max 顶点组并导出", icon="INFO")