import bpy
import bmesh
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import IntProperty, CollectionProperty, PointerProperty, EnumProperty

# =============================================================================
# 1. 数据结构 (用于缝合工具 & 属性传递)
# =============================================================================

class XXMI_VertexItem(PropertyGroup):
    index: IntProperty()

class XXMI_ElementGroup(PropertyGroup):
    vertices: CollectionProperty(type=XXMI_VertexItem)

class XXMI_TransferPropsSettings(PropertyGroup):
    transfer_mode: EnumProperty(
        name="模式",
        description="选择传递模式",
        items=[
            ('OBJECT', "物体对物体 (单体)", "在两个特定物体之间传递数据"),
            ('COLLECTION', "集合对集合 (批量)", "在两个集合之间根据物体名称自动匹配并传递数据")
        ],
        default='OBJECT'
    )
    
    # 物体模式用的属性
    source_object: PointerProperty(
        name="源物体",
        type=bpy.types.Object,
        description="包含原始属性和变换数据的物体"
    )
    target_object: PointerProperty(
        name="目标物体",
        type=bpy.types.Object,
        description="将要接收属性的物体（旧属性会被覆盖）"
    )
    
    # 集合模式用的属性
    source_collection: PointerProperty(
        name="源集合",
        type=bpy.types.Collection,
        description="包含原始物体的集合"
    )
    target_collection: PointerProperty(
        name="目标集合",
        type=bpy.types.Collection,
        description="包含需要修改的物体的集合"
    )

# =============================================================================
# 2. 属性传递工具逻辑 (新增)
# =============================================================================

class XXMI_OT_TransferProperties(Operator):
    bl_idname = "xxmi.transfer_properties"
    bl_label = "执行属性传递"
    bl_description = "传递自定义属性(Custom Props)和变换数据(位置/旋转/缩放)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.xxmi_transfer_settings
        mode = settings.transfer_mode

        # ==================== 集合模式 ====================
        if mode == 'COLLECTION':
            base_collection = settings.source_collection
            target_collection = settings.target_collection

            if not base_collection or not target_collection:
                self.report({'ERROR'}, "错误：请同时选择【源集合】和【目标集合】。")
                return {'CANCELLED'}

            # 建立源物体字典，用于名称匹配
            # 逻辑：去除名称末尾的 "-1", ".001" 等后缀
            base_prefix_dict = {}
            for base_obj in base_collection.objects:
                prefix = base_obj.name.rsplit("-", 1)[0].rsplit(".", 1)[0]  
                base_prefix_dict[prefix] = base_obj

            count = 0
            for target_obj in target_collection.objects:
                target_prefix = target_obj.name.rsplit("-", 1)[0].rsplit(".", 1)[0]  
                
                if target_prefix in base_prefix_dict:
                    base_obj = base_prefix_dict[target_prefix]

                    # 1. 清理目标物体旧属性 (保留UI定义)
                    for key in list(target_obj.keys()):
                        if key not in '_RNA_UI':  
                            del target_obj[key]

                    # 2. 复制自定义属性
                    for key in base_obj.keys():
                        target_obj[key] = base_obj[key]
                    
                    # 3. 复制变换数据
                    target_obj.location = base_obj.location
                    target_obj.rotation_euler = base_obj.rotation_euler
                    target_obj.scale = base_obj.scale  

                    count += 1
                    print(f"[传递成功] 从: {base_obj.name} -> 到: {target_obj.name}")

            if count == 0:
                self.report({'WARNING'}, "未找到名称匹配的物体，未进行任何操作。")
            else:
                self.report({'INFO'}, f"批量处理完成：共成功传递 {count} 个物体。")

        # ==================== 物体模式 ====================
        else:
            base_obj = settings.source_object    
            target_obj = settings.target_object

            if not base_obj or not target_obj:
                self.report({'ERROR'}, "错误：请同时选择【源物体】和【目标物体】。")
                return {'CANCELLED'}

            # 1. 清理目标物体旧属性
            for key in list(target_obj.keys()):
                if key not in '_RNA_UI': 
                    del target_obj[key]

            # 2. 复制自定义属性
            for key in base_obj.keys():
                target_obj[key] = base_obj[key]

            # 3. 复制变换数据
            target_obj.location = base_obj.location
            target_obj.rotation_euler = base_obj.rotation_euler
            target_obj.scale = base_obj.scale  

            log_message = f"属性传递完成：'{base_obj.name}' -> '{target_obj.name}'"
            print(log_message)
            self.report({'INFO'}, log_message)

        return {'FINISHED'}

