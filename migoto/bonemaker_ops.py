import bpy
import numpy as np
from mathutils import Vector, Matrix
from bpy.types import Operator, PropertyGroup, Panel
from bpy.props import EnumProperty, FloatProperty, BoolProperty, PointerProperty

# =============================================================================
# 1. 核心算法 (保持原版逻辑不变)
# =============================================================================

def _vgroup_items(self, context):
    """获取当前选中物体的所有顶点组列表"""
    obj = context.object
    if obj and obj.type == 'MESH' and obj.vertex_groups:
        return [(vg.name, vg.name, "") for vg in obj.vertex_groups]
    return [("","(无顶点组)","")]

def get_coords_and_weights_in_arm_space(mesh_obj, arm_obj, vg, w_thresh=0.001):
    arm_inv = arm_obj.matrix_world.inverted()
    coords = []
    weights = []
    gi = vg.index

    for v in mesh_obj.data.vertices:
        w = 0.0
        for g in v.groups:
            if g.group == gi:
                w = g.weight
                break
        if w > w_thresh:
            world_co = mesh_obj.matrix_world @ v.co
            local_co = arm_inv @ world_co
            coords.append((local_co.x, local_co.y, local_co.z))
            weights.append(w)

    if not coords:
        return None, None
    return np.asarray(coords, dtype=np.float64), np.asarray(weights, dtype=np.float64)

def weighted_mean(coords, weights):
    W = weights.sum()
    if W == 0:
        return coords.mean(axis=0)
    return (coords * (weights[:, None]/W)).sum(axis=0)

def weighted_cov(coords, weights, mean):
    X = coords - mean
    W = weights.sum()
    if W == 0:
        return np.cov(X.T)
    
    S = np.zeros((3,3), dtype=np.float64)
    for i in range(X.shape[0]):
        xi = X[i:i+1].T
        S += weights[i] * (xi @ xi.T)
    S /= W
    return S

def weighted_quantile(values, weights, q):
    if len(values) == 0:
        return 0.0
    order = np.argsort(values)
    v = values[order]
    w = weights[order]
    cw = np.cumsum(w)
    if cw[-1] == 0:
        return v[int(q*len(v))]
    t = q * cw[-1]
    idx = np.searchsorted(cw, t, side='left')
    idx = np.clip(idx, 0, len(v)-1)
    return v[idx]

def detect_ring_like(eigvals, tol=1.2):
    ratios = [
        eigvals[0] / max(eigvals[1], 1e-12),
        eigvals[1] / max(eigvals[2], 1e-12)
    ]
    return all(r < tol for r in ratios)

def pca_fit(coords, weights, low_q=0.05, high_q=0.95):
    center_w = weighted_mean(coords, weights)
    S = weighted_cov(coords, weights, center_w)
    eigvals, eigvecs = np.linalg.eigh(S)
    order = np.argsort(eigvals)[::-1]
    eigvals = eigvals[order]
    eigvecs = eigvecs[:, order]
    v1, v2, v3 = eigvecs[:, 0], eigvecs[:, 1], eigvecs[:, 2]
    l1, l2, l3 = float(eigvals[0]), float(eigvals[1]), float(eigvals[2])

    if detect_ring_like(eigvals):
        center = coords.mean(axis=0)
        case = "ring"
    else:
        center = center_w
        case = "weighted"

    eps = 1e-12
    cigar_ratio = (l1 / (l2 + eps)) if l2 > eps else 1.0
    sheet_ratio = (l2 / (l3 + eps)) if l3 > eps else 1.0

    K = 3.0
    if cigar_ratio >= K and l1 > eps:
        axis = v1
    elif sheet_ratio >= K and l2 > eps:
        axis = v3
    else:
        zhat = np.array([0.0, 0.0, 1.0])
        cands = np.stack([v1, v2, v3], axis=0)
        axis = cands[np.argmax(np.abs(cands @ zhat))]

    axis /= np.linalg.norm(axis) + 1e-12

    proj = (coords - center) @ axis
    pmin = weighted_quantile(proj, weights, low_q)
    pmax = weighted_quantile(proj, weights, high_q)
    if pmax - pmin < 1e-6:
        pmin, pmax = -0.01, 0.01

    head = center + pmax * axis
    tail = center + pmin * axis
    length = float(pmax - pmin)

    if head[2] < tail[2]:
        head, tail = tail, head

    return head, tail, length, axis, case

