# 重建猫咪透明 MP4

当前桌宠运行时直接读取 MP4，不使用 PNG 序列。源视频仍是白色影棚背景，因此透明度在开发阶段离线生成；用户电脑只负责解码已经打包好的颜色和透明度。

输入目录需要包含：

- `cat-idle-final.mp4`
- `cat-click-final.mp4`
- `cat-eat-final.mp4`
- `cat-groom-final.mp4`
- `cat-sleep-final.mp4`

五段源视频均应为 1088×816、24 fps。转换脚本会忽略并移除音轨。

离线工具依赖 OpenCV、NumPy、SciPy、Pillow 和 FFmpeg。这些依赖不进入发布程序。示例：

```powershell
py tools/prepare_video_assets.py `
  --input-dir "C:/your/cat-videos" `
  --output-dir "C:/temporary/cat-video-build" `
  --ffmpeg "C:/path/to/ffmpeg.exe" `
  --workers 3 `
  --qa-output "C:/temporary/cat-video-build/qa-contact-sheet.jpg"
```

先在临时目录检查 `qa-contact-sheet.jpg`，确认深色和浅色背景下没有白色矩形、猫粮完整、尺寸和落点一致，再把五个 MP4 与 `clips.json` 放入 `assets/cat/`。

输出视频为 640×300、24 fps、H.264：左侧 320×300 保存去白边后的颜色，右侧 320×300 保存灰度透明度。应用通过 `pet_video.py` 直接调用随包的 OpenCV FFmpeg VideoIO DLL，合成为 PIL RGBA 后继续使用原有的 `UpdateLayeredWindow` 透明窗口。发布包不需要 `cv2` 或 NumPy。

v6.3 在源画面第 720–765 行之间逐渐加强对浅灰中性地面的识别，去除爪旁残留的白色尖角，避免固定水平分界产生台阶。脸、胸口和上半身仍使用原有阈值；不对整只猫统一收缩透明轮廓。猫粮等有色道具继续保留。`clips.json` 的 `matte_revision` 记录这版素材处理规则。

离线抠像回归检查（需上述媒体工具依赖）：

```powershell
py tools/test_video_matting.py -v
```

检查包括：与猫咪相连的浅灰地面变透明，旧水平分界上方的爪边灰底变透明，白胸与封闭的白爪保留，有色猫粮保留，完全透明区域无残留白色 RGB。

动作时间如下：

| 动作 | 帧数 | 播放方式 |
| --- | ---: | --- |
| idle | 240 | 整段循环，4 帧接缝混合 |
| click | 96 | 完整单次播放后回待机 |
| eat | 240 | 完整单次播放后回待机 |
| groom | 240 | 完整单次播放后回待机 |
| sleep | 240 | 入睡 `[0,144)`，呼吸循环 `[144,168)`，醒来 `[168,240)` |

`assets/vendor/opencv-videoio/` 保存固定的 x64 解码 DLL、来源、SHA-256 和原始许可证。`tools/build_windows.py` 会在打包前校验它。`prepare_cat_assets.py` 与 `import_cat_package.py` 只用于历史 PNG 素材，不参与当前构建。
