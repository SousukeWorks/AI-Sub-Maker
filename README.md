# AI-Sub-Maker

版本：1.0.1

[下载 Windows 便携版](https://github.com/SousukeWorks/AI-Sub-Maker/releases/latest)：选择 `Windows11-x64.zip`，完整解压后运行 `AI-Sub-Maker.exe`，保留旁边的 `_internal` 文件夹。

Windows 11 日语字幕工具：用 **Soniox** 识别音视频并获取时间轴，用 **DeepSeek** 整批校对、翻译，生成上日语、下简体中文的 ASS 字幕。

Soniox 和 DeepSeek 需自行提供 API Key，并按各自服务收费。无需下载语音模型。

## 功能

- 支持 MP4、MP3 等音视频；按标点和说话人断句，保留原生词级时间戳。
- 新识别会向 Soniox 提供所选歌词摘录、歌名、英文词与启用的日文词库，辅助识别。
- 日中双语各显示一行，不强制换行；两种语言使用独立样式。
- 校对与翻译在一次请求中处理整批字幕；失败条目可单选、多选重试。
- 可编辑公演歌词库、人名、昵称及专有词；姓名按设定译名输出，昵称保留日文。
- 内置成员应援色，可修改字体、描边、阴影；合唱默认为粉字白边。
- 独立的“MP3＋图片 → MP4”工具。

## 使用

1. 选择音视频和 ASS 输出位置。
2. 在“Soniox”填写识别 Key，在“翻译”填写 DeepSeek Key。
3. 按需选择公演、导入歌词，或在“词库”修改人名与专有词。
4. 点击“Soniox 生成 ASS”。

在“失败重试”打开 `.retry.json` 可继续校对和翻译；保留 `.soniox.json` 可复用识别结果。未完成的字幕输出为 `.partial.ass`。

成员颜色在“词库 → 应援色”修改。“样式 → 成员 / 合唱标记与配色导出”可多选语段指定成员或合唱，直接重新导出，无须再次识别或翻译。自我介绍可辅助匹配；其他身份须自行确认。官网色名、来源和初始 RGB 见 [成员应援色](成员应援色.md)。

内置成员资料可直接使用；个人修改与 Key 保存在本机，记住 Key 时使用 Windows 加密。项目仅提供公演曲目目录，不附带完整歌词。

## 识别上下文

“Soniox”页默认开启“发送歌词和词库作为识别上下文”，可手动关闭。选择公演并保存歌词后，新识别会自动读取全部所选歌曲，提取歌名与英文词（如 `Maybe`），并加入启用的日文姓名、昵称和专有词；不发送中文译文和备注。

Soniox 上下文有容量限制。程序会去重、优先保留词条，并从各首歌均衡选取完整歌词行。日志显示实际发送的词条与歌词行数，`.soniox.json` 保存本次实际请求的上下文。它用于辅助识别，不保证按歌词原样输出，后续校对和翻译仍由 DeepSeek 处理。接口说明见 [Soniox Context](https://docs.soniox.com/stt/concepts/context)。

## 导入歌词

1. 点击主界面上方“歌词库…”，选择公演；没有对应公演时点“新增公演”。
2. 选中曲目；缺少曲目时点“新增曲目”，用“上移 / 下移”调整公演顺序。
3. 在“日文歌词”中粘贴歌词，或点“导入 TXT / LRC…”选择单曲文件。推荐 UTF-8，日文每行一句，空行可保留。
4. “中文译文（可选）”可以留空，交给 DeepSeek 翻译；自行填写时须与日文有效行数一致、逐行对应。
5. 点“保存歌词库”，关闭窗口后，在主界面“公演”下拉框选择该公演，再生成字幕。

已有本软件的 `lyrics-library.json` 时，在歌词库窗口点“打开库…”选择文件，再保存即可使用。可用“另存库…”备份到其他磁盘。TXT / LRC 用于单曲导入，JSON 用于打开整套歌词库；LRC 时间标签不替代本场音频的识别时间轴。

## 导入人名与昵称

内置成员的中日姓名、昵称和应援色可直接编辑。已有 `member-library.json` 时，进入“词库 → 打开库…”选择文件，检查后点“保存词库”。本名按中文译名输出，昵称保留日文；应援色在同页的“应援色”子标签中修改。

## 运行源码

需要 Windows 11、Python 3.12 x64。下载源码后，在项目目录执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe app.py
```

单独打开 MP3 配图工具：

```powershell
.\.venv\Scripts\python.exe app.py --image-video
```

## 打包

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

脚本会安装构建依赖、运行测试并生成 Windows 便携包，输出到 `dist/`。解压后运行 `AI-Sub-Maker.exe`，保留旁边的 `_internal` 文件夹；`Mp3ImageToMp4.exe` 是独立配图工具入口。

日语词性分析使用 [Janome](https://janome.mocobeta.dev/en/)，词典许可见 [JANOME-NOTICE.txt](JANOME-NOTICE.txt)。打包时自动收集其他依赖的许可声明。
