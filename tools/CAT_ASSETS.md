# 重建猫咪透明动画

以下依赖只用于离线重建素材，运行桌宠不需要安装它们。原 MP4 仅读取。

在项目根目录运行（建议单独的 Python 3.12 虚拟环境）：

```powershell
py -m pip install -r tools/requirements-media.txt
py tools/prepare_cat_assets.py --input-dir "C:/your/videos"
```

输入目录应包含 cat-idle-v1.mp4、cat-walk-v3.mp4、cat-groom-v1.mp4、cat-sleep-v3.mp4。
默认输出 assets/cat/；默认通过 imageio-ffmpeg 查找解码器。可用 --output-dir、--qa-dir、--ffmpeg 覆盖路径。--only idle|walk|groom|sleep 只重建指定动作。

输出为 320×300 的 RGBA PNG，24fps，统一0.32身体比例和地面y282。每个动作只使用固定位置变换，避免逐帧裁切引起大小跳动。白底识别结合背景连通区域、轮廓窄带透明度估计、去白污染和预乘透明度缩放；保留内部白胸和白爪。

| 动作 | 原始帧范围（从0计） | 输出帧 | 方式 |
| --- | --- | --- | --- |
| idle | 117–178 | 122 | 正反循环，避开原片尾尖截断 |
| walk | 155–179 | 25 | 稳定步态循环，原朝向左 |
| groom | 0–219 | 220 | 完整单次播放 |
| sleep | 107–178 | 72 | 呼吸循环 |

clips.json 记录源选段、帧率、循环、画布和地面。默认QA输出在 work/cat-preview/，含四种背景的接触表和10秒动画预览。原片没有起卧过渡，应用使用短淡化衔接。
