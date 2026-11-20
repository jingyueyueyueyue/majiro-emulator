# Majiro 模拟器

**版本 0.5.1**

一款面向 Android 手机和平板的 Majiro 引擎模拟器，让熟悉的视觉小说也能随身阅读。选择本地游戏目录后，即可读取其中的脚本、图片与音频，按原有比例呈现游戏画面。

支持历史记录与语音回放、存读档及截图预览、自动播放和快进。操作上提供直接触摸和虚拟鼠标，也能从游戏内打开菜单、调整声音与阅读设置。

可以使用已有的汉化资源，也可以自行配置在线翻译服务。游戏本体需自行准备。

## 目录说明

- `player/`：独立模拟器核心、运行时和 Android 宿主。
- `scripts/build/`、`scripts/analysis/`、`scripts/convert/`：构建、验证和格式处理。
- `scripts/analysis/test_streaming_media.py`：大音频分块读取、缓存与小内存回归；`test_android_library_batch.py` 支持启动后持续观察及真实正文输入，使用 `--pages` 检查连续正文，逐项保存截图与日志。
- `scripts/analysis/test_extboxfill.py`、`test_runtime_performance.py`：对话框透明遮罩、按需资源元数据、重复画面提交及视频像素传输专项，已接入集中验收。
- `scripts/analysis/benchmark_composition.py`：在同一 Windows 主机对照安卓合成代码的整帧耗时、混合耗时和输出像素；支持传入上一版结果和 17 张游戏图。
- `scripts/analysis/test_startup_performance.py`、`test_android_keyboard.py`：启动预检、指令查找与编码快速路径，以及平板键盘的 PC 输入语义回归；字形专项为 `test_emulator_glyph_contract.py`，实机像素检查由 `RenderInstrumentation` 执行。
- `scripts/analysis/test_android_event_wait.py`：输入唤醒、轮询哨兵过滤及空闲休眠回归；实机批处理可用 `--profile-startup` 归档 CPU 采样，记录标题菜单和首次正文时间。
- `scripts/analysis/test_original_text_shadow.py`、`test_original_typefaces.py`：原版阴影／描边模式、Windows 字体族别名和用户私有字体选择；`scripts/build/install_android_font.py --font 用户字体路径` 校验后将个人 MS Gothic 集合装入当前 Android 包的私有字体目录，不加入 APK。
- `localization/`：本地翻译输入及生成数据；`localization/player/ruri/` 保存原始补丁适配元数据，不进入公开源码包。
- `resources/`、`reports/`、`archive/`：本地产物、验证报告和按日期归档的证据。

