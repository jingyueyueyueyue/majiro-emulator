# 独立 Majiro Android 模拟器

本目录属于独立模拟器项目。最终平台为 Android ARM64，Windows 仅用作原版对照和格式／像素调试。旧 Artemis 移植位于项目根的 `runtime/`、`projects/windows/` 与 `resources/native/`，本模拟器不会加载或打包其运行时代码。

目录：

- `core/`：C++20 ARC/MJO、图像、存档格式及平台无关像素接口。
- `runtime/`：独立 Lua Majiro VM、系统调用及按引擎指纹选择的行为。
- `native/`：独立像素算法源码；Windows I/O 只用于本项目的分析探针，Android 由适配生成器取出平台无关算法。
- `tables/`：本项目固定的指令表与已知调用名称。分析结果可以与旧移植共用，运行时文件不自动同步。
- `android/`：SDL、JNI、SAF、触摸、媒体和生命周期宿主。

构建入口仍按用途放在根 `scripts/build/`：`build_player_core.py`、`build_android_game.py`、`build_android_probe.py`。`build_player_native.py` 只生成 `resources/player/native/` 内的独立 Windows 对照 DLL。`build_native.py` 及 `build_windows.py` 属于旧移植，独立模拟器构建不会调用它们。

资源从用户选择的游戏目录按需读取；游戏 ARC、EXE 和汉化资料可作为共同只读研究输入。实际存档、缓存和程序状态按模拟器自己的游戏身份隔离。APK 内部使用 `runtime/` 逻辑路径，但打包来源必须是本目录的 `runtime/`。

加载 ARC 时，自动将游戏根目录与声明的本地补丁目录作文件大小及逐字节比较，重复的本地补丁不再作为原版资源加载。日语、在线与本地汉化模式共享此分层规则；不同内容的更新包正常保留。不使用游戏指纹、游戏白名单或认证，也不改写用户资源。回归入口为 `scripts/analysis/test_archive_layers.py`，已纳入主测试流程。

当前按用户要求先补齐入口，之后再做全面语义和实机验收。快速盘点运行 `scripts/analysis/inventory_player_entries.py`，结果见根 `reports/player_entry_progress.json/.md`；注册覆盖不能等同完整兼容。
