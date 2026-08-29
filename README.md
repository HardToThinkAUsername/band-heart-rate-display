# 通用 BLE 心率广播显示器 (Band Heart Rate Display)

一个 Windows 桌面小程序：接收支持标准 BLE 心率广播的设备（如小米手环 10 Pro），在屏幕上用一个**无背景、透明、置顶、点击穿透**的最小数位实时显示心率。

## 特性

- 接收标准 BLE 心率服务（0x180D / 0x2A37）广播，兼容小米手环 10 Pro 及所有支持标准心率广播的 BLE 设备（手环 / 心率带等）
- 只显示一个数字，无任何背景，窗口缩到最小
- 默认点击穿透，不遮挡鼠标操作；按住 Alt 拖动数字即可移动，位置自动记忆
- 系统托盘红色 ❤ 图标：连接设备 / 断开 / 字号(8 档) / 字体颜色 / 窗口位置 / 退出
- 记住上次连接设备，断开自动重连
- 心率颜色分级：绿=正常 / 橙=偏快 / 红=过快 / 蓝=过低
- 字体颜色可自定义：自动按心率分级，或固定红 / 绿 / 蓝 / 橙 / 白 / 黄 / 青 / 粉 / 紫
- 8 档字号可选（32 / 40 / 48 / 56 / 64 / 72 / 84 / 96 像素）
- 单实例保护：重复启动自动提示，不会多开抢占蓝牙

## 使用

### 1. 手环端

在手环上开启：**设置 → 心率广播 → 开启**

### 2. 运行

下载 `BandHeartRateDisplay-v1.1.1.exe`（见 [Releases](https://github.com/HardToThinkAUsername/band-heart-rate-display/releases)），双击运行，右键系统托盘 ❤ 图标 → 连接设备 → 选择你的手环。

详细说明见 [使用说明.md](使用说明.md)。

### 3. FAQ：搜索不到设备

1. 请确保心率监测设备已开启"广播"（如小米手环：设置 → 心率广播 → 开启）
2. 请尝试断开电脑蓝牙、重新打开，再重新连接设备

## 从源码运行 / 构建

```bash
# 环境: Python 3.10+
uv venv .venv
uv pip install --python .venv -r requirements.txt

.venv\Scripts\python band_hr.py          # 运行
.venv\Scripts\python test_band_hr.py     # 运行测试

# 打包单文件 EXE
.venv\Scripts\pyinstaller --noconfirm --onefile --windowed --name "心率显示" band_hr.py
```

## 技术栈

- Python 3.13 + [bleak](https://github.com/hbldh/bleak)（Windows BLE）
- [PySide6](https://doc.qt.io/qtforpython/)（透明无边框置顶窗口 + 系统托盘）
- [PyInstaller](https://pyinstaller.org/)（打包单文件 EXE）

## License

[MIT](LICENSE)
