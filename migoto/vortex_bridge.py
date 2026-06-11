# type: ignore
"""
XXMI Tools CHN — 3DM 骨骼映射桥接模块
==========================================
将 Vortex Tools 的 3DM↔FBX 顶点组匹配、CSV 映射表管理、空物体清除
整合进 XXMI Tools 的骨骼工具菜单。

auto_load 会自动发现并注册此模块的所有类和属性。
"""

import bpy
import csv
import os
import json
from mathutils import Vector
from bpy.props import (
    PointerProperty, StringProperty, FloatProperty,
    CollectionProperty, IntProperty, BoolProperty,
)
from bpy.types import PropertyGroup, Operator, UIList
from bpy_extras.io_utils import ImportHelper, ExportHelper


# ============================================================
# 工具函数
# ============================================================

def _read_csv_multiencoding(filepath):
    for enc in ['utf-8', 'gbk', 'utf-16', 'latin-1']:
        try:
            with open(filepath, 'r', newline='', encoding=enc) as f:
                return list(csv.reader(f)), enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return None, None


def _get_vertex_group_centers(obj):
    """获取网格每个非空顶点组的加权质心（世界空间）。"""
    centers = {}
    mesh = obj.data
    if not obj.vertex_groups or not mesh.vertices:
        return centers
    matrix = obj.matrix_world
    for vg in obj.vertex_groups:
        ws = Vector((0, 0, 0))
        tw = 0.0
        for v in mesh.vertices:
            for g in v.groups:
                if g.group == vg.index and g.weight > 0.001:
                    ws += matrix @ v.co * g.weight
                    tw += g.weight
                    break
        if tw > 0:
            centers[vg.name] = ws / tw
    return centers


def _match_by_centers(src_centers, tgt_centers, threshold=0.05):
    """贪心匹配：src=FBX(名称源), tgt=3DM(被重命名) → {tgt_name: src_name}"""
    matches, unmatched = {}, []
    remaining = dict(src_centers)
    for tgt_name, tgt_pos in tgt_centers.items():
        best_name, best_dist = None, float('inf')
        for src_name, src_pos in remaining.items():
            d = (tgt_pos - src_pos).length
            if d < best_dist:
                best_dist, best_name = d, src_name
        if best_dist <= threshold:
            matches[tgt_name] = best_name
            del remaining[best_name]
        else:
            unmatched.append((tgt_name, best_dist))
    return matches, unmatched


# ============================================================
# 数据存储
# ============================================================

class XXMI_VORTEX_CsvEntry(PropertyGroup):
    """单条 CSV 映射记录"""
    name: StringProperty(name="名称", default="")
    data_json: StringProperty(name="数据(JSON)", default="[]")


class XXMI_VORTEX_UL_csv_list(UIList):
    bl_idname = "XXMI_VORTEX_UL_csv_list"

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        layout.prop(item, "name", text="", emboss=False)
        rows = json.loads(item.data_json) if item.data_json else []
        layout.label(text=f"  ({len(rows)} 条映射)")


class XXMI_VORTEX_Properties(PropertyGroup):
    mesh_3dm: PointerProperty(
        name="3DM 网格",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
        description="3DMigoto 提取的网格模型",
    )
    mesh_fbx: PointerProperty(
        name="FBX 网格",
        type=bpy.types.Object,
        poll=lambda self, obj: obj.type == 'MESH',
        description="FBX 解包的网格模型（含命名顶点组）",
    )


# ============================================================
# 操作符 1：匹配重命名 + 绑定骨架 + 生成 CSV
# ============================================================

