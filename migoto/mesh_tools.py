import json
import bpy
import bmesh
import numpy
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import IntProperty, CollectionProperty, PointerProperty, EnumProperty


# =============================================================================
# 1. 常量 & 全局变量
# =============================================================================

# 合并对象上记录原始组件信息的自定义属性名
MERGED_COMPONENTS_KEY = "MergedSculpt:Components"

# 顶点冻结工具 — 全局缓存
frozen_vertex_positions = {}


# =============================================================================
# 2. 数据结构 (缝合工具 & 属性传递)
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
# 3. 内部辅助函数 (合并雕刻工具所需)
# =============================================================================

def _deselect_all():
    """取消选择所有对象"""
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    bpy.context.view_layer.objects.active = None


def _select_only(context, obj):
    """只选择并激活指定对象"""
    _deselect_all()
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _ensure_object_mode(context, obj):
    """确保对象处于 OBJECT 模式，返回之前的模式"""
    _select_only(context, obj)
    prev_mode = obj.mode if obj.mode else 'OBJECT'
    if obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    return prev_mode


def _restore_mode(context, obj, mode):
    """恢复对象到指定模式"""
    if obj and mode and mode != 'OBJECT':
        _select_only(context, obj)
        try:
            bpy.ops.object.mode_set(mode=mode)
        except RuntimeError:
            pass


def _copy_object(context, obj, name=None, collection=None):
    """复制对象及其网格数据"""
    new_obj = obj.copy()
    new_obj.data = obj.data.copy()
    if name:
        new_obj.name = name
    if collection:
        collection.objects.link(new_obj)
    else:
        context.scene.collection.objects.link(new_obj)
    return new_obj


def _join_objects(context, objects):
    """将多个对象合并为一个（结果保留在 objects[0]）"""
    if len(objects) <= 1:
        return
    _deselect_all()
    for obj in objects:
        obj.select_set(True)
    context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()


# =============================================================================
# 4. 合并雕刻 — 核心逻辑函数
# =============================================================================

def create_merged_object(context):
    """
    将选中的多个网格对象合并为一个用于雕刻的临时对象。
    不要求对象有任何特殊属性，仅要求选中至少2个网格对象。
    """
    selected_meshes = [obj for obj in context.selected_objects if obj.type == 'MESH']

    if len(selected_meshes) < 2:
        raise ValueError("请至少选择 2 个网格对象！(At least 2 mesh objects must be selected!)")

    col = (selected_meshes[0].users_collection[0]
           if selected_meshes[0].users_collection
           else context.scene.collection)

    vertex_counts = {}
    temp_objects = []
    for obj in selected_meshes:
        vertex_counts[obj.name] = len(obj.data.vertices)
        temp_obj = _copy_object(context, obj, name=f'TEMP_{obj.name}', collection=col)
        temp_objects.append(temp_obj)

    _join_objects(context, temp_objects)

    merged_obj = temp_objects[0]
    merged_obj.name = 'MERGED_OBJECT'
    merged_obj[MERGED_COMPONENTS_KEY] = json.dumps(vertex_counts)

    _select_only(context, merged_obj)

    if (merged_obj.data.shape_keys is not None
            and len(getattr(merged_obj.data.shape_keys, 'key_blocks', [])) > 0):
        merged_obj.active_shape_key_index = 0

    return merged_obj


