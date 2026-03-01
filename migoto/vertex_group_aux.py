import bpy
from bpy.types import Operator, Panel

# -------------------------------------------------------------------
# 核心逻辑类
# -------------------------------------------------------------------

class XXMI_VG_Utils:
    @staticmethod
    def remove_unused_vertex_groups(obj):
        """移除权重全为0的顶点组"""
        if obj.type != "MESH":
            return
        
        obj.update_from_editmode()
        vgroup_used = {i: False for i, k in enumerate(obj.vertex_groups)}

        for v in obj.data.vertices:
            for g in v.groups:
                if g.weight > 0.0:
                    vgroup_used[g.group] = True

        unused_indices = [i for i, used in vgroup_used.items() if not used]
        
        for i in sorted(unused_indices, reverse=True):
            obj.vertex_groups.remove(obj.vertex_groups[i])

    @staticmethod
    def merge_vertex_groups_by_prefix(obj):
        """合并同前缀的顶点组"""
        if obj.type != "MESH":
            return

        base_names = set([vg.name.split(".")[0] for vg in obj.vertex_groups])

        for base_name in base_names:
            relevant_vgs = [vg for vg in obj.vertex_groups if vg.name.split(".")[0] == base_name]

            if len(relevant_vgs) <= 1 and relevant_vgs[0].name == base_name:
                continue
            
            if not relevant_vgs:
                continue

            new_vg_name = f"x{base_name}"
            target_vg = obj.vertex_groups.new(name=new_vg_name)
            target_vg_index = target_vg.index

            relevant_indices = [vg.index for vg in relevant_vgs]
            
            for v in obj.data.vertices:
                total_weight = 0.0
                for g in v.groups:
                    if g.group in relevant_indices:
                        total_weight += g.weight
                
                if total_weight > 0:
                    target_vg.add([v.index], total_weight, 'REPLACE')

            for vg in relevant_vgs:
                obj.vertex_groups.remove(vg)

            final_vg = obj.vertex_groups.get(new_vg_name)
            if final_vg:
                final_vg.name = base_name

        bpy.ops.object.vertex_group_sort()

# -------------------------------------------------------------------
# Operators (操作符)
# -------------------------------------------------------------------

class XXMI_OT_RemoveUnusedVG(Operator):
    bl_idname = "xxmi.remove_unused_vg"
    bl_label = "移除未使用顶点组"
    bl_description = "移除所有权重为0或未分配顶点的顶点组"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object
        XXMI_VG_Utils.remove_unused_vertex_groups(obj)
        self.report({'INFO'}, "已移除未使用的顶点组")
        return {'FINISHED'}


class XXMI_OT_MergePrefixVG(Operator):
    bl_idname = "xxmi.merge_prefix_vg"
    bl_label = "合并同前缀顶点组"
    bl_description = "合并名称前缀相同的顶点组"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        selected_objects = context.selected_objects
        if not selected_objects:
            selected_objects = [context.object]

        count = 0
        for obj in selected_objects:
            if obj.type == 'MESH':
                XXMI_VG_Utils.merge_vertex_groups_by_prefix(obj)
                count += 1
        
        self.report({'INFO'}, f"已处理 {count} 个对象的顶点组合并")
        return {'FINISHED'}

# -------------------------------------------------------------------
# UI Panel
# -------------------------------------------------------------------

class XXMI_PT_VertexGroupAux(Panel):
    bl_label = "顶点组辅助"
    bl_idname = "XXMI_PT_VertexGroupAux"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "XXMI_PT_Sidebar" 
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 6

    def draw(self, context):
        layout = self.layout
        
        layout.label(text="清理工具", icon='BRUSH_DATA')
        layout.operator(XXMI_OT_RemoveUnusedVG.bl_idname, icon='X')
        
        layout.separator()
        
        layout.label(text="合并工具", icon='GROUP_VERTEX')
        layout.operator(XXMI_OT_MergePrefixVG.bl_idname, icon='AUTOMERGE_ON')