# =============================================================================
# 3. 顶点冻结工具逻辑
# =============================================================================

# 全局变量用于存储冻结的坐标
frozen_vertex_positions = {}

class XXMI_OT_FreezeSelectedVertices(Operator):
    bl_idname = "xxmi.freeze_selected_vertices"
    bl_label = "冻结选中顶点位置"
    bl_description = "记录当前选中顶点的坐标 (Edit模式)"
    bl_options = {'REGISTER', 'UNDO'}
    
    def execute(self, context):
        obj = context.object
        if not obj or obj.mode != 'EDIT':
            self.report({'WARNING'}, "请在 Edit 模式中选择要冻结的顶点")
            return {'CANCELLED'}

        # 切换到对象模式以获取更新后的选中状态
        bpy.ops.object.mode_set(mode='OBJECT')
        frozen_vertex_positions.clear()

        count = 0
        for v in obj.data.vertices:
            if v.select:
                frozen_vertex_positions[v.index] = v.co.copy()
                count += 1

        # 自动切回雕刻模式方便操作，如果不需要可改为保持 OBJECT 或切回 EDIT
        bpy.ops.object.mode_set(mode='SCULPT')
        self.report({'INFO'}, f"已冻结 {count} 个顶点位置 (现可进行雕刻)")
        return {'FINISHED'}

class XXMI_OT_RestoreFrozenVertices(Operator):
    bl_idname = "xxmi.restore_frozen_vertices"
    bl_label = "恢复冻结顶点位置"
    bl_description = "将之前冻结的顶点恢复到记录的位置"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        # 允许在 Sculpt 或 Object 模式下恢复
        if obj.mode not in {'SCULPT', 'OBJECT'}:
            self.report({'WARNING'}, "请在 Sculpt 或 Object 模式中恢复顶点")
            return {'CANCELLED'}

        if not frozen_vertex_positions:
            self.report({'WARNING'}, "没有记录冻结的顶点信息")
            return {'CANCELLED'}

        # 如果在雕刻模式，需要确保数据更新
        if obj.mode == 'SCULPT':
            bpy.ops.object.mode_set(mode='OBJECT')
            
        for idx, co in frozen_vertex_positions.items():
            if idx < len(obj.data.vertices):
                obj.data.vertices[idx].co = co.copy()

        obj.data.update()
        
        # 恢复回原来的模式 (假设主要是为了雕刻)
        bpy.ops.object.mode_set(mode='SCULPT')
        
        self.report({'INFO'}, f"已恢复 {len(frozen_vertex_positions)} 个顶点的位置")
        return {'FINISHED'}

# =============================================================================
# 4. 缝合工具逻辑
# =============================================================================

class BaseElementMarker:
    @classmethod
    def get_elements(cls, bm, selected_edges=None, selected_verts=None, selected_faces=None):
        """获取选中的顶点索引去重列表"""
        elements = []
        if selected_edges:
            elements.extend([v.index for e in selected_edges for v in e.verts])
        if selected_verts:
            elements.extend([v.index for v in selected_verts])
        if selected_faces:
            elements.extend([v.index for f in selected_faces for v in f.verts])
        return list(set(elements))

