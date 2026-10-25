# OpenCV FFmpeg Video I/O plugin

This directory contains only the x64 FFmpeg Video I/O plugin needed to decode
the pet's MP4 animation files. The application loads the plugin directly; it
does not bundle or import the OpenCV Python bindings or NumPy at runtime.

- Package source: `opencv-python-headless` 5.0.0.93
- Project: https://github.com/opencv/opencv-python
- Distribution: Windows x64 `cp37-abi3` wheel from PyPI
- File: `opencv_videoio_ffmpeg500_64.dll`
- Size: 30,876,160 bytes
- SHA-256: `7aabff029d1fa47cf56ede4dbbe05dc62da3208f07cd16471d4c966a33f9aa31`
- Purpose: MP4/H.264 frame decoding for the bundled color-and-alpha videos

`LICENSE.txt` is the unmodified license shipped by the Python wrapper package.
`LICENSE-3RD-PARTY.txt` is its unmodified OpenCV and bundled dependency notice.
Both files came from the same installed wheel as the DLL.