def ensure_edit_mode(obj):
    bpy.context.view_layer.objects.active = obj
    if bpy.context.object.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')

def leave_object_mode():
    if bpy.context.object and bpy.context.object.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')

# =============================================================================
# 2. 骨骼构建逻辑
# =============================================================================

def build_single_bone_from_vgroup(mesh_obj, arm_obj, vgroup_name, w_thresh=0.001,
                                  low_q=0.05, high_q=0.95):
    vg = mesh_obj.vertex_groups.get(vgroup_name)
    if not vg:
        return None

    coords, weights = get_coords_and_weights_in_arm_space(mesh_obj, arm_obj, vg, w_thresh)
    if coords is None:
        return None

    head, tail, length, axis, case = pca_fit(coords, weights, low_q, high_q)

    ensure_edit_mode(arm_obj)
    eb = arm_obj.data.edit_bones.new(vgroup_name)
    eb.head = Vector(head)
    eb.tail = Vector(tail)
    leave_object_mode()
    return eb

def build_armature_from_all_vgroups(mesh_obj, connect_factor=0.0, w_thresh=0.001,
                                    low_q=0.05, high_q=0.95):
    arm_data = bpy.data.armatures.new(mesh_obj.name + "_AutoRig")
    arm_obj = bpy.data.objects.new(arm_data.name, arm_data)
    mesh_obj.users_collection[0].objects.link(arm_obj)
    
    arm_obj.show_in_front = True 

    bones_info = []

    ensure_edit_mode(arm_obj)

    # 1. 批量生成
    for vg in mesh_obj.vertex_groups:
        coords, weights = get_coords_and_weights_in_arm_space(mesh_obj, arm_obj, vg, w_thresh)
        if coords is None:
            continue
        head, tail, length, axis, case = pca_fit(coords, weights, low_q, high_q)
        eb = arm_obj.data.edit_bones.new(vg.name)
        eb.head = Vector(head)
        eb.tail = Vector(tail)
        bones_info.append({
            "name": vg.name,
            "head": Vector(head),
            "tail": Vector(tail),
            "length": float(length)
        })

    # 2. 自动连接
    if connect_factor > 0 and bones_info:
        avg_len = np.mean([b["length"] for b in bones_info])
        thresh = float(avg_len * connect_factor)

        name_to_bone = {b.name: b for b in arm_obj.data.edit_bones}
        
        for b in bones_info:
            child = name_to_bone[b["name"]]
            if child.parent:
                continue
            best_parent = None
            best_dist = 1e18
            for o in bones_info:
                if o["name"] == b["name"]:
                    continue
                parent = name_to_bone[o["name"]]
                d = (b["head"] - o["tail"]).length
                if d < thresh and d < best_dist:
                    best_parent, best_dist = parent, d
            if best_parent:
                child.parent = best_parent
                child.use_connect = True
                child.head = best_parent.tail.copy()

    leave_object_mode()
    return arm_obj, bones_info

# =============================================================================
# 3. 属性定义
# =============================================================================

class XXMI_BoneMakerProperties(PropertyGroup):
    vertex_group: EnumProperty(
        name="选择顶点组",
        description="从当前活动网格中选择一个顶点组",
        items=_vgroup_items
    )
    connect_threshold: FloatProperty(
        name="自动连接阈值",
        description="自动连接父子骨骼的距离容差 (倍率基于平均骨骼长度，0 = 关闭)",
        default=0.0, min=0.0, max=2.0
    )
    weight_threshold: FloatProperty(
        name="权重忽略阈值",
        description="忽略权重小于等于此值的顶点 (降噪)",
        default=0.001, min=0.0, max=1.0
    )
    use_active_armature: BoolProperty(
        name="添加到活动骨架",
        description="如果勾选且当前选中了骨架，新骨骼会添加进去；否则新建一个骨架。",
        default=True
    )
    keep_transform_on_parent: BoolProperty(
        name="保持变换 (Keep Transform)",
        description="设置父级时保持网格物体的世界变换不变",
        default=True
    )
    low_quantile: FloatProperty(
        name="范围起点 (Low %)",
        description="骨骼起点位置的权重分位数 (用于去除末端极值)",
        default=0.05, min=0.0, max=0.49
    )
    high_quantile: FloatProperty(
        name="范围终点 (High %)",
        description="骨骼终点位置的权重分位数",
        default=0.95, min=0.51, max=1.0
    )
    last_armature: PointerProperty(
        name="Last Armature",
        type=bpy.types.Object
    )

