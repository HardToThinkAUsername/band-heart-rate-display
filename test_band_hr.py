# -*- coding: utf-8 -*-
"""验证 band_hr.py 的核心逻辑: 心率解析 + GUI 状态更新(无界面模式)"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer, QObject

import band_hr as m

# ---------- 1. 心率解析 ----------
assert m.parse_heart_rate(bytes([0x00, 0x4E])) == 78, "8bit HR"
assert m.parse_heart_rate(bytes([0x01, 0x34, 0x01])) == 0x0134, "16bit HR"
assert m.parse_heart_rate(bytes([0x00, 0x4E, 0x00, 0x42, 0x57])) == 78, "带RR间隔"
assert m.parse_heart_rate(bytes([0x00])) is None, "数据不足"
assert m.parse_heart_rate(b"") is None, "空数据"
print("[PASS] parse_heart_rate")

# ---------- 2. 颜色 ----------
assert m.hr_color(80) == "#5aff9e"
assert m.hr_color(150) == "#ffaa44"
assert m.hr_color(180) == "#ff5555"
assert m.hr_color(35) == "#4da6ff"
print("[PASS] hr_color")

# ---------- 2.5 设备匹配逻辑 ----------
from types import SimpleNamespace as NS

def adv(service_uuids=None, service_data=None):
    return NS(service_uuids=service_uuids or [], service_data=service_data or {})

# 名字匹配(手环广播时名为 Xiaomi Smart Band 10 XXXX)
assert m.is_target_device("Xiaomi Smart Band 10 Pro", adv()) is True
assert m.is_target_device("MI BAND 9", adv()) is True
# 心率服务匹配
assert m.is_target_device("HeartRate1", adv(service_uuids=["0000180d-0000-1000-8000-00805f9b34fb"])) is True
# 心率服务在 service_data 里
assert m.is_target_device("Unnamed", adv(service_data={"0000180d-0000-1000-8000-00805f9b34fb": b"\x01"})) is True
# 无关设备
assert m.is_target_device("ABC-Mouse", adv()) is False
assert m.is_target_device(None, adv()) is False
print("[PASS] is_target_device")

# ---------- 3. GUI 状态更新 ----------
app = QApplication.instance() or QApplication([])

class FakeWorker(QObject):
    hr = m.Signal(int)
    state = m.Signal(str)
    devices = m.Signal(list)
    notify = m.Signal(str)

    def disconnect_manual(self):
        pass

worker = FakeWorker()
win = m.HRWindow(worker)
win.show()

# 未连接 -> "--" + 提示可见
assert win.label.text() == "--", win.label.text()
assert win.hint.isVisible(), "未连接时应显示提示"
assert win.hint.text() == m.HINT_TEXT
# 默认点击穿透开启
assert win._passthrough is True
# 关闭穿透 -> 状态切换
win._set_passthrough(False)
assert win._passthrough is False
# 重新开启
win._set_passthrough(True)
assert win._passthrough is True
# 连接中 -> "…" 提示隐藏
worker.state.emit("connecting")
assert win.label.text() == "…", win.label.text()
assert not win.hint.isVisible(), "连接中应隐藏提示"
# 收到心率 -> 数字 + 颜色
worker.hr.emit(82)
assert win.label.text() == "82", win.label.text()
assert "5aff9e" in win.label.styleSheet(), win.label.styleSheet()
assert not win.hint.isVisible()
# 高心率 -> 红色
worker.hr.emit(172)
assert win.label.text() == "172"
assert "ff5555" in win.label.styleSheet()
# 断开 -> 灰 "--" + 提示恢复
worker.state.emit("disconnected")
assert win.label.text() == "--", win.label.text()
assert win.hint.isVisible(), "断开后应恢复提示"

# 字体颜色: 默认自动分级
assert win._font_color == "auto"
assert win._hr_display_color(82) == "#5aff9e", win._hr_display_color(82)
assert win._hr_display_color(172) == "#ff5555"
# 固定颜色
win._set_font_color("#4da6ff")
assert win._font_color == "#4da6ff"
assert win._hr_display_color(82) == "#4da6ff"
worker.hr.emit(100)
assert win.label.text() == "100"
assert "4da6ff" in win.label.styleSheet()
# 恢复自动
win._set_font_color("auto")
assert win._hr_display_color(100) == "#5aff9e"
# 提示词为醒目色(非灰)
assert m.HINT_COLOR != "#9a9a9a"

# 窗口尺寸贴内容
w = win.label.sizeHint().width()
assert win.width() >= w, (win.width(), w)
print(f"[PASS] GUI 状态更新, 窗口尺寸 {win.width()}x{win.height()}")

# 延迟退出
QTimer.singleShot(200, app.quit)
app.exec()
print("[PASS] 全部验证通过")