class XXMI_OT_MarkFixedElements(Operator, BaseElementMarker):
    bl_idname = "xxmi.mark_fixed_elements"
    bl_label = "标记固定元素"
    bl_description = "标记作为目标位置的顶点 (不移动)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        if obj and obj.type == 'MESH' and obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            selected_edges = [e for e in bm.edges if e.select]
            selected_verts = [v for v in bm.verts if v.select]
            selected_faces = [f for f in bm.faces if f.select]
            
            fixed_elements = self.get_elements(bm, selected_edges, selected_verts, selected_faces)
            
            if not fixed_elements:
                self.report({'ERROR'}, "未选中任何元素")
                return {'CANCELLED'}
            
            context.scene.xxmi_fixed_elements.vertices.clear()
            for idx in fixed_elements:
                item = context.scene.xxmi_fixed_elements.vertices.add()
                item.index = idx
                
            self.report({'INFO'}, f"已标记固定点: {len(fixed_elements)} 个")
            return {'FINISHED'}
        return {'CANCELLED'}

class XXMI_OT_MarkMovingElements(Operator, BaseElementMarker):
    bl_idname = "xxmi.mark_moving_elements"
    bl_label = "标记移动元素"
    bl_description = "标记需要移动并缝合的顶点"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        if obj and obj.type == 'MESH' and obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            selected_edges = [e for e in bm.edges if e.select]
            selected_verts = [v for v in bm.verts if v.select]
            selected_faces = [f for f in bm.faces if f.select]
            
            moving_elements = self.get_elements(bm, selected_edges, selected_verts, selected_faces)
            
            if not moving_elements:
                self.report({'ERROR'}, "未选中任何元素")
                return {'CANCELLED'}
            
            context.scene.xxmi_moving_elements.vertices.clear()
            for idx in moving_elements:
                item = context.scene.xxmi_moving_elements.vertices.add()
                item.index = idx
                
            self.report({'INFO'}, f"已标记移动点: {len(moving_elements)} 个")
            return {'FINISHED'}
        return {'CANCELLED'}

class XXMI_OT_MergeElements(Operator):
    bl_idname = "xxmi.merge_elements"
    bl_label = "合并到固定元素"
    bl_description = "将移动点吸附到最近的固定点并合并"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        if not obj or obj.type != 'MESH' or obj.mode != 'EDIT':
            self.report({'ERROR'}, "需要在编辑模式下操作")
            return {'CANCELLED'}
            
        bm = bmesh.from_edit_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        
        # 使用自定义的 xxmi 属性
        fixed_indices = [i.index for i in context.scene.xxmi_fixed_elements.vertices]
        moving_indices = [i.index for i in context.scene.xxmi_moving_elements.vertices]
        
        # 安全检查：防止索引越界 (如果模型拓扑改变过)
        total_verts = len(bm.verts)
        fixed_elements = [bm.verts[i] for i in fixed_indices if i < total_verts]
        moving_elements = [bm.verts[i] for i in moving_indices if i < total_verts]
        
        if not fixed_elements or not moving_elements:
            self.report({'ERROR'}, "请先标记固定元素和移动元素")
            return {'CANCELLED'}
        
        # 核心合并逻辑
        for mv in moving_elements:
            # 找到距离最近的固定点
            closest = min(fixed_elements, key=lambda fv: (mv.co - fv.co).length)
            mv.co = closest.co  # 移动位置
            
        # 合并重复顶点
        unique_verts = list(set(moving_elements + fixed_elements))
        # Remove doubles
        bmesh.ops.remove_doubles(bm, verts=unique_verts, dist=0.0001)
        
        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, "缝合完成")
        return {'FINISHED'}

# =============================================================================
# 5. 统一面板 (UI)
# =============================================================================

