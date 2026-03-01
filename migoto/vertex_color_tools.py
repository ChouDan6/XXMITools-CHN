import bpy

# =============================================================================
# 1. 属性定义
# =============================================================================
class XXMI_VertexColorProperties(bpy.types.PropertyGroup):
    a: bpy.props.FloatProperty(name="红 (R)", default=1.0, min=0.0, max=1.0, precision=3)
    b: bpy.props.FloatProperty(name="绿 (G)", default=0.216, min=0.0, max=1.0, precision=3)
    c: bpy.props.FloatProperty(name="蓝 (B)", default=0.216, min=0.0, max=1.0, precision=3)
    d: bpy.props.FloatProperty(name="透明度 (A)", default=0.304, min=0.0, max=1.0, precision=3)
    
    is_ming_chao_selected: bpy.props.BoolProperty(name="是否鸣潮模式", default=False)

# =============================================================================
# 2. 辅助函数
# =============================================================================
def _apply_vertex_color_to_selected(context, operator_instance):
    if not hasattr(context.scene, "xxmi_vertex_color_props"):
        operator_instance.report({'ERROR'}, "插件属性未加载，请重启 Blender")
        return {'CANCELLED'}
        
    props = context.scene.xxmi_vertex_color_props
    color_to_apply = (props.a, props.b, props.c, props.d)
    selected_objects = context.selected_objects

    if not selected_objects:
        operator_instance.report({'WARNING'}, "没有选中的物体！")
        return {'CANCELLED'}

    applied_count = 0
    for obj in selected_objects:
        if obj.type == 'MESH':
            # 移除现有的COLOR
            if "COLOR" in obj.data.attributes:
                obj.data.attributes.remove(obj.data.attributes["COLOR"])
            
            # 创建新的 COLOR
            color_attr = obj.data.attributes.new(name="COLOR", domain='CORNER', type='BYTE_COLOR')
            for i in range(len(color_attr.data)):
                color_attr.data[i].color = color_to_apply

            # 鸣潮特殊处理
            if props.is_ming_chao_selected:
                if "COLOR1" in obj.data.attributes:
                    obj.data.attributes.remove(obj.data.attributes["COLOR1"])
                
                color_attr1 = obj.data.attributes.new(name="COLOR1", domain='CORNER', type='BYTE_COLOR')
                for i in range(len(color_attr1.data)):
                    color_attr1.data[i].color = color_to_apply
                operator_instance.report({'INFO'}, f"已应用鸣潮双层顶点色: {obj.name}")
            else:
                if "COLOR1" in obj.data.attributes:
                    obj.data.attributes.remove(obj.data.attributes["COLOR1"])
                    operator_instance.report({'INFO'}, f"已应用顶点色 (并清理 COLOR1): {obj.name}")
                else:
                    operator_instance.report({'INFO'}, f"已应用顶点色: {obj.name}")
            applied_count +=1
        else:
            operator_instance.report({'WARNING'}, f"{obj.name} 不是网格物体，已跳过")
            
    if applied_count == 0:
        operator_instance.report({'WARNING'}, "未找到有效的网格物体。")
        return {'CANCELLED'}
    
    return {'FINISHED'}

# =============================================================================
# 3. Operators
# =============================================================================

# 原神
class XXMI_OT_SetDefaultColorYS(bpy.types.Operator):
    bl_idname = "xxmi.set_default_color_ys"
    bl_label = "设为原神默认"
    
    def execute(self, context):
        props = context.scene.xxmi_vertex_color_props
        props.a = 1.0
        props.b = 0.216
        props.c = 0.216
        props.d = 0.302
        props.is_ming_chao_selected = False
        return _apply_vertex_color_to_selected(context, self)

# 崩铁
class XXMI_OT_SetDefaultColorBT(bpy.types.Operator):
    bl_idname = "xxmi.set_default_color_bt"
    bl_label = "设为崩铁默认"
    
    def execute(self, context):
        props = context.scene.xxmi_vertex_color_props
        props.a = 1.0
        props.b = 0.216
        props.c = 0.0
        props.d = 0.302
        props.is_ming_chao_selected = False
        return _apply_vertex_color_to_selected(context, self)