class XXMI_VORTEX_OT_match_rename(Operator):
    bl_idname = "xxmi_vortex.match_rename"
    bl_label = "顶点组匹配重命名"
    bl_description = (
        "基于空间质心匹配，将 3DM 顶点组重命名为 FBX 顶点组名，\n"
        "绑定 FBX 骨架，生成 CSV 映射表"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        p = context.scene.xxmi_vortex
        return (p.mesh_3dm is not None and p.mesh_fbx is not None
                and p.mesh_3dm.type == 'MESH' and p.mesh_fbx.type == 'MESH')

    def execute(self, context):
        p = context.scene.xxmi_vortex
        mesh_3dm = p.mesh_3dm
        mesh_fbx = p.mesh_fbx
        threshold = context.scene.xxmi_vortex_match_threshold
        create_mat = context.scene.xxmi_vortex_create_materials

        if context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        centers_fbx = _get_vertex_group_centers(mesh_fbx)
        centers_3dm = _get_vertex_group_centers(mesh_3dm)

        if not centers_fbx:
            self.report({'ERROR'}, f"FBX 网格 '{mesh_fbx.name}' 没有非空顶点组")
            return {'CANCELLED'}
        if not centers_3dm:
            self.report({'ERROR'}, f"3DM 网格 '{mesh_3dm.name}' 没有非空顶点组")
            return {'CANCELLED'}

        matches, unmatched = _match_by_centers(centers_fbx, centers_3dm, threshold)

        # 重命名
        renamed = 0
        csv_rows = [("3DM_VertexGroup", "FBX_VertexGroup")]
        for old_name, new_name in matches.items():
            vg = mesh_3dm.vertex_groups.get(old_name)
            if vg and vg.name != new_name:
                old = vg.name
                vg.name = new_name
                renamed += 1
                csv_rows.append((old, new_name))
            elif vg:
                csv_rows.append((old_name, new_name))
        for old_name, _ in unmatched:
            csv_rows.append((old_name, "---未匹配---"))

        # 绑定骨架
        arm = None
        for mod in mesh_fbx.modifiers:
            if mod.type == 'ARMATURE' and mod.object:
                arm = mod.object
                break
        if arm:
            for mod in list(mesh_3dm.modifiers):
                if mod.type == 'ARMATURE':
                    mesh_3dm.modifiers.remove(mod)
            mod = mesh_3dm.modifiers.new(name="Vortex_Armature", type='ARMATURE')
            mod.object = arm

        # 材质：无材质才建空白材质
        mat_created = False
        if create_mat and not mesh_3dm.data.materials:
            mat = bpy.data.materials.new(name=mesh_3dm.name)
            mesh_3dm.data.materials.append(mat)
            mat_created = True

        # 存 CSV
        csv_name = mesh_3dm.name
        csv_json = json.dumps(csv_rows, ensure_ascii=False)
        self._store_csv(context, csv_name, csv_json)

        msg = f"匹配 {renamed}/{len(centers_3dm)} 顶点组"
        if arm:
            msg += f"，已绑定骨架 '{arm.name}'"
        if mat_created:
            msg += "，已创建空白材质"
        msg += f"，映射表 '{csv_name}' 已暂存"
        self.report({'INFO'}, msg)

        print(f"\n{'='*60}\n  顶点组匹配: {mesh_3dm.name} ← {mesh_fbx.name}")
        print(f"  匹配 {renamed}/{len(centers_3dm)}, 未匹配 {len(unmatched)}")
        if arm:
            print(f"  骨架: {arm.name}")
        for old, new in csv_rows[1:6]:
            flag = "" if new != "---未匹配---" else " ⚠"
            print(f"    {old} → {new}{flag}")
        if len(csv_rows) > 7:
            print(f"    ... 共 {len(csv_rows)-1} 条")
        print(f"{'='*60}\n")
        return {'FINISHED'}

    def _store_csv(self, context, name, csv_json):
        items = context.scene.xxmi_vortex_csv_list
        for item in items:
            if item.name == name:
                item.data_json = csv_json
                return item
        item = items.add()
        item.name = name
        item.data_json = csv_json
        context.scene.xxmi_vortex_csv_index = len(items) - 1
        return item


# ============================================================
# 操作符 2：导出 CSV
# ============================================================

class XXMI_VORTEX_OT_save_csv(Operator, ExportHelper):
    bl_idname = "xxmi_vortex.save_csv"
    bl_label = "导出 CSV"
    bl_description = "将选中的映射表保存为 CSV 文件"
    bl_options = {'REGISTER'}
    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        idx = context.scene.xxmi_vortex_csv_index
        items = context.scene.xxmi_vortex_csv_list
        return 0 <= idx < len(items)

    def invoke(self, context, event):
        items = context.scene.xxmi_vortex_csv_list
        idx = context.scene.xxmi_vortex_csv_index
        if 0 <= idx < len(items):
            self.filepath = items[idx].name + ".csv"
        return super().invoke(context, event)

    def execute(self, context):
        items = context.scene.xxmi_vortex_csv_list
        idx = context.scene.xxmi_vortex_csv_index
        rows = json.loads(items[idx].data_json)
        try:
            with open(self.filepath, 'w', newline='', encoding='utf-8-sig') as f:
                csv.writer(f).writerows(rows)
            self.report({'INFO'}, f"已导出: {os.path.basename(self.filepath)}")
        except Exception as e:
            self.report({'ERROR'}, f"导出失败: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


# ============================================================
# 操作符 3：导入 CSV
# ============================================================

class XXMI_VORTEX_OT_load_csv(Operator, ImportHelper):
    bl_idname = "xxmi_vortex.load_csv"
    bl_label = "导入 CSV"
    bl_description = "从文件导入 CSV 映射表"
    bl_options = {'REGISTER'}
    filename_ext = ".csv"
    filter_glob: StringProperty(default="*.csv", options={'HIDDEN'})

    def execute(self, context):
        rows, enc = _read_csv_multiencoding(self.filepath)
        if rows is None:
            self.report({'ERROR'}, "无法读取 CSV（编码错误）")
            return {'CANCELLED'}
        name = os.path.splitext(os.path.basename(self.filepath))[0]
        data = json.dumps(rows, ensure_ascii=False)
        items = context.scene.xxmi_vortex_csv_list
        for item in items:
            if item.name == name:
                item.data_json = data
                self.report({'INFO'}, f"已覆盖: '{name}' ({len(rows)-1} 条)")
                return {'FINISHED'}
        item = items.add()
        item.name = name
        item.data_json = data
        context.scene.xxmi_vortex_csv_index = len(items) - 1
        self.report({'INFO'}, f"已导入: '{name}' ({len(rows)-1} 条)")
        return {'FINISHED'}


# ============================================================
# 操作符 4：应用 CSV 映射
# ============================================================

class XXMI_VORTEX_OT_apply_csv(Operator):
    bl_idname = "xxmi_vortex.apply_csv"
    bl_label = "应用映射表"
    bl_description = "基于 CSV 重命名 3DM 顶点组并绑定骨架"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        p = context.scene.xxmi_vortex
        idx = context.scene.xxmi_vortex_csv_index
        items = context.scene.xxmi_vortex_csv_list
        return (p.mesh_3dm is not None and p.mesh_3dm.type == 'MESH'
                and 0 <= idx < len(items))

    def execute(self, context):
        mesh_3dm = context.scene.xxmi_vortex.mesh_3dm
        items = context.scene.xxmi_vortex_csv_list
        idx = context.scene.xxmi_vortex_csv_index
        rows = json.loads(items[idx].data_json)
        if len(rows) < 2:
            self.report({'ERROR'}, "CSV 为空")
            return {'CANCELLED'}

        renamed = 0
        for row in rows[1:]:
            if len(row) < 2:
                continue
            old, new = row[0].strip(), row[1].strip()
            if not old or not new or new == "---未匹配---":
                continue
            vg = mesh_3dm.vertex_groups.get(old)
            if vg and vg.name != new:
                vg.name = new
                renamed += 1

        # 绑定任意场景中的骨架
        arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
        if arm:
            for mod in list(mesh_3dm.modifiers):
                if mod.type == 'ARMATURE':
                    mesh_3dm.modifiers.remove(mod)
            mod = mesh_3dm.modifiers.new(name="Vortex_Armature", type='ARMATURE')
            mod.object = arm

        msg = f"应用 '{items[idx].name}'：重命名 {renamed} 顶点组"
        if arm:
            msg += f"，已绑定 '{arm.name}'"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ============================================================
# 操作符 5：移除暂存 CSV
# ============================================================

class XXMI_VORTEX_OT_remove_csv(Operator):
    bl_idname = "xxmi_vortex.remove_csv"
    bl_label = "移除"
    bl_description = "从暂存列表删除选中映射表"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        idx = context.scene.xxmi_vortex_csv_index
        return 0 <= idx < len(context.scene.xxmi_vortex_csv_list)

    def execute(self, context):
        items = context.scene.xxmi_vortex_csv_list
        idx = context.scene.xxmi_vortex_csv_index
        name = items[idx].name
        items.remove(idx)
        n = len(items)
        context.scene.xxmi_vortex_csv_index = min(idx, n - 1) if n > 0 else -1
        self.report({'INFO'}, f"已移除: '{name}'")
        return {'FINISHED'}


# ============================================================
# 操作符 6：清除空物体
# ============================================================

class XXMI_VORTEX_OT_clear_empty(Operator):
    bl_idname = "xxmi_vortex.clear_empty"
    bl_label = "清除选中空物体"
    bl_description = "删除选中物体的空物体(EMPTY)，自动解除父子关系并保持子物体变换"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 0

    def execute(self, context):
        empties = [o for o in context.selected_objects if o.type == 'EMPTY']
        if not empties:
            self.report({'INFO'}, "选中物体中没有空物体")
            return {'FINISHED'}

        bpy.ops.object.select_all(action='DESELECT')
        cleared = 0
        for e in empties:
            try:
                e.name
            except ReferenceError:
                continue
            children = list(e.children)
            if children:
                bpy.ops.object.select_all(action='DESELECT')
                for c in children:
                    c.select_set(True)
                bpy.context.view_layer.objects.active = children[0]
                bpy.ops.object.parent_clear(type='CLEAR_KEEP_TRANSFORM')
            bpy.ops.object.select_all(action='DESELECT')
            e.select_set(True)
            bpy.context.view_layer.objects.active = e
            bpy.ops.object.delete()
            cleared += 1

        self.report({'INFO'}, f"已清除 {cleared} 个空物体")
        return {'FINISHED'}


# ============================================================
# 面板：挂载在 XXMI Tools 主侧栏下，骨骼工具区域
# ============================================================

class XXMI_PT_vortex_bridge(bpy.types.Panel):
    bl_label = "3DM 骨骼映射"
    bl_idname = "XXMI_PT_vortex_bridge"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "XXMI_PT_Sidebar"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 12

    def draw(self, context):
        layout = self.layout
        p = context.scene.xxmi_vortex

        # -- 模型选择 --
        box = layout.box()
        box.label(text="模型选择", icon='MESH_DATA')
        box.prop(p, "mesh_3dm")
        box.prop(p, "mesh_fbx")

        # -- 匹配 --
        box = layout.box()
        box.label(text="匹配操作", icon='BONE_DATA')
        col = box.column(align=True)
        row = col.row(align=True)
        row.scale_y = 1.3
        row.operator("xxmi_vortex.match_rename", icon='ARROW_LEFTRIGHT')
        col.prop(context.scene, "xxmi_vortex_match_threshold", text="匹配阈值", slider=True)
        col.prop(context.scene, "xxmi_vortex_create_materials", text="无材质则创建空白材质")

        # -- CSV 映射表 --
        box = layout.box()
        box.label(text="CSV 映射表", icon='FILE_TEXT')
        col = box.column(align=True)
        items = context.scene.xxmi_vortex_csv_list
        col.label(text=f"暂存: {len(items)} 份")

        if len(items) > 0:
            row = col.row()
            row.template_list(
                "XXMI_VORTEX_UL_csv_list", "",
                context.scene, "xxmi_vortex_csv_list",
                context.scene, "xxmi_vortex_csv_index",
                rows=3,
            )
            row = col.row(align=True)
            row.operator("xxmi_vortex.apply_csv", icon='CHECKMARK', text="应用")
            row.operator("xxmi_vortex.save_csv", icon='EXPORT', text="导出")
            row.operator("xxmi_vortex.remove_csv", icon='X', text="移除")
        col.operator("xxmi_vortex.load_csv", icon='IMPORT', text="导入")

        # -- 空物体清除 --
        box = layout.box()
        box.label(text="清理空物体", icon='TRASH')
        col = box.column(align=True)
        n = sum(1 for o in context.selected_objects if o.type == 'EMPTY')
        col.label(text=f"选中包含 {n} 个空物体")
        row = col.row(align=True)
        row.scale_y = 1.2
        row.operator("xxmi_vortex.clear_empty", icon='OUTLINER_OB_EMPTY')


# ============================================================
# 注册 / 注销 (由 auto_load 自动调用)
# ============================================================

def register():
    bpy.types.Scene.xxmi_vortex = PointerProperty(type=XXMI_VORTEX_Properties)
    bpy.types.Scene.xxmi_vortex_csv_list = CollectionProperty(type=XXMI_VORTEX_CsvEntry)
    bpy.types.Scene.xxmi_vortex_csv_index = IntProperty(default=-1)
    bpy.types.Scene.xxmi_vortex_match_threshold = FloatProperty(
        name="匹配阈值",
        description="质心距离超过此值的顶点组视为不匹配 (米)",
        default=0.05, min=0.001, max=1.0, precision=4,
    )
    bpy.types.Scene.xxmi_vortex_create_materials = BoolProperty(
        name="无材质则创建空白材质",
        description="如果 3DM 网格没有材质，则根据网格名创建空白材质",
        default=True,
    )


def unregister():
    del bpy.types.Scene.xxmi_vortex
    del bpy.types.Scene.xxmi_vortex_csv_list
    del bpy.types.Scene.xxmi_vortex_csv_index
    del bpy.types.Scene.xxmi_vortex_match_threshold
    del bpy.types.Scene.xxmi_vortex_create_materials