def transfer_position_data(context, apply_deltas_to_shapekeys=False):
    """
    将合并对象的顶点位置数据传回原始对象。
    支持两种属性名：通用属性名和 WWMI 兼容属性名。
    """
    merged_obj = bpy.context.active_object
    if not merged_obj or merged_obj.mode not in ('SCULPT', 'OBJECT'):
        if len(context.selected_objects) < 1:
            raise ValueError("未选择任何对象！(No object selected!)")
        merged_obj = context.selected_objects[0]

    if merged_obj.type != 'MESH':
        raise ValueError("选中的对象不是网格类型！(Selected object is not a mesh!)")

    merged_object_components = merged_obj.get(MERGED_COMPONENTS_KEY, None)
    if merged_object_components is None:
        merged_object_components = merged_obj.get('WWMI:MergedObjectComponents', None)

    if merged_object_components is None:
        raise ValueError(
            f"对象 '{merged_obj.name}' 缺少合并组件属性！\n"
            f"请先使用 '创建合并对象' 功能。\n"
            f"(Object is missing merged components attribute! "
            f"Use 'Create Merged Object' first.)"
        )

    vertex_counts = json.loads(merged_object_components)

    for obj_name, vertex_count in vertex_counts.items():
        if obj_name not in bpy.data.objects:
            raise ValueError(
                f"找不到原始对象 '{obj_name}'！"
                f"(Original object '{obj_name}' not found!)"
            )
        obj = bpy.data.objects[obj_name]
        if obj.type != 'MESH':
            raise ValueError(
                f"原始对象 '{obj_name}' 不是网格类型！"
                f"(Original object '{obj_name}' is not a mesh!)"
            )
        if len(obj.data.vertices) != vertex_count:
            raise ValueError(
                f"对象 '{obj_name}' 的顶点数 {len(obj.data.vertices)} "
                f"与记录的 {vertex_count} 不匹配！请勿在雕刻期间修改原始对象。\n"
                f"(Object '{obj_name}' vertex count {len(obj.data.vertices)} "
                f"differs from recorded {vertex_count}!)"
            )

    prev_mode = merged_obj.mode
    if prev_mode != 'OBJECT':
        _ensure_object_mode(context, merged_obj)

    # 读取合并对象的顶点坐标
    if (merged_obj.data.shape_keys is None
            or len(getattr(merged_obj.data.shape_keys, 'key_blocks', [])) == 0):
        depsgraph = context.evaluated_depsgraph_get()
        eval_obj = merged_obj.evaluated_get(depsgraph)
        mesh = eval_obj.to_mesh()
        position_data = numpy.empty(len(mesh.vertices), dtype=(numpy.float32, 3))
        mesh.vertices.foreach_get('undeformed_co', position_data.ravel())
        eval_obj.to_mesh_clear()
    else:
        key_block = merged_obj.data.shape_keys.key_blocks[0]
        position_data = numpy.empty(len(key_block.data), dtype=(numpy.float32, 3))
        key_block.data.foreach_get('co', position_data.ravel())

    # 将数据分段写回各个原始对象
    offset = 0
    for obj_name, vertex_count in vertex_counts.items():
        obj = bpy.data.objects[obj_name]
        obj_prev_mode = _ensure_object_mode(context, obj)

        if (obj.data.shape_keys is None
                or len(getattr(obj.data.shape_keys, 'key_blocks', [])) == 0):
            obj.data.vertices.foreach_set('co', position_data[offset:offset + vertex_count].ravel())
        else:
            key_block = obj.data.shape_keys.key_blocks[0]

            if apply_deltas_to_shapekeys:
                original_pos = numpy.empty(len(key_block.data), dtype=(numpy.float32, 3))
                key_block.data.foreach_get('co', original_pos.ravel())
                pos_diff = original_pos - position_data[offset:offset + vertex_count]

                sk_pos = numpy.empty(len(key_block.data), dtype=(numpy.float32, 3))
                for key in obj.data.shape_keys.key_blocks:
                    if key == key_block:
                        continue
                    key.data.foreach_get('co', sk_pos.ravel())
                    sk_pos -= pos_diff
                    key.data.foreach_set('co', sk_pos.ravel())

            key_block.data.foreach_set('co', position_data[offset:offset + vertex_count].ravel())

        obj.data.update()
        _restore_mode(context, obj, obj_prev_mode)
        offset += vertex_count

    # 恢复合并对象的模式
    _select_only(context, merged_obj)
    if prev_mode != 'OBJECT':
        try:
            bpy.ops.object.mode_set(mode=prev_mode)
        except RuntimeError:
            pass


# =============================================================================
# 5. 操作符 — 合并雕刻
# =============================================================================

