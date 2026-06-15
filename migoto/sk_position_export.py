#!/usr/bin/env python3
"""
XXMI Tools - Shape Key Position Export

User picks a mesh → climb collection tree upward → match ancestor
collection name against hash.json → set SK on all VBLayout objects
in that collection → export → copy Position.buf as Position{idx}.buf.
"""

import shutil
import time
from pathlib import Path

import bpy
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import PointerProperty, StringProperty

from .data.hash_json import HashJsonData


# =============================================================================
# 1. PropertyGroup
# =============================================================================

class XXMI_SKPositionProperties(PropertyGroup):
    target_object: PointerProperty(
        name="Target",
        type=bpy.types.Object,
        poll=lambda self, obj: (
            obj.type == 'MESH'
            and obj.data.shape_keys is not None
            and len(obj.data.shape_keys.key_blocks) > 1
        ),
        description="Pick any mesh in the component collection",
    )
    export_folder: StringProperty(
        name="Export Folder",
        subtype='DIR_PATH',
        description="Position1.buf, Position2.buf, …",
    )


# =============================================================================
# 2. 查找 — 爬集合树
# =============================================================================

def _all_ancestor_collections(obj):
    """从 obj 直接所属的集合开始，向上爬所有父集合。去重、按层级浅→深排序。"""
    ancestors: list[bpy.types.Collection] = []
    seen: set[str] = set()
    stack: list[bpy.types.Collection] = list(obj.users_collection)

    while stack:
        col = stack.pop()
        if col.name in seen:
            continue
        seen.add(col.name)
        ancestors.append(col)
        # 找所有包含当前集合作为子集合的父集合
        for candidate in bpy.data.collections:
            if candidate.name in seen:
                continue
            if any(c.name == col.name for c in candidate.children):
                stack.append(candidate)

    return ancestors


def _find_component_and_objects(target, hd):
    """爬 target 的集合树 → 祖先集合名在 hash.json part.fullname 中出现即命中。"""
    ancestors = _all_ancestor_collections(target)

    print(f"<XXMI SK Export> 对象 {target.name} 的集合链: "
          f"{' → '.join(reversed([c.name for c in ancestors]))}")

    for col in ancestors:
        cn = col.name.lower()
        for comp in hd.components:
            if comp.draw_vb == "":
                continue
            for part in comp.parts:
                pn = part.fullname.lower()
                if cn in pn:
                    sk_objs = _collect_sk_objects(col)
                    if sk_objs:
                        return comp, col, sk_objs

    print(f"<XXMI SK Export> hash.json parts: "
          f"{[p.fullname for comp in hd.components for p in comp.parts]}")
    return None, None, []


def _collect_sk_objects(collection):
    """递归收集 collection 中所有带形态键的 MESH 对象。"""
    result: list[bpy.types.Object] = []
    seen: set[str] = set()

    def _walk(col):
        for obj in sorted(col.all_objects, key=lambda o: o.name):
            if obj.type != 'MESH':
                continue
            if obj.name_full in seen:
                continue
            seen.add(obj.name_full)
            if obj.data.shape_keys is None:
                continue
            if len(obj.data.shape_keys.key_blocks) <= 1:
                continue
            result.append(obj)

    _walk(collection)
    return result


# =============================================================================
# 3. 核心操作符
# =============================================================================

