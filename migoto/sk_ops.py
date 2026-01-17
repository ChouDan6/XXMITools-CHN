import bpy
import time
from bpy.types import Operator, PropertyGroup, Panel
from bpy.props import BoolProperty, CollectionProperty

# =============================================================================
# 1. 核心算法 (SKKeeper 逻辑移植)
# =============================================================================

def log(msg):
    t = time.localtime()
    current_time = time.strftime("%H:%M", t)
    print(f"<XXMI SK> {current_time} {msg}")

def copy_object(obj, times=1, offset=0):
    objects = []
    for i in range(0, times):
        copy_obj = obj.copy()
        copy_obj.data = obj.data.copy()
        copy_obj.name = obj.name + "_shapekey_" + str(i+1)
        copy_obj.location.x += offset*(i+1)
        bpy.context.collection.objects.link(copy_obj)
        objects.append(copy_obj)
    return objects

def apply_shapekey(obj, sk_keep):
    shapekeys = obj.data.shape_keys.key_blocks
    if sk_keep < 0 or sk_keep > len(shapekeys):
        return
    for i in reversed(range(0, len(shapekeys))):
        if i != sk_keep:
            obj.shape_key_remove(shapekeys[i])
    obj.shape_key_remove(shapekeys[0])

def apply_modifiers(obj):
    modifiers = obj.modifiers
    for modifier in modifiers:
        if modifier.type == 'SUBSURF':
            modifier.show_only_control_edges = False
    for o in bpy.context.scene.objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.convert(target='MESH')

def remove_modifiers(obj):
    for i in reversed(range(0, len(obj.modifiers))):
        obj.modifiers.remove(obj.modifiers[i])

def apply_subdmod(obj):
    modifiers = [mod for mod in obj.modifiers if mod.type == 'SUBSURF']
    if not modifiers: return
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = obj
    modifiers[0].show_only_control_edges = False
    bpy.ops.object.modifier_apply(modifier=modifiers[0].name)

def apply_modifier(obj, modifier_name):
    modifier = [mod for mod in obj.modifiers if mod.name == modifier_name]
    if not modifier: return
    modifier = modifier[0]
    for o in bpy.context.scene.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=modifier.name)

def add_objs_shapekeys(destination, sources):
    for o in bpy.context.scene.objects:
        o.select_set(False)
    for src in sources:
        src.select_set(True)
    bpy.context.view_layer.objects.active = destination
    bpy.ops.object.join_shapes()

# =============================================================================
# 2. 数据属性与操作符
# =============================================================================

class XXMI_SK_Resource(PropertyGroup):
    selected: BoolProperty(name="Selected", default=False)

class XXMI_OT_ApplyModsKeepSK(Operator):
    bl_idname = "xxmi.apply_mods_sk"
    bl_label = "应用全部修改器 (保留形态键)"
    bl_description = "应用所有修改器，并自动重建形态键"
    bl_options = {'REGISTER', 'UNDO'}

    def validate_input(self, obj):
        if not obj:
            self.report({'ERROR'}, "请先选择一个物体")
            return {'CANCELLED'}
        if obj.type != 'MESH':
            self.report({'ERROR'}, "对象必须是网格(Mesh)")
            return {'CANCELLED'}
        if not obj.data.shape_keys:
            self.report({'ERROR'}, "对象没有形态键")
            return {'CANCELLED'}
        if len(obj.data.shape_keys.key_blocks) == 1:
            self.report({'ERROR'}, "对象只有基态(Basis)形态键")
            return {'CANCELLED'}
        if len(obj.modifiers) == 0:
            self.report({'ERROR'}, "对象没有修改器")
            return {'CANCELLED'}
        return {'FINISHED'}

    def execute(self, context):
        self.obj = context.active_object
        if self.validate_input(self.obj) == {'CANCELLED'}:
            return {'CANCELLED'}
        
        sk_names = [block.name for block in self.obj.data.shape_keys.key_blocks]
        sk_values = [block.value for block in self.obj.data.shape_keys.key_blocks]

        receiver = copy_object(self.obj, times=1, offset=0)[0]
        receiver.name = "sk_receiver"
        apply_shapekey(receiver, 0)
        apply_modifiers(receiver)

        num_shapes = len(self.obj.data.shape_keys.key_blocks)
        for i in range(1, num_shapes):
            blendshape = copy_object(self.obj, times=1, offset=0)[0]
            apply_shapekey(blendshape, i)
            apply_modifiers(blendshape)
            add_objs_shapekeys(receiver, [blendshape])
            if i < len(receiver.data.shape_keys.key_blocks):
                receiver.data.shape_keys.key_blocks[i].name = sk_names[i]
            
            mesh_data = blendshape.data
            bpy.data.objects.remove(blendshape)
            bpy.data.meshes.remove(mesh_data)
        
        for i, val in enumerate(sk_values):
            if i < len(receiver.data.shape_keys.key_blocks):
                receiver.data.shape_keys.key_blocks[i].value = val

        orig_name = self.obj.name
        orig_data = self.obj.data
        bpy.data.objects.remove(self.obj)
        bpy.data.meshes.remove(orig_data)
        receiver.name = orig_name
        
        context.view_layer.objects.active = receiver
        receiver.select_set(True)

        return {'FINISHED'}