# ZZZ
class XXMI_OT_SetDefaultColorZZZ(bpy.types.Operator):
    bl_idname = "xxmi.set_default_color_zzz"
    bl_label = "设为绝区零默认"
    
    def execute(self, context):
        props = context.scene.xxmi_vertex_color_props
        props.a = 0.216
        props.b = 0.216
        props.c = 0.0
        props.d = 0.0
        props.is_ming_chao_selected = False
        return _apply_vertex_color_to_selected(context, self)

# 鸣潮
class XXMI_OT_SetDefaultColorMT(bpy.types.Operator):
    bl_idname = "xxmi.set_default_color_mt"
    bl_label = "设为鸣潮默认"
    
    def execute(self, context):
        props = context.scene.xxmi_vertex_color_props
        props.a = 0.216
        props.b = 0.216
        props.c = 0.0
        props.d = 0.0
        props.is_ming_chao_selected = True
        return _apply_vertex_color_to_selected(context, self)

# 应用当前颜色
class XXMI_OT_AddColorAttribute(bpy.types.Operator):
    bl_idname = "xxmi.add_color_attribute"
    bl_label = "应用顶点色"
    
    def execute(self, context):
        return _apply_vertex_color_to_selected(context, self)

# UV 重命名
class XXMI_OT_UVName(bpy.types.Operator):
    bl_idname = "xxmi.uv_name"
    bl_label = "UV重命名"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        selected_objects = context.selected_objects
        count = 0
        for obj in selected_objects:
            if obj.type == 'MESH':
                for i, uv_map in enumerate(obj.data.uv_layers):
                    new_name = f"TEXCOORD{i}.xy" if i > 0 else "TEXCOORD.xy"
                    if uv_map.name != new_name:
                        uv_map.name = new_name
                        count += 1
        self.report({'INFO'}, f"已重命名 {count} 个UV层")
        return {'FINISHED'}

# 按材质分离
class XXMI_OT_SeparateByMaterial(bpy.types.Operator):
    bl_idname = "xxmi.separate_by_material"
    bl_label = "材质分离"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        bpy.ops.mesh.separate(type='MATERIAL')
        # 重命名分离后的物体
        for i, obj in enumerate(context.selected_objects):
            if obj.active_material:
                obj.name = obj.active_material.name
            else:
                obj.name = f"Split_Part_{i+1}"
        self.report({'INFO'}, "已按材质分离物体")
        return {'FINISHED'}

# =============================================================================
# 4. UI 面板
# =============================================================================
class XXMI_PT_ExtraToolsPanel(bpy.types.Panel):
    bl_label = "顶点色预设"
    bl_idname = "XXMI_PT_ExtraToolsPanel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "XXMI_PT_Sidebar" 
    

    bl_order = 9 

    def draw(self, context):
        layout = self.layout
        if not hasattr(context.scene, "xxmi_vertex_color_props"):
            layout.label(text="需重启插件", icon="ERROR")
            return
            
        props = context.scene.xxmi_vertex_color_props

        # 自定义颜色区域
        box = layout.box()
        box.label(text="自定义颜色:", icon="COLOR")
        col = box.column(align=True)
        col.prop(props, "a", text="红 (R)")
        col.prop(props, "b", text="绿 (G)")
        col.prop(props, "c", text="蓝 (B)")
        col.prop(props, "d", text="透明度 (A)")
        
        col.separator()
        col.operator("xxmi.add_color_attribute", text="应用顶点色", icon="BRUSH_DATA")

        # 游戏类型预设
        layout.separator()
        layout.label(text="游戏类型:", icon="PRESET")
        grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, even_rows=True)
        grid.operator("xxmi.set_default_color_ys", text="原神")
        grid.operator("xxmi.set_default_color_bt", text="崩铁")
        grid.operator("xxmi.set_default_color_zzz", text="绝区零")
        grid.operator("xxmi.set_default_color_mt", text="鸣潮")

        # 网格工具
        layout.separator()
        layout.label(text="网格工具:", icon="MODIFIER")
        row = layout.row(align=True)
        row.operator("xxmi.uv_name", text="UV重命名", icon="GROUP_UVS")
        row.operator("xxmi.separate_by_material", text="材质分离", icon="OUTLINER_OB_MESH")

# =============================================================================
# 5. 注册
# =============================================================================
def register():
    bpy.types.Scene.xxmi_vertex_color_props = bpy.props.PointerProperty(type=XXMI_VertexColorProperties)

def unregister():
    if hasattr(bpy.types.Scene, "xxmi_vertex_color_props"):
        del bpy.types.Scene.xxmi_vertex_color_props