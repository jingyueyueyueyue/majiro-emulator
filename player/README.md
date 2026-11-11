# 独立 Majiro Android 模拟器

本目录属于独立模拟器项目。最终平台为 Android ARM64，Windows 仅用作原版对照和格式／像素调试。旧 Artemis 移植位于项目根的 `runtime/`、`projects/windows/` 与 `resources/native/`，本模拟器不会加载或打包其运行时代码。

目录：

- `core/`：C++20 ARC/MJO、图像、存档格式及平台无关像素接口。
- `runtime/`：独立 Lua Majiro VM、系统调用及按原 EXE 实现特征选择的行为；不依赖游戏名或 EXE 指纹白名单。
- `native/`：独立像素算法源码；Windows I/O 只用于本项目的分析探针，Android 由适配生成器取出平台无关算法。
- `tables/`：本项目固定的指令表与已知调用名称。分析结果可以与旧移植共用，运行时文件不自动同步。
- `android/`：SDL、JNI、SAF、触摸、媒体和生命周期宿主。

构建入口仍按用途放在根 `scripts/build/`：`build_player_core.py`、`build_android_game.py`、`build_android_probe.py`。`build_player_native.py` 只生成 `resources/player/native/` 内的独立 Windows 对照 DLL。`build_native.py` 及 `build_windows.py` 属于旧移植，独立模拟器构建不会调用它们。

资源从用户选择的游戏目录按需读取；游戏 ARC、EXE 和汉化资料可作为共同只读研究输入。实际存档、缓存和程序状态按模拟器自己的游戏身份隔离。APK 内部使用 `runtime/` 逻辑路径，但打包来源必须是本目录的 `runtime/`。

加载 ARC 时，自动将游戏根目录与声明的本地补丁目录作文件大小及逐字节比较，重复的本地补丁不再作为原版资源加载。日语、在线与本地汉化模式共享此分层规则；不同内容的更新包正常保留。不使用游戏指纹、游戏白名单或认证，也不改写用户资源。回归入口为 `scripts/analysis/test_archive_layers.py`，已纳入主测试流程。

统一构建与回归入口为 `python -B -X utf8 scripts/build/run_emulator_audit.py --build --package`。它先构建同源核心与像素提供器，运行原 EXE 差分、正式 Java/Lua 配置、存档故障注入和宿主契约检查，通过后打包。`--suite` 可只复查指定测试；按上次耗时优先调度长测试，报告同时绑定生产源码与测试源码。

快速盘点运行 `scripts/analysis/inventory_player_entries.py`，结果见根 `reports/player_entry_progress.json/.md`；注册覆盖和桌面差分通过均不能替代 Android 实机全路线验收。游戏素材、译文、签名密钥、个人配置、生成的 `patches/` 数据与测试归档均保留在本地。

文本资源现按原文、本地、在线模式选择来源和编码。自动模式只有在声明资源齐全且启动前校验通过后才启用本地层；缺失、冲突或编码未声明时停止，避免静默混层。本地图片 ARC 即使同时列在普通 ARC 中，也只能提供图像。原文和在线共用原始脚本，在线译文只用于显示。

现有 schema 1 的原 MJO＋顺序 TXT 适配和 pinned raw/tunnel 补丁继续使用原哈希及锚点，不更改译文。schema 2 增加 `encoded_text`、`archive_encodings`、`resource_hashes`、`metadata_hashes`、`vm_encoding`，明确 CP932、CP936 或 UTF-8；`shared_image_archives` 可明确选择原文／在线共用的图像层。补丁 Lua 仅作为受限字面量表解析，不执行代码。

schema 1 的 `image_archives` 保持原有跨模式图像可见性，保证原始标题与菜单所需的图像仍可读取；归档中的 MJO、CFG、ENV、TXT 始终不通过图像作用范围加载。schema 2 默认只在本地模式加载图像，跨模式共享必须列入 `shared_image_archives`。

启动时校验有效脚本与配置，并建立最多 16 MiB 的会话字节快照；未缓存资源读取时必须匹配初始内容和来源。下一次启动重新校验，图像／音频不做全文身份哈希。文本身份包含有效文本、来源、适配器字节和 VM 编码；旧存档索引保留并分区，脚本输出按新身份隔离。专项入口为 `scripts/analysis/test_text_resources.py`，旧五个文本测试名称仍保留，统一审修内共享一次同源 Java／C++／Lua 验证。