# =============================================================================
# 4. 操作符
# =============================================================================

class XXMI_OT_OneBoneFromVGroup(Operator):
    bl_idname = "xxmi.one_bone_from_vgroup"
    bl_label = "生成单根骨骼"
    bl_description = "仅为当前选中的这一个顶点组生成骨骼"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.xxmi_bonemaker_props
        mesh = context.object
        if not mesh or mesh.type != 'MESH':
            self.report({'ERROR'}, "请先选择一个网格物体")
            return {'CANCELLED'}
        vg_name = props.vertex_group or (mesh.vertex_groups.active and mesh.vertex_groups.active.name)
        if not vg_name:
            self.report({'ERROR'}, "未选择顶点组")
            return {'CANCELLED'}

        arm = None
        if props.use_active_armature and context.active_object and context.active_object.type == 'ARMATURE':
            arm = context.active_object
        else:
            arm_data = bpy.data.armatures.new(mesh.name + "_Arm")
            arm = bpy.data.objects.new(mesh.name + "_Arm", arm_data)
            mesh.users_collection[0].objects.link(arm)
            
        if arm:
            arm.show_in_front = True

        bone = build_single_bone_from_vgroup(
            mesh, arm, vg_name,
            w_thresh=props.weight_threshold,
            low_q=props.low_quantile,
            high_q=props.high_quantile
        )
        if not bone:
            self.report({'WARNING'}, f"顶点组 '{vg_name}' 是空的或太小，无法生成")
            return {'CANCELLED'}

        props.last_armature = arm
        self.report({'INFO'}, f"已在骨架 '{arm.name}' 中创建骨骼")
        return {'FINISHED'}


class XXMI_OT_AllBonesFromVGroups(Operator):
    bl_idname = "xxmi.all_bones_from_vgroups"
    bl_label = "生成完整骨架 (所有组)"
    bl_description = "遍历所有顶点组并生成对应的骨骼结构"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.xxmi_bonemaker_props
        mesh = context.object
        if not mesh or mesh.type != 'MESH':
            self.report({'ERROR'}, "请先选择一个网格物体")
            return {'CANCELLED'}

        arm, info = build_armature_from_all_vgroups(
            mesh,
            connect_factor=props.connect_threshold,
            w_thresh=props.weight_threshold,
            low_q=props.low_quantile,
            high_q=props.high_quantile
        )
        if not info:
            self.report({'WARNING'}, "未找到有效的顶点组")
            return {'CANCELLED'}

        props.last_armature = arm
        self.report({'INFO'}, f"骨架生成完毕: 共 {len(info)} 根骨骼")
        return {'FINISHED'}


class XXMI_OT_AutoParentMesh(Operator):
    bl_idname = "xxmi.auto_parent_mesh"
    bl_label = "绑定网格 (添加修改器)"
    bl_description = "将网格父级设为骨架，并添加 Armature 修改器"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = context.scene.xxmi_bonemaker_props

        mesh = None
        arm = None
        for obj in context.selected_objects:
            if obj.type == 'MESH':
                mesh = obj
            elif obj.type == 'ARMATURE':
                arm = obj
        if mesh is None:
            if context.object and context.object.type == 'MESH':
                mesh = context.object
        if arm is None:
            arm = props.last_armature

        if not mesh or not arm:
            self.report({'ERROR'}, "请同时选择网格和骨架 (或先生成一个骨架)")
            return {'CANCELLED'}

        mods = [m for m in mesh.modifiers if m.type == 'ARMATURE' and m.object == arm]
        if not mods:
            mod = mesh.modifiers.new(name="Armature", type='ARMATURE')
            mod.object = arm

        bpy.context.view_layer.objects.active = arm
        mesh.select_set(True)
        arm.select_set(True)
        bpy.ops.object.parent_set(
            type='ARMATURE_NAME',
            keep_transform=props.keep_transform_on_parent
        )

        self.report({'INFO'}, "网格已绑定，Armature 修改器已添加")
        return {'FINISHED'}


