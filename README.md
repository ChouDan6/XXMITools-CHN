# XXMI Tools CHN

基于 [XXMI Tools](https://github.com/leotorrez/XXMITools) v1.6.7 的中文修改版，添加多项实用功能，方便 Mod 导出与调试。  
如果更新XXMI Tools，将migoto中的python文件和templatea文件迁移即可。  
> 测试环境：Blender 4.4 / 5.0

---

## 安装

1. 下载本仓库源码，按照Blender要求打包
2. Blender → 编辑 → 偏好设置 → 插件 → 从磁盘安装 → 选择 ZIP
3. 启用 `XXMI_Tools` 插件
4. 侧边栏（N 面板）出现 **XXMI Tools** 标签

---

## 功能概览

### 1. 主面板（XXMI Tools）

位于 3D 视口侧边栏，提供核心导出功能：

| 设置项 | 说明 |
|---|---|
| Dump Folder | 指向包含 hash.json 的 dump 文件夹 |
| Output Folder | Mod 输出目录 |
| Game | 选择目标游戏（原神/崩铁/绝区零/鸣潮等） |

#### Export Settings（导出设置）

- **Ignore hidden objects** — 忽略隐藏物体
- **Only export selected** — 仅导出选中物体
- **Apply modifiers and shapekeys** — 应用修改器和形态键
- **Normalize weights** — 权重归一化
- **Mod textures** — 复制并写入贴图
- **Write buffers / Write ini** — 控制导出内容
- **Outline Optimization** — 轮廓线优化（推荐最终导出时开启）
- **Use custom template** — 使用自定义 INI 模板文件（为原神新增Hash贴图风格等多种模板）
- **Credit** — Mod 加载时显示的作者名

### 2. 自动补充顶点组导出

解决导出时因顶点组序号不连续（如缺少中间编号）导致的错误。

- **导出可见模型** — 一键补全 0~Max 顶点组并排序，然后自动调用标准导出流程
- **预览 INI** — 在文本编辑器中预览即将生成的 INI 文件内容，方便导出前检查
- **添加 DISABLED 前缀** — 勾选后导出的 INI 文件名自动添加 `DISABLED` 前缀，使 mod 默认不生效

### 3. 顶点色预设

快速为选中模型设置顶点色（COLOR 属性）。

#### 游戏预设一键应用

| 按钮 | R | G | B | A | 备注 |
|---|---|---|---|---|---|
| 原神 | 1.0 | 0.216 | 0.216 | 0.302 | |
| 崩铁 | 1.0 | 0.216 | 0.0 | 0.302 | |
| 绝区零 | 0.216 | 0.216 | 0.0 | 0.0 | |
| 鸣潮 | 0.216 | 0.216 | 0.0 | 0.0 | 同时生成 COLOR + COLOR1 |

#### 自定义颜色

手动调节 RGBA 四通道后点击「应用顶点色」。

#### 网格工具

- **UV 重命名** — 按 TEXCOORD.xy / TEXCOORD1.xy 规范重命名 UV 层
- **材质分离** — 按材质拆分网格并以材质名重命名

### 4. 保留形态键应用修改器

在不丢失形态键的前提下应用修改器：

- **应用全部修改器** — 保留所有形态键
- **仅应用细分** — 只应用细分曲面修改器
- **应用选定修改器** — 选择特定修改器应用

### 5. 骨骼生成器

根据顶点组权重快速生成骨架：

- **生成单根骨骼** — 为选中顶点组生成一根骨骼
- **生成完整骨架** — 为所有数字顶点组生成骨骼
- **绑定网格** — 添加骨架修改器
- **一键生成并绑定** — 上述流程一步完成
- **清理空顶点组** — 删除无权重的顶点组

### 6. AI 形态键工具

使用 DeepSeek API 翻译形态键名称，支持全局同步调整。

- **AI 翻译形态键** — 调用 AI 将日文/英文形态键名翻译为中文
- **刷新/读取列表** — 重新加载当前形态键列表

### 7. 锁定选中顶点权重

在编辑权重时保护指定顶点不被修改：

- **锁定选中点** — 记录并保护选中顶点的权重
- **清空所有锁定** — 移除所有锁定
- **选中已锁定点** — 快速选择所有被锁定的顶点

### 8. 网格工具

高级网格编辑功能面板：

- **创建/应用/删除合并对象** — 多网格合并雕刻工作流
- **属性传递** — 在网格间传递属性数据
- **冻结/恢复顶点位置** — 暂存顶点位置用于对比修改
- **标记固定/移动元素** — 辅助合并操作

### 9. UV 贴图合并工具

将多个部件的贴图合并到一张贴图上：

- **预计算最佳尺寸** — 自动计算合并后的贴图尺寸
- **执行自动合并** — 一键合并
- **手动流程** — 生成面片网格 → 确认合并

### 10. MMD 辅助工具

- **MMD 材质分组** — 按 MMD 材质自动分组

### 11. XXMI Toolbox

原版 XXMI Tools 的工具箱功能：

- Remove unused Vertex Groups — 删除未使用的顶点组
- Merge shared name Vertex Groups — 合并同名顶点组
- Fill gaps in Vertex Groups — 填补顶点组编号空缺
- Clean UV Names — 清理 UV 命名
- Reset Vertex Colors — 重置顶点色

### 12. 更新器

内置插件自动更新检查。

---

## 支持的游戏

- 原神 (Genshin Impact)
- 崩坏：星穹铁道 (Honkai: Star Rail)
- 绝区零 (Zenless Zone Zero)
- 鸣潮 (Wuthering Waves)

---

## 致谢

- 原始插件：[DarkStarSword](https://github.com/DarkStarSword/3d-fixes)
- XXMI Tools：[LeoTorreZ](https://github.com/leotorrez/XXMITools)
- 贡献者：SilentNightSound, HazrateGolabi, HummyR, SinsOfSeven, SpectrumQT, Sora, Comilarex等









