# -*- coding: utf-8 -*-
"""验证 band_hr.py 的核心逻辑: 心率解析 + GUI 状态更新(无界面模式)"""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtWidgets import QApplication, QMenu
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

# ---------- 4. 退出非阻塞 + 单实例去重 ----------
import time as _t
w2 = m.BleWorker()
w2.start()
while w2._loop is None:
    _t.sleep(0.01)
t0 = _t.time()
w2.shutdown()                # 默认不阻塞
dt = _t.time() - t0
assert dt < 2.0, f"shutdown 阻塞了 {dt:.2f}s"
w2.shutdown()                # 重复调用应被 guard 拦截
w2.wait(3000)
assert not w2.isRunning(), "工作线程应已退出"
print("[PASS] 退出非阻塞 + 重复关闭去重")

# ---------- 5. 单实例锁 ----------
from PySide6.QtCore import QLockFile
import tempfile as _tf
_lock_path = os.path.join(_tf.gettempdir(), "band_hr_singleton_test.lock")
l1 = QLockFile(_lock_path); l1.setStaleLockTime(0)
assert l1.tryLock(100), "第一个实例应获得锁"
l2 = QLockFile(_lock_path); l2.setStaleLockTime(0)
assert not l2.tryLock(100), "第二个实例不应获得锁"
l1.unlock()
l3 = QLockFile(_lock_path); l3.setStaleLockTime(0)
assert l3.tryLock(100), "释放后应可重新获得锁"
l3.unlock()
print("[PASS] 单实例锁")

# ---------- 6. 穿透状态 + 拖动位置记忆 ----------
win._drag_pos = None
win._applied_passthrough = None
win._apply_click_state()
assert win._applied_passthrough == win._passthrough, "未按 Alt 时实际穿透应等于配置"
win._drag_pos = object()          # 模拟正在拖动
win.mouseReleaseEvent(None)       # 结束拖动
win._drag_pos = None
_cfg = m.load_config()
assert _cfg.get("window_pos") and len(_cfg["window_pos"]) == 2, "拖动结束应保存位置"
# 清理测试写入的位置, 避免影响默认启动位置
_cfg.pop("window_pos", None)
m.save_config(_cfg)
print("[PASS] 穿透状态 + 拖动位置记忆")

# ---------- 7. 字号/颜色菜单互斥(打勾不重叠) ----------
for _sub in win._menu.findChildren(QMenu):
    if _sub.title() not in ("字号", "字体颜色"):
        continue
    _acts = _sub.actions()
    _grp = _acts[0].actionGroup()
    assert _grp is not None and _grp.isExclusive(), f"{_sub.title()} 应属于互斥组"
    _checked = [a for a in _acts if a.isChecked()]
    assert len(_checked) == 1, f"{_sub.title()} 初始应只有一个打勾, 实际 {len(_checked)}"
    # 切换一项后仍只有一个打勾
    for _a in _acts:
        if not _a.isChecked():
            _a.trigger()
            break
    _checked = [a for a in _acts if a.isChecked()]
    assert len(_checked) == 1, f"{_sub.title()} 切换后应仍只有一个打勾, 实际 {len(_checked)}"
print("[PASS] 字号/颜色菜单互斥")

# 延迟退出
QTimer.singleShot(200, app.quit)
app.exec()
print("[PASS] 全部验证通过")