class XXMI_OT_OneClickRig(Operator):
    bl_idname = "xxmi.one_click_rig"
    bl_label = "一键生成并绑定"
    bl_description = "自动执行：生成所有骨骼 -> 绑定网格"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        r1 = bpy.ops.xxmi.all_bones_from_vgroups()
        if r1 != {'FINISHED'}:
            return r1
        r2 = bpy.ops.xxmi.auto_parent_mesh()
        return r2

class XXMI_OT_RemoveEmptyVGroups(Operator):
    bl_idname = "xxmi.remove_empty_vgroups"
    bl_label = "清理空顶点组"
    bl_description = "删除所有不包含任何权重信息的顶点组"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "请选择一个网格")
            return {'CANCELLED'}

        removed = 0
        for vg in list(obj.vertex_groups):
            # 检查是否所有顶点在这个组的权重都是 0
            # 优化逻辑：只要有一个顶点权重 > 0 就保留
            is_empty = True
            for v in obj.data.vertices:
                for g in v.groups:
                    if g.group == vg.index and g.weight > 0.0:
                        is_empty = False
                        break
                if not is_empty:
                    break
            
            if is_empty:
                obj.vertex_groups.remove(vg)
                removed += 1

        self.report({'INFO'}, f"已移除 {removed} 个空顶点组")
        return {'FINISHED'}

# =============================================================================
# 5. UI 面板
# =============================================================================

class XXMI_PT_BoneMakerPanel(Panel):
    bl_label = "骨骼生成器 (BoneMaker)"
    bl_idname = "XXMI_PT_BoneMakerPanel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "XXMI_PT_Sidebar" 
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 4 # 排在 AI 工具之后

    def draw(self, context):
        # 安全检查
        if not hasattr(context.scene, "xxmi_bonemaker_props"):
            self.layout.label(text="需重启插件", icon="ERROR")
            return

        props = context.scene.xxmi_bonemaker_props
        layout = self.layout

        # 1. 单骨骼生成
        box = layout.box()
        box.label(text="单根骨骼操作:", icon="BONE_DATA")
        col = box.column(align=True)
        col.prop(props, "vertex_group")
        col.prop(props, "use_active_armature")
        col.operator("xxmi.one_bone_from_vgroup")

        # 2. 全骨架生成
        layout.separator()
        box = layout.box()
        box.label(text="全骨架生成设置:", icon="OUTLINER_OB_ARMATURE")
        col = box.column(align=True)
        col.prop(props, "connect_threshold", slider=True)
        
        # 折叠高级设置
        col.separator()
        col.label(text="高级参数 (PCA):", icon="PREFERENCES")
        col.prop(props, "weight_threshold")
        row = col.row(align=True)
        row.prop(props, "low_quantile", text="Low%")
        row.prop(props, "high_quantile", text="High%")
        
        col.separator()
        col.operator("xxmi.all_bones_from_vgroups", text="生成完整骨架")

        # 3. 绑定工具
        layout.separator()
        box = layout.box()
        box.label(text="绑定操作:", icon="CONSTRAINT_BONE")
        col = box.column(align=True)
        col.prop(props, "keep_transform_on_parent")
        col.operator("xxmi.auto_parent_mesh", text="绑定网格 (自动父级)")

        # 4. 快捷操作
        layout.separator()
        row = layout.row(align=True)
        row.operator("xxmi.one_click_rig", text="一键生成并绑定", icon="MOD_ARMATURE")
        row.operator("xxmi.remove_empty_vgroups", text="清理空组", icon="TRASH")

# =============================================================================
# 6. 注册
# =============================================================================

classes = (
    XXMI_BoneMakerProperties,
    XXMI_OT_OneBoneFromVGroup,
    XXMI_OT_AllBonesFromVGroups,
    XXMI_OT_AutoParentMesh,
    XXMI_OT_OneClickRig,
    XXMI_OT_RemoveEmptyVGroups, 
    XXMI_PT_BoneMakerPanel,
)

def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError: pass
        
    if not hasattr(bpy.types.Scene, "xxmi_bonemaker_props"):
        bpy.types.Scene.xxmi_bonemaker_props = PointerProperty(type=XXMI_BoneMakerProperties)

def unregister():
    if hasattr(bpy.types.Scene, "xxmi_bonemaker_props"):
        del bpy.types.Scene.xxmi_bonemaker_props
        
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError: pass