class XXMI_OT_SKPositionExport(Operator):
    bl_idname = "xxmi.sk_position_export"
    bl_label = "Export SK Positions"
    bl_description = (
        "For every adjustable shape key in the picked object's collection:\n"
        "  set SK=1 → export → Position{idx}.buf"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        tgt = context.scene.xxmi_sk_position.target_object
        return tgt is not None and tgt.type == 'MESH'

    def execute(self, context):
        start = time.time()
        scene = context.scene
        xxmi = scene.xxmi
        props = scene.xxmi_sk_position
        target = props.target_object

        if not props.export_folder:
            self.report({'ERROR'}, "请先设置导出文件夹")
            return {'CANCELLED'}
        sk_dir = Path(props.export_folder)
        sk_dir.mkdir(parents=True, exist_ok=True)

        dump_path = Path(xxmi.dump_path)
        if dump_path.suffix != "":
            dump_path = dump_path.parent
        hash_json = dump_path / "hash.json"
        if not hash_json.exists():
            self.report({'ERROR'}, f"hash.json 不存在: {hash_json}")
            return {'CANCELLED'}

        dest = (
            Path(xxmi.destination_path)
            if xxmi.destination_path
            else dump_path.parent / f"{dump_path.stem}Mod"
        )

        try:
            hd = HashJsonData(hash_json)
        except Exception as e:
            self.report({'ERROR'}, f"hash.json 解析失败: {e}")
            return {'CANCELLED'}

        component, comp_col, comp_objects = _find_component_and_objects(target, hd)
        if component is None:
            self.report(
                {'ERROR'},
                f"未匹配到 hash.json 部件。\n"
                f"详情见控制台 (Window → Toggle System Console)。"
            )
            return {'CANCELLED'}

        comp_fullname = component.fullname
        print(f"<XXMI SK Export> 命中集合: {comp_col.name}")
        print(f"<XXMI SK Export> Component: {comp_fullname}")

        all_sk: set[str] = set()
        for obj in comp_objects:
            for sk in obj.data.shape_keys.key_blocks:
                if sk.name != "Basis" and sk.slider_min < sk.slider_max:
                    all_sk.add(sk.name)
        sk_list = sorted(all_sk)
        if not sk_list:
            self.report({'WARNING'}, "未找到可调节的形态键")
            return {'CANCELLED'}

        print(f"<XXMI SK Export> {len(comp_objects)} 对象: "
              f"{[o.name for o in comp_objects]}")
        print(f"<XXMI SK Export> {len(sk_list)} SK: {sk_list}")

        saved = {}
        for obj in comp_objects:
            for sk in obj.data.shape_keys.key_blocks:
                saved[(obj.name_full, sk.name)] = sk.value

        old_ini = xxmi.write_ini
        old_tex = xxmi.copy_textures
        xxmi.write_ini = False
        xxmi.copy_textures = False

        pos_fn = f"{comp_fullname}Position.buf"
        total = 0

        try:
            for idx, sk_name in enumerate(sk_list, start=1):
                for obj in comp_objects:
                    for sk in obj.data.shape_keys.key_blocks:
                        sk.value = 1.0 if sk.name == sk_name else 0.0
                bpy.context.view_layer.update()

                print(f"<XXMI SK Export> [{idx}] {sk_name} …")
                try:
                    bpy.ops.xxmi.exportadvanced('INVOKE_DEFAULT')
                except Exception as e:
                    self.report({'ERROR'}, f"[{idx}] {sk_name} 导出失败: {e}")
                    continue

                src = dest / pos_fn
                if src.exists():
                    tgt = sk_dir / f"{comp_fullname}Position{idx}.buf"
                    shutil.copy2(str(src), str(tgt))
                    total += 1
                    print(f"<XXMI SK Export>     {pos_fn} → {tgt.name}")
                else:
                    print(f"<XXMI SK Export>     ⚠ 未找到 {pos_fn}")

        finally:
            for (on, sn), val in saved.items():
                for obj in comp_objects:
                    if obj.name_full == on and sn in obj.data.shape_keys.key_blocks:
                        obj.data.shape_keys.key_blocks[sn].value = val
                        break
            bpy.context.view_layer.update()
            xxmi.write_ini = old_ini
            xxmi.copy_textures = old_tex

        self.report(
            {'INFO'},
            f"已导出 {total} 文件到 {sk_dir} ({time.time()-start:.1f}s)"
        )
        return {'FINISHED'}


# =============================================================================
# 4. UI
# =============================================================================

class XXMI_PT_SKPositionExport(Panel):
    bl_label = "Shape Key Position Export"
    bl_idname = "XXMI_PT_SKPositionExport"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "XXMI Tools"
    bl_parent_id = "XXMI_PT_Sidebar"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 4

    def draw(self, context):
        layout = self.layout
        props = context.scene.xxmi_sk_position
        target = props.target_object

        layout.prop(props, "target_object", text="Object")

        if target and target.type == 'MESH':
            sks = target.data.shape_keys
            if sks and len(sks.key_blocks) > 1:
                n = sum(1 for sk in sks.key_blocks
                        if sk.name != "Basis" and sk.slider_min < sk.slider_max)
                layout.label(text=f"可调形态键: {n}")

                # 显示集合链
                chain = _all_ancestor_collections(target)
                if chain:
                    names = " → ".join(reversed([c.name for c in chain]))
                    layout.label(text=f"集合链: {names}", icon='OUTLINER_COLLECTION')

        layout.prop(props, "export_folder", text="Folder")

        row = layout.row(align=True)
        row.enabled = (target is not None and target.type == 'MESH'
                       and bool(props.export_folder))
        row.operator("xxmi.sk_position_export", text="Export All", icon='EXPORT')


# =============================================================================
# 5. Register
# =============================================================================

def register():
    bpy.types.Scene.xxmi_sk_position = bpy.props.PointerProperty(
        type=XXMI_SKPositionProperties
    )


def unregister():
    if hasattr(bpy.types.Scene, "xxmi_sk_position"):
        del bpy.types.Scene.xxmi_sk_position