class XXMI_OT_CreateMergedObject(Operator):
    bl_idname = "xxmi.create_merged_object"
    bl_label = "创建合并对象"
    bl_description = (
        "将选中的网格对象合并为一个临时对象用于雕刻。\n"
        "注意：在完成雕刻之前，请勿增减原始对象的顶点！\n"
        "(Join selected mesh objects into one for sculpting. "
        "Do NOT add/remove vertices in originals until done!)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        mesh_count = sum(1 for obj in context.selected_objects if obj.type == 'MESH')
        return mesh_count >= 2

    def execute(self, context):
        try:
            merged = create_merged_object(context)
            self.report({'INFO'}, f"已创建合并对象: {merged.name}")
        except ValueError as e:
            self.report({'ERROR'}, str(e))
        return {'FINISHED'}


class XXMI_OT_ApplyMergedSculpt(Operator):
    bl_idname = "xxmi.apply_merged_sculpt"
    bl_label = "应用合并雕刻"
    bl_description = (
        "将合并对象的顶点位置传回原始对象。\n"
        "(Transfer vertex positions from merged object back to originals.)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj and obj.type == 'MESH':
            return (obj.get(MERGED_COMPONENTS_KEY) is not None
                    or obj.get('WWMI:MergedObjectComponents') is not None)
        return False

    def execute(self, context):
        try:
            transfer_position_data(context, apply_deltas_to_shapekeys=False)
            self.report({'INFO'}, "已应用合并雕刻到原始对象。")
        except ValueError as e:
            self.report({'ERROR'}, str(e))
        return {'FINISHED'}


class XXMI_OT_ApplyMergedSculptShapeKeys(Operator):
    bl_idname = "xxmi.apply_merged_sculpt_shapekeys"
    bl_label = "应用合并雕刻 (形态键)"
    bl_description = (
        "将合并对象的顶点位置传回原始对象，并将位移差值应用到所有形态键。\n"
        "(Transfer vertex positions and apply deltas to all shape keys.)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj and obj.type == 'MESH':
            return (obj.get(MERGED_COMPONENTS_KEY) is not None
                    or obj.get('WWMI:MergedObjectComponents') is not None)
        return False

    def execute(self, context):
        try:
            transfer_position_data(context, apply_deltas_to_shapekeys=True)
            self.report({'INFO'}, "已应用合并雕刻（含形态键）到原始对象。")
        except ValueError as e:
            self.report({'ERROR'}, str(e))
        return {'FINISHED'}


class XXMI_OT_DeleteMergedObject(Operator):
    bl_idname = "xxmi.delete_merged_object"
    bl_label = "删除合并对象"
    bl_description = (
        "删除合并对象及其网格数据。\n"
        "(Delete the merged object and its mesh data.)"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if obj and obj.type == 'MESH':
            return (obj.get(MERGED_COMPONENTS_KEY) is not None
                    or obj.get('WWMI:MergedObjectComponents') is not None)
        return False

    def execute(self, context):
        obj = context.active_object
        mesh_data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        if mesh_data and mesh_data.users == 0:
            bpy.data.meshes.remove(mesh_data)
        self.report({'INFO'}, "已删除合并对象。")
        return {'FINISHED'}


# =============================================================================
# 6. 操作符 — 属性传递
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

            base_prefix_dict = {}
            for base_obj in base_collection.objects:
                prefix = base_obj.name.rsplit("-", 1)[0].rsplit(".", 1)[0]
                base_prefix_dict[prefix] = base_obj

            count = 0
            for target_obj in target_collection.objects:
                target_prefix = target_obj.name.rsplit("-", 1)[0].rsplit(".", 1)[0]

                if target_prefix in base_prefix_dict:
                    base_obj = base_prefix_dict[target_prefix]

                    for key in list(target_obj.keys()):
                        if key not in '_RNA_UI':
                            del target_obj[key]

                    for key in base_obj.keys():
                        target_obj[key] = base_obj[key]

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

            for key in list(target_obj.keys()):
                if key not in '_RNA_UI':
                    del target_obj[key]

            for key in base_obj.keys():
                target_obj[key] = base_obj[key]

            target_obj.location = base_obj.location
            target_obj.rotation_euler = base_obj.rotation_euler
            target_obj.scale = base_obj.scale

            log_message = f"属性传递完成：'{base_obj.name}' -> '{target_obj.name}'"
            print(log_message)
            self.report({'INFO'}, log_message)

        return {'FINISHED'}


# =============================================================================
# 7. 操作符 — 顶点冻结
# =============================================================================

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

        bpy.ops.object.mode_set(mode='OBJECT')
        frozen_vertex_positions.clear()

        count = 0
        for v in obj.data.vertices:
            if v.select:
                frozen_vertex_positions[v.index] = v.co.copy()
                count += 1

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
        if obj.mode not in {'SCULPT', 'OBJECT'}:
            self.report({'WARNING'}, "请在 Sculpt 或 Object 模式中恢复顶点")
            return {'CANCELLED'}

        if not frozen_vertex_positions:
            self.report({'WARNING'}, "没有记录冻结的顶点信息")
            return {'CANCELLED'}

        if obj.mode == 'SCULPT':
            bpy.ops.object.mode_set(mode='OBJECT')

        for idx, co in frozen_vertex_positions.items():
            if idx < len(obj.data.vertices):
                obj.data.vertices[idx].co = co.copy()

        obj.data.update()
        bpy.ops.object.mode_set(mode='SCULPT')

        self.report({'INFO'}, f"已恢复 {len(frozen_vertex_positions)} 个顶点的位置")
        return {'FINISHED'}


# =============================================================================
# 8. 操作符 — 缝合工具
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

        fixed_indices = [i.index for i in context.scene.xxmi_fixed_elements.vertices]
        moving_indices = [i.index for i in context.scene.xxmi_moving_elements.vertices]

        total_verts = len(bm.verts)
        fixed_elements = [bm.verts[i] for i in fixed_indices if i < total_verts]
        moving_elements = [bm.verts[i] for i in moving_indices if i < total_verts]

        if not fixed_elements or not moving_elements:
            self.report({'ERROR'}, "请先标记固定元素和移动元素")
            return {'CANCELLED'}

        for mv in moving_elements:
            closest = min(fixed_elements, key=lambda fv: (mv.co - fv.co).length)
            mv.co = closest.co

        unique_verts = list(set(moving_elements + fixed_elements))
        bmesh.ops.remove_doubles(bm, verts=unique_verts, dist=0.0001)

        bmesh.update_edit_mesh(obj.data)
        self.report({'INFO'}, "缝合完成")
        return {'FINISHED'}


# =============================================================================
# 9. 面板 (UI) — 统一面板
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

        # ========== 模块1: 合并雕刻 ==========
        box_sculpt = layout.box()
        box_sculpt.label(text="合并雕刻", icon='MOD_BOOLEAN')

        # --- 创建 ---
        mesh_count = sum(1 for obj in context.selected_objects if obj.type == 'MESH')
        row = box_sculpt.row()
        row.operator(XXMI_OT_CreateMergedObject.bl_idname, icon='MESH_DATA')
        if mesh_count < 2:
            row.enabled = False
            box_sculpt.label(text=f"  选择了 {mesh_count} 个网格 (需要 ≥ 2)", icon='INFO')

        # --- 应用 ---
        obj = context.active_object
        is_merged = False
        if obj and obj.type == 'MESH':
            is_merged = (obj.get(MERGED_COMPONENTS_KEY) is not None
                         or obj.get('WWMI:MergedObjectComponents') is not None)

        col = box_sculpt.column(align=True)
        col.operator(XXMI_OT_ApplyMergedSculpt.bl_idname, icon='CHECKMARK')
        col.operator(XXMI_OT_ApplyMergedSculptShapeKeys.bl_idname, icon='SHAPEKEY_DATA')
        col.enabled = is_merged

        if not is_merged:
            box_sculpt.label(text="  请激活一个合并对象", icon='INFO')

        # --- 删除 ---
        row = box_sculpt.row()
        row.operator(XXMI_OT_DeleteMergedObject.bl_idname, icon='X')
        row.enabled = is_merged

        # --- 组件信息 ---
        if is_merged and obj:
            comp_data = obj.get(MERGED_COMPONENTS_KEY) or obj.get('WWMI:MergedObjectComponents')
            if comp_data:
                try:
                    components = json.loads(comp_data)
                    info_box = box_sculpt.box()
                    info_box.label(text="Merged Components:", icon='OUTLINER_OB_GROUP_INSTANCE')
                    for name, vcount in components.items():
                        info_box.label(text=f"  {name}  ({vcount} verts)")
                except (json.JSONDecodeError, TypeError):
                    pass

        layout.separator()

        # ========== 模块2: 属性传递 ==========
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

        # ========== 模块3: 顶点冻结 ==========
        box_freeze = layout.box()
        box_freeze.label(text="顶点冻结 (雕刻保护)", icon='FREEZE')

        col_f = box_freeze.column(align=True)
        col_f.label(text="1. Edit模式选中顶点", icon='EDITMODE_HLT')
        col_f.operator("xxmi.freeze_selected_vertices", text="冻结选中")

        col_f.separator()
        col_f.label(text="2. Sculpt后恢复", icon='SCULPTMODE_HLT')
        col_f.operator("xxmi.restore_frozen_vertices", text="还原位置")

        layout.separator()

        # ========== 模块4: 缝合工具 ==========
        box_merge = layout.box()
        box_merge.label(text="缝合工具 (Sora_)", icon='AUTOMERGE_ON')

        col_m = box_merge.column(align=True)
        col_m.operator("xxmi.mark_fixed_elements", text="A. 标记固定目标")
        col_m.operator("xxmi.mark_moving_elements", text="B. 标记移动来源")
        col_m.separator()
        col_m.operator("xxmi.merge_elements", text="执行吸附合并", icon='MOD_SHRINKWRAP')

        col_help = box_merge.column(align=True)
        col_help.enabled = False
        col_help.scale_y = 0.8
        col_help.label(text="步骤: 对齐 -> 标记目标 ->")
        col_help.label(text="标记来源(细分更多点) -> 合并")


# =============================================================================
# 10. 注册 / 注销
# =============================================================================

classes = (
    # 数据结构
    XXMI_VertexItem,
    XXMI_ElementGroup,
    XXMI_TransferPropsSettings,
    # 合并雕刻操作符
    XXMI_OT_CreateMergedObject,
    XXMI_OT_ApplyMergedSculpt,
    XXMI_OT_ApplyMergedSculptShapeKeys,
    XXMI_OT_DeleteMergedObject,
    # 属性传递操作符
    XXMI_OT_TransferProperties,
    # 顶点冻结操作符
    XXMI_OT_FreezeSelectedVertices,
    XXMI_OT_RestoreFrozenVertices,
    # 缝合工具操作符
    XXMI_OT_MarkFixedElements,
    XXMI_OT_MarkMovingElements,
    XXMI_OT_MergeElements,
    # 面板
    XXMI_PT_MeshTools,
)


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            pass

    # 场景属性
    bpy.types.Scene.xxmi_fixed_elements = PointerProperty(type=XXMI_ElementGroup)
    bpy.types.Scene.xxmi_moving_elements = PointerProperty(type=XXMI_ElementGroup)
    bpy.types.Scene.xxmi_transfer_settings = PointerProperty(type=XXMI_TransferPropsSettings)


def unregister():
    if hasattr(bpy.types.Scene, "xxmi_fixed_elements"):
        del bpy.types.Scene.xxmi_fixed_elements
    if hasattr(bpy.types.Scene, "xxmi_moving_elements"):
        del bpy.types.Scene.xxmi_moving_elements
    if hasattr(bpy.types.Scene, "xxmi_transfer_settings"):
        del bpy.types.Scene.xxmi_transfer_settings

    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            pass