[Android ARM64 发布](https://github.com/jingyueyueyueyue/majiro-emulator/releases)

0.5.1（内部版本 28）优化了全屏淡入淡出、带透明遮罩的立绘和逐行更新的对话框。ARM64 播放器使用 NEON 执行 Majiro 的整数混合，按原顺序绘制页面，沿用原版缩放采样。安卓纹理只上传首尾变化行之间的像素，渲染器重建时从保留的完整画面恢复。调试命令 `render_stats` 提供累计合成时间 `compose_us` 和实际上传字节数 `upload_bytes`。

TyranorNext 会将本应用作为第三方运行时模块发现。模块包名为 `com.tyranor.runtime.majiro`，启动 action 为 `com.tyranor.runtime.action.LAUNCH`，接收 `com.tyranor.runtime.extra.FOLDER_PATH`、`GAME_ROOT`、`GAME_TITLE` 和 `PROTOCOL_VERSION`。Tyn 只传绝对路径时，本模块优先使用 Majiro 已获授权且覆盖该目录的 SAF 授权；对支持转交授权的宿主，也接受 Intent.data tree URI、`GAME_ROOT` 子目录 ID 和临时只读授权。主 App 不传存档或设置。

2026-11-18 更新：修复从 Tyn 启动时共享存储中已有汉化补丁可能因权限错误无法读取的问题。如果未向 Majiro 授权该目录，先在 Majiro 游戏库添加并授权一次。安装包可覆盖更新，版本保持 0.5。

2026-11-16 更新：修正半透明填充的反向透明度语义，恢复对话框背景与按钮。补齐并强制检查 Android 视频依赖；资源元数据改为按需读取，减少归档文件名转换、静态画面重复提交和视频逐像素搬运。版本保持 0.5。此包的对话框已获实机确认，字体顶部与后续性能调优继续单独处理。

2026-11-17 续修：字形缓存按真实字体隔离并限制内存，缺失的补丁字体只检查一次；动画及后台线程共同决定下一次唤醒，空闲等待允许 SDL 输入中断。启动时使用有序指令查找、复用单脚本字符串转码，并在完整校验声明补丁后延迟解析未修改的原文脚本；详细像素摘要仅在主动诊断时计算。诊断启动转发测试存档命名空间，批处理检查实际隔离目录；历史存档测试同时接受原包名与模块包名。集中验收通过后统一打包，正式结果见本地 `reports/font_performance_followup.json`。

原版字体对照续修：文字阴影的高字节改按原版效果模式解释，恢复细描边及模式 3 的浅色偏移，避免误作描边半径；原文模式保留游戏指定字族，不加载汉化补丁字体，默认后备字形采用日文区域。可使用用户在本机提供的 MS Gothic／MS PGothic 私有字体；缺少同一字体文件时，安卓后备字体仍可能与 Windows 原版不同。内部版本 26，公开版本保持 0.5。

《琉璃交错》加载专项：修复 Android 事件等待反复唤醒造成的 CPU 空转，输入到达时唤醒独立信号量；大块无蒙版转场采用连续通道混合，屏幕偏移仅清空外露边缘。游戏自身等待、演出与逐字显示保持原有时序，专项记录分别衡量启动到标题及点击开始到正文。

0.2 集中修复原版语义、图像与输入、音视频生命周期、存读档回滚和引擎配置识别，并优化字符串哈希、图像复制及特征扫描。原文／汉化资源方案已合入：启动前校验资源来源、编码和补丁完整性，隔离原文、本地汉化及在线翻译，保留并分区旧存档。

修复煌乙女章节界面右键触发菜单函数缺失的错误，按游戏实际提供的菜单入口分派，保留《ルリのかさね》的章节暂停与返回行为。读档和音乐压栈恢复按游戏资源名重新取得音乐、语音、音效和循环段，解决旧音频缓存丢失导致模拟器停止运行的问题，并保留播放位置、暂停与当前音量；运行中被清理或截断的缓存会自动重建，音频和字体缓存损坏或写入不完整后会重新生成。

修复旧版升级后存档列表为空的问题，自动校验并恢复兼容的旧存档、进度和缩略图，补齐原文／汉化存档目录的识别。修复保存、复制、删除和修改存档说明失败后的记录回滚，恢复旧版外部存档导入，并修复导出途中取消可能留下空文件的问题。

修复重复函数哈希引起的菜单和初始化分派错误，以及系统调用返回值丢失造成的运行中断；音频与字体缓存增加内容校验和损坏重建。

修复《水仙·菫》播放大于 64 MiB 的音乐时停止运行的问题。封包和独立音频文件改为分块提取并原子写入播放缓存，保留缓存校验和损坏重建；不会为音频一次性分配整块 Java 或原生内存。

完善页面句柄重用、影片定位和纹理、音乐出栈音量、动态资源、图片密码及旧存档脚本别名的恢复。补齐原版 V1.102／V1.103／V1.104／V1.105 存档、系统变量、混合编码和资源句柄恢复，增加历史版本升级与真实存档续跑测试。修复片尾大倍率缩放、滚轮、左右修饰键和光标显示问题。

## 目前测试过的游戏

- Amber Quartz
- 僕がサダメ 君には翼を
- Happy Princess
- Idol ☆ Revolution
- 痴漢貴族
- まじかるカナン２
- おーぱんつ
- Reconquista
- ルリのかさね ～いもうと物語り～
- Switch!! ～ボクがナツに想うこと～
- White ～blanche comme la lune～
- Narcissu 姫子篇
- Narcissu アイリス
- Narcissu 1st & 2nd
- Narcissu 菫
- Narcissu Zero
- そして煌めく乙女と秘密^5