class XXMI_OT_ApplySubdKeepSK(Operator):
    bl_idname = "xxmi.apply_subd_sk"
    bl_label = "仅应用细分 (保留形态键)"
    bl_description = "只应用表面细分修改器，保留其他修改器和形态键"
    bl_options = {'REGISTER', 'UNDO'}

    def validate_input(self, obj):
        if not obj or obj.type != 'MESH': return {'CANCELLED'}
        if not obj.data.shape_keys: return {'CANCELLED'}
        if not [mod for mod in obj.modifiers if mod.type == 'SUBSURF']:
            self.report({'ERROR'}, "未找到表面细分修改器")
            return {'CANCELLED'}
        return {'FINISHED'}

    def execute(self, context):
        self.obj = context.active_object
        if self.validate_input(self.obj) == {'CANCELLED'}:
            return {'CANCELLED'}
        
        sk_names = [block.name for block in self.obj.data.shape_keys.key_blocks]
        sk_values = [block.value for block in self.obj.data.shape_keys.key_blocks]

        receiver = copy_object(self.obj, times=1, offset=0)[0]
        receiver.name = "sk_receiver"
        apply_shapekey(receiver, 0)
        apply_subdmod(receiver)

        num_shapes = len(self.obj.data.shape_keys.key_blocks)
        for i in range(1, num_shapes):
            blendshape = copy_object(self.obj, times=1, offset=0)[0]
            apply_shapekey(blendshape, i)
            apply_subdmod(blendshape)
            add_objs_shapekeys(receiver, [blendshape])
            if i < len(receiver.data.shape_keys.key_blocks):
                receiver.data.shape_keys.key_blocks[i].name = sk_names[i]
            
            mesh_data = blendshape.data
            bpy.data.objects.remove(blendshape)
            bpy.data.meshes.remove(mesh_data)
        
        for i, val in enumerate(sk_values):
            if i < len(receiver.data.shape_keys.key_blocks):
                receiver.data.shape_keys.key_blocks[i].value = val

        orig_name = self.obj.name
        orig_data = self.obj.data
        bpy.data.objects.remove(self.obj)
        bpy.data.meshes.remove(orig_data)
        receiver.name = orig_name
        
        context.view_layer.objects.active = receiver
        receiver.select_set(True)
        return {'FINISHED'}

class XXMI_OT_ApplyModsChoiceKeepSK(Operator):
    bl_idname = "xxmi.apply_mods_choice_sk"
    bl_label = "应用选定修改器 (保留形态键)"
    bl_options = {'REGISTER', 'UNDO'}
    
    resource_list: CollectionProperty(name="Modifier List", type=XXMI_SK_Resource)

    def invoke(self, context, event):
        self.obj = context.active_object
        if not self.obj or self.obj.type != 'MESH' or not self.obj.data.shape_keys:
            self.report({'ERROR'}, "对象无效或无形态键")
            return {'CANCELLED'}
        
        self.resource_list.clear()
        for mod in self.obj.modifiers:
            entry = self.resource_list.add()
            entry.name = mod.name
            entry.selected = (mod.type != 'ARMATURE')
            
        return context.window_manager.invoke_props_dialog(self, width=300)

    def execute(self, context):
        sk_names = [block.name for block in self.obj.data.shape_keys.key_blocks]
        sk_values = [block.value for block in self.obj.data.shape_keys.key_blocks]

        selected_mods = [entry.name for entry in self.resource_list if entry.selected]
        if not selected_mods:
            return {'CANCELLED'}

        receiver = copy_object(self.obj, times=1, offset=0)[0]
        receiver.name = "sk_receiver"
        apply_shapekey(receiver, 0)
        for mod_name in selected_mods:
            apply_modifier(receiver, mod_name)
        
        num_shapes = len(self.obj.data.shape_keys.key_blocks)
        for i in range(1, num_shapes):
            blendshape = copy_object(self.obj, times=1, offset=0)[0]
            apply_shapekey(blendshape, i)
            for mod_name in selected_mods:
                apply_modifier(blendshape, mod_name)
            
            remove_modifiers(blendshape)
            
            add_objs_shapekeys(receiver, [blendshape])
            if i < len(receiver.data.shape_keys.key_blocks):
                receiver.data.shape_keys.key_blocks[i].name = sk_names[i]
            
            mesh_data = blendshape.data
            bpy.data.objects.remove(blendshape)
            bpy.data.meshes.remove(mesh_data)
        
        for i, val in enumerate(sk_values):
            if i < len(receiver.data.shape_keys.key_blocks):
                receiver.data.shape_keys.key_blocks[i].value = val

        orig_name = self.obj.name
        orig_data = self.obj.data
        bpy.data.objects.remove(self.obj)
        bpy.data.meshes.remove(orig_data)
        receiver.name = orig_name
        
        context.view_layer.objects.active = receiver
        receiver.select_set(True)
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.label(text="选择要应用的修改器:")
        col = layout.column(align=True)
        for entry in self.resource_list:
            row = col.row()
            row.prop(entry, 'selected', text=entry.name)

# =============================================================================
# 3. 独立 UI 面板
# =============================================================================

class XXMI_PT_ShapeKeyTools(Panel):
    bl_label = "保留形态键应用修改器"
    bl_idname = "XXMI_PT_ShapeKeyTools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    # 挂载到主菜单
    bl_parent_id = "XXMI_PT_Sidebar" 
    bl_options = {'DEFAULT_CLOSED'}
    # 放在 Import (0) 和 Export Settings (10) 之间
    bl_order = 1 

    def draw(self, context):
        layout = self.layout
        
        obj = context.active_object
        is_valid = False
        if obj and obj.type == 'MESH' and obj.data.shape_keys:
            if len(obj.data.shape_keys.key_blocks) > 1:
                is_valid = True
        
        col = layout.column(align=True)
        col.enabled = is_valid
        
        col.operator("xxmi.apply_mods_sk", text="应用全部修改器", icon='MODIFIER')
        col.operator("xxmi.apply_subd_sk", text="仅应用细分", icon='MOD_SUBSURF')
        col.operator("xxmi.apply_mods_choice_sk", text="选择应用修改器...", icon='CHECKBOX_HLT')
        
        if not is_valid:
            layout.label(text="需选中带形态键的网格", icon="INFO")

# =============================================================================
# 4. 注册
# =============================================================================

classes = (
    XXMI_SK_Resource,
    XXMI_OT_ApplyModsKeepSK,
    XXMI_OT_ApplySubdKeepSK,
    XXMI_OT_ApplyModsChoiceKeepSK,
    XXMI_PT_ShapeKeyTools,
)

def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError:
            # 如果已经注册过，直接跳过，不报错
            pass

def unregister():
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError:
            # 如果类已经被注销或不存在，跳过
            pass