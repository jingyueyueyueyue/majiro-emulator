# Majiro 模拟器

**版本 0.2**

一款面向 Android 手机和平板的 Majiro 引擎模拟器，让熟悉的视觉小说也能随身阅读。选择本地游戏目录后，即可读取其中的脚本、图片与音频，按原有比例呈现游戏画面。

支持历史记录与语音回放、存读档及截图预览、自动播放和快进。操作上提供直接触摸和虚拟鼠标，也能从游戏内打开菜单、调整声音与阅读设置。

可以使用已有的汉化资源，也可以自行配置在线翻译服务。游戏本体需自行准备。

## 目录说明

- `player/`：独立模拟器核心、运行时和 Android 宿主。
- `scripts/build/`、`scripts/analysis/`、`scripts/convert/`：构建、验证和格式处理。
- `localization/`：本地翻译输入及生成数据；`localization/player/ruri/` 保存原始补丁适配元数据，不进入公开源码包。
- `resources/`、`reports/`、`archive/`：本地产物、验证报告和按日期归档的证据。

[下载 0.2 APK（Android ARM64）](https://github.com/jingyueyueyueyue/majiro-emulator/releases/download/v0.2/Majiro-0.2-arm64-v8a.apk)

0.2 集中修复原版语义、图像与输入、音视频生命周期、存读档回滚和引擎配置识别，并优化字符串哈希、图像复制及特征扫描。原文／汉化资源方案已合入：启动前校验资源来源、编码和补丁完整性，隔离原文、本地汉化及在线翻译，保留并分区旧存档。

修复煌乙女章节界面右键触发菜单函数缺失的错误，按游戏实际提供的菜单入口分派，保留《ルリのかさね》的章节暂停与返回行为。读档和音乐压栈恢复按游戏资源名重新取得音乐、语音、音效和循环段，解决旧音频缓存丢失导致模拟器停止运行的问题，并保留播放位置、暂停与当前音量；运行中被清理或截断的缓存会自动重建，音频和字体缓存损坏或写入不完整后会重新生成。

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
