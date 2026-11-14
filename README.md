# AI-Sub-Maker

版本：1.0.0

Windows 11 日语字幕工具：用 **Soniox** 识别音视频并获取时间轴，用 **DeepSeek** 整批校对、翻译，生成上日语、下简体中文的 ASS 字幕。

Soniox 和 DeepSeek 需自行提供 API Key，并按各自服务收费。无需下载语音模型。

## 功能

- 支持 MP4、MP3 等音视频；按标点和说话人断句，保留原生词级时间戳。
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

完整歌词需自行导入。个人词库、歌词库和 Key 保存在本机，不随源码分发；记住 Key 时使用 Windows 加密。

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