class XXMI_PT_MeshTools(Panel):
    bl_label = "网格工具"
    bl_idname = "XXMI_PT_MeshTools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "XXMI_PT_Sidebar"
    bl_order = 10
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        # --- 模块1: 属性传递 ---
        box_transfer = layout.box()
        box_transfer.label(text="属性传递", icon='IMPORT')
        
        settings = context.scene.xxmi_transfer_settings
        box_transfer.prop(settings, "transfer_mode", text="")
        
        if settings.transfer_mode == 'OBJECT':
            col = box_transfer.column(align=True)
            col.prop(settings, "source_object", text="源 (原始)", icon='EXPORT')
            col.prop(settings, "target_object", text="目标 (接收)", icon='IMPORT')
        else:
            col = box_transfer.column(align=True)
            col.prop(settings, "source_collection", text="源集合", icon='EXPORT')
            col.prop(settings, "target_collection", text="目标集合", icon='IMPORT')
            col.label(text="忽略后缀 (.001, -1)", icon='INFO')

        # 按钮
        sub = box_transfer.column()
        sub.scale_y = 1.2
        if settings.transfer_mode == 'OBJECT':
            if not settings.source_object or not settings.target_object:
                sub.enabled = False
        else:
            if not settings.source_collection or not settings.target_collection:
                sub.enabled = False
        
        sub.operator("xxmi.transfer_properties", text="开始传递数据", icon='FILE_REFRESH')

        layout.separator()

        # --- 模块2: 顶点冻结 ---
        box_freeze = layout.box()
        box_freeze.label(text="顶点冻结 (雕刻保护)", icon='FREEZE')
        
        col_f = box_freeze.column(align=True)
        col_f.label(text="1. Edit模式选中顶点", icon='EDITMODE_HLT')
        col_f.operator("xxmi.freeze_selected_vertices", text="冻结选中")
        
        col_f.separator()
        col_f.label(text="2. Sculpt后恢复", icon='SCULPTMODE_HLT')
        col_f.operator("xxmi.restore_frozen_vertices", text="还原位置")

        layout.separator()

        # --- 模块3: 缝合工具 ---
        box_merge = layout.box()
        box_merge.label(text="缝合工具 (Sora_)", icon='AUTOMERGE_ON')
        
        col_m = box_merge.column(align=True)
        col_m.operator("xxmi.mark_fixed_elements", text="A. 标记固定目标")
        col_m.operator("xxmi.mark_moving_elements", text="B. 标记移动来源")
        col_m.separator()
        col_m.operator("xxmi.merge_elements", text="执行吸附合并", icon='MOD_SHRINKWRAP')

        # 帮助提示
        col_help = box_merge.column(align=True)
        col_help.enabled = False
        col_help.scale_y = 0.8
        col_help.label(text="步骤: 对齐 -> 标记目标 ->")
        col_help.label(text="标记来源(细分更多点) -> 合并")

# =============================================================================
# 6. 注册逻辑
# =============================================================================

classes = (
    XXMI_VertexItem,
    XXMI_ElementGroup,
    XXMI_TransferPropsSettings,
    XXMI_OT_TransferProperties,
    XXMI_OT_FreezeSelectedVertices,
    XXMI_OT_RestoreFrozenVertices,
    XXMI_OT_MarkFixedElements,
    XXMI_OT_MarkMovingElements,
    XXMI_OT_MergeElements,
    XXMI_PT_MeshTools,
)

def register():
    # 注册类
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass

    # 注册场景属性
    bpy.types.Scene.xxmi_fixed_elements = PointerProperty(type=XXMI_ElementGroup)
    bpy.types.Scene.xxmi_moving_elements = PointerProperty(type=XXMI_ElementGroup)
    # 新增：属性传递设置
    bpy.types.Scene.xxmi_transfer_settings = PointerProperty(type=XXMI_TransferPropsSettings)

def unregister():
    # 移除属性
    if hasattr(bpy.types.Scene, "xxmi_fixed_elements"):
        del bpy.types.Scene.xxmi_fixed_elements
    if hasattr(bpy.types.Scene, "xxmi_moving_elements"):
        del bpy.types.Scene.xxmi_moving_elements
    if hasattr(bpy.types.Scene, "xxmi_transfer_settings"):
        del bpy.types.Scene.xxmi_transfer_settings
        
    # 注销类
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass