import bpy
import json
import urllib.request
import urllib.error
import re
from bpy.types import Operator, PropertyGroup, Panel
from bpy.props import StringProperty, FloatProperty, CollectionProperty, PointerProperty

# =============================================================================
# 1. 全局配置与 API 调用 (完全还原原版)
# =============================================================================

API_URL = "https://api.deepseek.com/chat/completions"
AI_MODEL = "deepseek-chat"

def extract_json_from_text(text):
    """提取 JSON 字符串"""
    try:
        pattern = r"```json(.*?)```"
        match = re.search(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        
        start = text.find('{')
        end = text.rfind('}') + 1
        if start != -1 and end != -1:
            return text[start:end]
        return text
    except Exception:
        return text

def call_ai_translation(names_list, api_key):
    """发送请求给 AI API (还原原版 urllib 实现)"""
    
    system_prompt = (
        "你是一个Blender形态键翻译工具。请将传入的英文形态键列表翻译为中文。"
        "要求：1. 仅返回一个合法的 JSON 对象 {'原名': '译名'}。2. 保留常用缩写。3. 不包含Markdown。"
    )
    
    user_prompt = json.dumps(names_list)

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}"
    }

    payload = {
        "model": AI_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"{user_prompt}"}
        ],
        "temperature": 0.1
    }

    try:
        req = urllib.request.Request(
            API_URL, 
            data=json.dumps(payload).encode('utf-8'), 
            headers=headers, 
            method='POST'
        )
        
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode('utf-8'))
            content = data['choices'][0]['message']['content']
            json_str = extract_json_from_text(content)
            return json.loads(json_str)
            
    except urllib.error.HTTPError as e:
        print(f"HTTP Error: {e.code} - {e.read().decode()}")
        return None
    except Exception as e:
        print(f"Error: {str(e)}")
        return None

# =============================================================================
# 2. 数据结构 (同步逻辑)
# =============================================================================

def update_shape_key_global(self, context):
    """智能全局同步逻辑"""
    target_key_name = self.name
    target_value = self.value
    
    # 遍历当前场景中的所有物体
    for obj in context.scene.objects:
        if obj.type != 'MESH' or not obj.data.shape_keys:
            continue
        
        kb = obj.data.shape_keys.key_blocks.get(target_key_name)
        if kb and kb.value != target_value:
            kb.value = target_value

class XXMI_ShapeKeyProxyItem(PropertyGroup):
    name: StringProperty()
    value: FloatProperty(
        name="Value", 
        min=0.0, max=1.0, 
        update=update_shape_key_global, 
        description="同步场景所有物体"
    )

# 为了不修改 __init__.py 也能存 Key，我们把 Key 放在属性组里
class XXMI_AI_Properties(PropertyGroup):
    api_key: StringProperty(
        name="API Key",
        description="请输入 DeepSeek API Key",
        subtype='PASSWORD' # 显示为星号
    )

# =============================================================================
# 3. 操作符
# =============================================================================

class XXMI_OT_TranslateShapeKeys(Operator):
    bl_idname = "xxmi.translate_shape_keys"
    bl_label = "AI 翻译形态键"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        # 直接从场景属性获取 Key，不再去偏好设置找
        props = context.scene.xxmi_ai_props
        api_key = props.api_key
        
        if not api_key:
            self.report({'ERROR'}, "请先在上方输入框填写 API Key")
            return {'CANCELLED'}

        obj = context.active_object
        if not obj or not obj.data.shape_keys:
            self.report({'WARNING'}, "请选择一个参考模型")
            return {'CANCELLED'}

        key_blocks = obj.data.shape_keys.key_blocks
        original_names = [kb.name for kb in key_blocks if kb.name != "Basis"]
        
        if not original_names:
            return {'FINISHED'}

        self.report({'INFO'}, f"正在请求 AI 翻译 ({len(original_names)} 个)...")
        
        translation_map = call_ai_translation(original_names, api_key)
        
        if translation_map:
            count = 0
            scanned_objects = 0
            for scene_obj in context.scene.objects:
                if scene_obj.type == 'MESH' and scene_obj.data.shape_keys:
                    scanned_objects += 1
                    for kb in scene_obj.data.shape_keys.key_blocks:
                        if kb.name in translation_map:
                            kb.name = translation_map[kb.name]
                            count += 1
                            
            self.report({'INFO'}, f"完成！已更新 {scanned_objects} 个物体的 {count} 个键名")
            bpy.ops.xxmi.refresh_shape_key_proxies()
        else:
            self.report({'ERROR'}, "翻译失败，请检查网络或 Key")

        return {'FINISHED'}

class XXMI_OT_RefreshShapeKeyProxies(Operator):
    bl_idname = "xxmi.refresh_shape_key_proxies"
    bl_label = "刷新/读取列表"
    bl_description = "读取当前物体的形态键列表"

    def execute(self, context):
        scene = context.scene
        obj = context.active_object
        
        scene.xxmi_shape_key_proxies.clear()
        
        if obj and obj.type == 'MESH' and obj.data.shape_keys:
            for kb in obj.data.shape_keys.key_blocks:
                if kb.name == "Basis":
                    continue
                item = scene.xxmi_shape_key_proxies.add()
                item.name = kb.name
                item.value = kb.value
            self.report({'INFO'}, f"已加载 {len(scene.xxmi_shape_key_proxies)} 个控制器")
        else:
            self.report({'WARNING'}, "请先选中一个包含形态键的模型")
        
        return {'FINISHED'}

# =============================================================================
# 4. UI 面板
# =============================================================================

class XXMI_PT_AIShapeKeyTools(Panel):
    bl_label = "AI 形态键工具"
    bl_idname = "XXMI_PT_AIShapeKeyTools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "XXMI_PT_Sidebar" # 挂载到 XXMI Tools
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 3

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        
        # 确保属性已加载
        if not hasattr(scene, "xxmi_ai_props"):
            layout.label(text="需重启插件", icon="ERROR")
            return

        props = scene.xxmi_ai_props
        
        # 1. API Key 输入区 (直接画在面板上！)
        box = layout.box()
        box.label(text="API 配置:", icon='PREFERENCES')
        box.prop(props, "api_key") # 直接在这里输入，不用去偏好设置了
        
        # 2. 翻译功能
        layout.separator()
        box = layout.box()
        box.label(text="翻译功能", icon='WORLD')
        box.operator("xxmi.translate_shape_keys", text="全局翻译 (DeepSeek)", icon='AUTO')
        
        # 3. 同步功能
        layout.separator()
        box = layout.box()
        row = box.row()
        row.label(text="同步控制", icon='ARMATURE_DATA')
        row.operator("xxmi.refresh_shape_key_proxies", text="", icon='FILE_REFRESH')
        
        if len(scene.xxmi_shape_key_proxies) > 0:
            col = box.column(align=True)
            for item in scene.xxmi_shape_key_proxies:
                row = col.row(align=True)
                row.label(text=item.name)
                row.prop(item, "value", text="")
            box.label(text="数值将自动同步至全场景", icon='INFO')
        else:
            col = box.column(align=True)
            col.label(text="请选中模型并点击刷新", icon='ERROR')

# =============================================================================
# 5. 注册
# =============================================================================

classes = (
    XXMI_ShapeKeyProxyItem,
    XXMI_AI_Properties,
    XXMI_OT_TranslateShapeKeys,
    XXMI_OT_RefreshShapeKeyProxies,
    XXMI_PT_AIShapeKeyTools,
)

def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except ValueError: pass
    
    # 注册场景属性 (PropertyGroups)
    if not hasattr(bpy.types.Scene, "xxmi_shape_key_proxies"):
        bpy.types.Scene.xxmi_shape_key_proxies = CollectionProperty(type=XXMI_ShapeKeyProxyItem)
        
    if not hasattr(bpy.types.Scene, "xxmi_ai_props"):
        bpy.types.Scene.xxmi_ai_props = PointerProperty(type=XXMI_AI_Properties)

def unregister():
    if hasattr(bpy.types.Scene, "xxmi_shape_key_proxies"):
        del bpy.types.Scene.xxmi_shape_key_proxies
    if hasattr(bpy.types.Scene, "xxmi_ai_props"):
        del bpy.types.Scene.xxmi_ai_props
        
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except RuntimeError: pass