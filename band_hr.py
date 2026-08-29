# -*- coding: utf-8 -*-
"""
小米手环 10 Pro 心率广播接收器 —— 极简心率数字显示

接收标准 BLE 心率服务(0x180D / 特征值 0x2A37)的广播数据，
在"无背景、透明、置顶、最小化、点击穿透"的小窗口里实时显示单个心率数字。

用法:
    1. 手环上开启: 设置 -> 心率广播 -> 开启
    2. 运行本程序, 右键系统托盘红色❤图标 -> 连接设备 -> 选中你的手环
    3. 程序会记住设备并自动重连

交互:
    窗口默认点击穿透(不挡鼠标), 只显示数字
    托盘图标 = 连接设备 / 断开 / 字号 / 字体颜色 / 窗口位置 / 点击穿透开关 / 退出
    在托盘关闭"点击穿透"后, 可左键拖动窗口, 双击数字恢复穿透
    点击穿透开启时: 按住 Alt 拖动数字即可移动位置(自动记忆)

版本:
    v1.1.1  退出秒退、单实例保护
    v1.1.0  新增更多字号、字体颜色自定义、醒目未连接提示
"""

import asyncio
import ctypes
import json
import os
import sys
import tempfile
import time

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPoint, QLockFile
from PySide6.QtGui import (
    QFont, QAction, QActionGroup, QColor, QIcon, QPainter, QPixmap, QPainterPath, QCursor,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QLabel, QVBoxLayout, QMenu, QSystemTrayIcon,
    QDialog, QListWidget, QListWidgetItem, QPushButton,
    QHBoxLayout, QMessageBox,
)

from bleak import BleakClient, BleakScanner

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

# Windows 点击穿透所需
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_FRAMECHANGED = 0x0020

try:
    _GetWindowLongPtr = ctypes.windll.user32.GetWindowLongPtrW
    _SetWindowLongPtr = ctypes.windll.user32.SetWindowLongPtrW
except AttributeError:
    _GetWindowLongPtr = ctypes.windll.user32.GetWindowLongW
    _SetWindowLongPtr = ctypes.windll.user32.SetWindowLongW
_SetWindowPos = ctypes.windll.user32.SetWindowPos


# ---------------------------------------------------------------- 版本与外观配置

APP_VERSION = "1.1.1"

FONT_SIZES = (32, 40, 48, 56, 64, 72, 84, 96)   # 可选字号(像素)
DEFAULT_FONT_SIZE = 56

# 字体颜色选项: "auto" 表示按心率自动分级着色
FONT_COLORS = {
    "auto": "自动(按心率分级)",
    "#ff5555": "红色",
    "#5aff9e": "绿色",
    "#4da6ff": "蓝色",
    "#ffaa44": "橙色",
    "#ffffff": "白色",
    "#ffd93b": "黄色",
    "#3be8ff": "青色",
    "#ff6fa5": "粉色",
    "#c58aff": "紫色",
}

HINT_TEXT = "未连接，右键托盘小图标进行连接"
HINT_COLOR = "#ff3b3b"   # 醒目红, 用于未连接提示


# ---------------------------------------------------------------- 工具函数

def get_app_dir():
    """EXE 运行时取 EXE 所在目录, 脚本运行取脚本目录。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


CONFIG_PATH = os.path.join(get_app_dir(), "band_hr_config.json")


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        alt = os.path.join(os.path.expanduser("~"), "band_hr_config.json")
        try:
            with open(alt, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def parse_heart_rate(data: bytes):
    """解析标准 BLE 心率测量值(0x2A37)。返回 bpm(int) 或 None。

    第一个字节为 flags: bit0 = 0(8位) / 1(16位) 心率值格式。
    """
    if not data:
        return None
    flags = data[0]
    if flags & 0x01:  # 16 位心率值
        if len(data) < 3:
            return None
        return (data[2] << 8) | data[1]
    else:             # 8 位心率值
        if len(data) < 2:
            return None
        return data[1]


def hr_color(v: int) -> str:
    """按心率高低给数字上色: 正常绿 / 偏快橙 / 过快红 / 过低蓝。"""
    if v < 40:
        return "#4da6ff"
    if v >= 170:
        return "#ff5555"
    if v >= 140:
        return "#ffaa44"
    return "#5aff9e"


def _is_hr_uuid(u) -> bool:
    """判断 UUID 是否为心率服务 0x180D(兼容 16 位短形式与完整 128 位形式)。"""
    u2 = str(u).lower().replace("-", "").strip()
    return (
        u2 == "180d"
        or u2 == "0000180d00001000800000805f9b34fb"
        or u2.endswith("180d00001000800000805f9b34fb")
    )


def is_target_device(name, adv) -> bool:
    """判断 BLE 广播是否属于小米手环 / 标准心率服务(0x180D)。"""
    low = (name or "").lower()
    is_band = any(k in low for k in ("xiaomi", "mi band", "smart band", "band", "手环"))
    uuids = list(getattr(adv, "service_uuids", None) or [])
    uuids += list(getattr(adv, "service_data", None) or {}.keys())
    is_hr = any(_is_hr_uuid(u) for u in uuids)
    return is_band or is_hr


def set_window_click_through(hwnd: int, enabled: bool):
    """设置 Windows 窗口是否点击穿透(WS_EX_TRANSPARENT)。"""
    try:
        style = _GetWindowLongPtr(hwnd, GWL_EXSTYLE)
        if enabled:
            style |= WS_EX_LAYERED | WS_EX_TRANSPARENT
        else:
            style &= ~WS_EX_TRANSPARENT
        _SetWindowLongPtr(hwnd, GWL_EXSTYLE, style)
        _SetWindowPos(hwnd, 0, 0, 0, 0, 0,
                      SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED)
    except Exception:
        pass


# ---------------------------------------------------------------- BLE 工作线程

class BleWorker(QThread):
    """在独立线程中运行 asyncio 事件循环, 负责扫描 / 连接 / 订阅心率广播。"""

    hr = Signal(int)        # 实时心率
    state = Signal(str)     # idle / scanning / connecting / connected / disconnected
    devices = Signal(list)  # [ {name,address,rssi}, ... ]
    notify = Signal(str)    # 一次性提示/错误消息

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loop = None
        self._client = None
        self._connect_gen = 0   # 连接代次, 用于取消旧的自动重连链
        self._exit = False
        self._shutdown_started = False   # 防重复关闭

    # ---- 线程入口 ----
    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    # ---- 供 GUI 线程调用的接口 ----
    def submit(self, coro):
        if self._loop is None:
            return
        asyncio.run_coroutine_threadsafe(coro, self._loop)

    def start_scan(self, duration=6.0):
        self.submit(self._scan(duration))

    def connect(self, mac, name=""):
        self._connect_gen += 1
        gen = self._connect_gen
        self.submit(self._connect(mac, name, gen))

    def disconnect_manual(self):
        self._connect_gen += 1   # 取消自动重连链
        self.submit(self._disconnect())

    def shutdown(self, wait=False):
        """通知线程退出。默认不阻塞 GUI: 由后台任务快速断开并结束事件循环。"""
        self._exit = True
        if self._shutdown_started:
            return
        self._shutdown_started = True
        try:
            fut = asyncio.run_coroutine_threadsafe(self._close(), self._loop)
            if wait:
                try:
                    fut.result(timeout=5)
                except Exception:
                    pass
        except Exception:
            pass

    # ---- 内部协程 ----
    async def _scan(self, duration):
        self.state.emit("scanning")
        found = {}

        def cb(device, adv):
            if not is_target_device(device.name, adv):
                return
            found[device.address] = {
                "name": device.name or "(未命名设备)",
                "address": device.address,
                "rssi": getattr(adv, "rssi", None),
            }

        try:
            scanner = BleakScanner(detection_callback=cb)
            async with scanner:
                await asyncio.sleep(duration)
        except Exception as e:
            self.notify.emit(f"蓝牙扫描失败: {e}\n请确认电脑蓝牙已开启。")
            self.state.emit("idle")
            return

        devs = sorted(
            found.values(),
            key=lambda d: -(d["rssi"] if d["rssi"] is not None else -100),
        )
        self.devices.emit(devs)
        self.state.emit("idle")

    async def _disconnect(self):
        if self._client:
            try:
                await self._client.disconnect()
            except Exception:
                pass
            self._client = None
        self.state.emit("disconnected")

    async def _close(self):
        if self._client:
            try:
                await asyncio.wait_for(self._client.disconnect(), timeout=1.0)
            except Exception:
                pass
        try:
            self._loop.stop()
        except Exception:
            pass

    async def _connect(self, mac, name, gen, retry_delay=5.0):
        """连接并订阅心率; 断开后自动重连(同一代次内)。"""
        if self._exit or gen != self._connect_gen:
            return
        self.state.emit("connecting")
        client = None
        try:
            client = BleakClient(mac)
            await client.connect(timeout=15.0)
        except Exception as e:
            self.state.emit("disconnected")
            if gen == self._connect_gen and not self._exit:
                await asyncio.sleep(retry_delay)
                asyncio.ensure_future(self._connect(mac, name, gen, retry_delay))
            return

        self._client = client
        try:
            hr_char = None
            for service in client.services:
                if service.uuid.lower() == HEART_RATE_SERVICE_UUID:
                    for ch in service.characteristics:
                        if ch.uuid.lower() == HEART_RATE_MEASUREMENT_UUID:
                            hr_char = ch
                            break
                if hr_char:
                    break
            if hr_char is None:  # 兜底: 全服务内按特征值 UUID 找
                for service in client.services:
                    for ch in service.characteristics:
                        if ch.uuid.lower() == HEART_RATE_MEASUREMENT_UUID:
                            hr_char = ch
                            break
                    if hr_char:
                        break
            if hr_char is None:
                self.notify.emit("未在手环上找到心率服务(0x180D / 0x2A37)。\n请确认已开启“心率广播”。")
                await client.disconnect()
                self.state.emit("disconnected")
                return

            def on_hr(_ch, data: bytearray):
                v = parse_heart_rate(bytes(data))
                if v is not None:
                    self.hr.emit(v)

            await client.start_notify(hr_char, on_hr)
            self.state.emit("connected")

            # 保持连接, 直到断开或退出
            while client.is_connected and not self._exit:
                await asyncio.sleep(1.0)

            await client.disconnect()
        except Exception as e:
            self.notify.emit(f"订阅心率失败: {e}")
            try:
                await client.disconnect()
            except Exception:
                pass
        finally:
            self._client = None

        if gen == self._connect_gen and not self._exit:
            self.state.emit("disconnected")
            await asyncio.sleep(retry_delay)
            asyncio.ensure_future(self._connect(mac, name, gen, retry_delay))


# ---------------------------------------------------------------- 主窗口

class HRWindow(QWidget):
    """无背景、透明、置顶、贴边的最小心率数字窗口。"""

    def __init__(self, worker: BleWorker):
        super().__init__()
        self.worker = worker
        self._drag_pos = None
        self._current_hr = None
        self._font_size = int(load_config().get("font_size", DEFAULT_FONT_SIZE))
        self._font_size = self._font_size if self._font_size in FONT_SIZES else DEFAULT_FONT_SIZE
        self._font_color = load_config().get("font_color", "auto")
        if self._font_color not in FONT_COLORS:
            self._font_color = "auto"

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)

        # 主数字: 透明到鼠标事件, 让右键/拖动都落在窗口上
        self.label = QLabel("--", self)
        self.label.setAttribute(Qt.WA_TransparentForMouseEvents)
        self._apply_font()
        self.label.setAlignment(Qt.AlignCenter)

        # 未连接时的小提示(连接后隐藏)
        self.hint = QLabel(HINT_TEXT, self)
        self.hint.setAttribute(Qt.WA_TransparentForMouseEvents)
        _hint_font = QFont("Microsoft YaHei", 11)
        _hint_font.setBold(True)
        self.hint.setFont(_hint_font)
        self.hint.setStyleSheet(f"color: {HINT_COLOR}; background: transparent;")
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.hide()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.addWidget(self.label)
        lay.addWidget(self.hint)

        self._set_text("--", "#7a7a7a")
        self._show_hint(True)  # 默认未连接, 显示提示
        self.adjustSize()

        # 点击穿透(默认开启)与托盘
        self._passthrough = bool(load_config().get("passthrough", True))
        self._applied_passthrough = self._passthrough
        # 按住 Alt 临时取消穿透, 便于直接拖动数字
        self._move_timer = QTimer(self)
        self._move_timer.setInterval(100)
        self._move_timer.timeout.connect(self._apply_click_state)
        self._move_timer.start()
        self._use_tray = False
        self._menu = self._build_menu()
        self._tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self._tray = QSystemTrayIcon(self._make_tray_icon(), self)
            self._tray.setToolTip(f"心率显示 v{APP_VERSION}")
            self._tray.setContextMenu(self._menu)
            self._tray.activated.connect(self._on_tray_activated)
            self._tray.show()
            self._use_tray = True

        # 信号连接
        worker.hr.connect(self._on_hr)
        worker.state.connect(self._on_state)
        worker.devices.connect(self._on_devices)
        worker.notify.connect(self._on_notify)

    # ---- 显示 ----
    def _apply_font(self):
        self.label.setFont(QFont("Consolas", self._font_size, QFont.Bold))
        self.adjustSize()

    def _set_font_size(self, size):
        self._font_size = size
        self._apply_font()
        cfg = load_config()
        cfg["font_size"] = size
        save_config(cfg)

    def _set_font_color(self, color):
        self._font_color = color
        cfg = load_config()
        cfg["font_color"] = color
        save_config(cfg)
        if self._current_hr is not None:
            self._set_text(str(self._current_hr), self._hr_display_color(self._current_hr))

    def _hr_display_color(self, v):
        """当前字体颜色设置下, 心率数字应显示的颜色。"""
        if self._font_color == "auto":
            return hr_color(v)
        return self._font_color

    def _set_text(self, text, color):
        self.label.setText(text)
        self.label.setStyleSheet(f"color: {color}; background: transparent;")
        self.adjustSize()

    def _show_hint(self, show: bool):
        self.hint.setVisible(show)
        self.adjustSize()

    def _on_hr(self, v):
        self._current_hr = v
        self._show_hint(False)
        self._set_text(str(v), self._hr_display_color(v))
        if self._tray is not None:
            self._tray.setToolTip(f"心率: {v} bpm")

    def _on_state(self, st):
        if st == "connecting":
            self._show_hint(False)
            self._set_text("…", "#88ccff")
        elif st == "connected":
            if self._current_hr is None:
                self._show_hint(False)
                self._set_text("…", "#88ccff")
        elif st == "scanning":
            self._show_hint(False)
            self._set_text("…", "#88ccff")
        elif st in ("idle", "disconnected"):
            self._current_hr = None
            self._show_hint(True)
            self._set_text("--", "#7a7a7a")
            if self._tray is not None:
                self._tray.setToolTip(f"心率显示 v{APP_VERSION} (未连接)")

    def _on_devices(self, devs):
        # 扫描结果由设备选择对话框消费; 这里仅作兜底占位
        pass

    def _on_notify(self, msg):
        QMessageBox.information(self, "心率显示", msg)

    # ---- 拖动(点击穿透时按住 Alt, 或关闭穿透时直接拖动) ----
    def _apply_click_state(self):
        """按"配置穿透 && 是否按住 Alt"计算并应用实际穿透状态。"""
        if self._drag_pos is not None:   # 拖动中不切换, 避免丢鼠标
            return
        alt = bool(ctypes.windll.user32.GetAsyncKeyState(0x12) & 0x8000)
        want = self._passthrough and not alt
        if want != self._applied_passthrough:
            self._applied_passthrough = want
            set_window_click_through(int(self.winId()), want)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and not self._applied_passthrough:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag_pos is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e):
        if self._drag_pos is not None:
            # 拖动结束后记忆位置
            cfg = load_config()
            cfg['window_pos'] = [self.x(), self.y()]
            save_config(cfg)
        self._drag_pos = None

    def mouseDoubleClickEvent(self, e):
        # 关闭穿透时可双击数字快速恢复穿透
        if e.button() == Qt.LeftButton and not self._applied_passthrough:
            self._set_passthrough(True)

    def showEvent(self, event):
        super().showEvent(event)
        # 每次显示都按当前状态应用穿透(窗口句柄可能被 Qt 重建)
        QTimer.singleShot(0, lambda: self._apply_click_state())

    # ---- 右键菜单(仅当系统托盘不可用时, 作为窗口兜底入口) ----
    def contextMenuEvent(self, event):
        if not self._use_tray:
            self._menu.exec(event.globalPos())

    # ---- 托盘 ----
    def _make_tray_icon(self):
        pm = QPixmap(32, 32)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#ff3b5c"))
        path = QPainterPath()
        path.moveTo(16, 27)
        path.cubicTo(5, 19, 2, 8, 10, 4)
        path.cubicTo(14, 3, 17, 6, 20, 9)
        path.cubicTo(23, 6, 26, 3, 30, 4)
        path.cubicTo(34, 8, 31, 19, 16, 27)
        p.drawPath(path)
        p.end()
        return QIcon(pm)

    def _build_menu(self):
        menu = QMenu(self)
        act_connect = QAction("连接设备…", self)
        act_connect.triggered.connect(self._open_device_dialog)
        menu.addAction(act_connect)

        act_disconnect = QAction("断开连接", self)
        act_disconnect.triggered.connect(self.worker.disconnect_manual)
        menu.addAction(act_disconnect)

        menu.addSeparator()

        size_menu = menu.addMenu("字号")
        size_group = QActionGroup(self)
        size_group.setExclusive(True)   # 同一时刻只允许一个字号打勾
        for s in FONT_SIZES:
            act = QAction(f"{s} px", self)
            act.setCheckable(True)
            act.setChecked(self._font_size == s)
            act.triggered.connect(lambda _c, ss=s: self._set_font_size(ss))
            size_group.addAction(act)
            size_menu.addAction(act)

        color_menu = menu.addMenu("字体颜色")
        color_group = QActionGroup(self)
        color_group.setExclusive(True)  # 同一时刻只允许一个颜色打勾
        for c, name in FONT_COLORS.items():
            act = QAction(name, self)
            act.setCheckable(True)
            act.setChecked(self._font_color == c)
            act.triggered.connect(lambda _c, cc=c: self._set_font_color(cc))
            color_group.addAction(act)
            color_menu.addAction(act)

        pos_menu = menu.addMenu("窗口位置")
        for name, corner in (("右上角", "tr"), ("左上角", "tl"), ("右下角", "br"), ("左下角", "bl")):
            act = QAction(name, self)
            act.triggered.connect(lambda _c, c=corner: self._move_to_corner(c))
            pos_menu.addAction(act)

        act_pt = QAction("点击穿透", self)
        act_pt.setCheckable(True)
        act_pt.setChecked(self._passthrough)
        act_pt.triggered.connect(lambda _c: self._set_passthrough(not self._passthrough))
        menu.addAction(act_pt)

        menu.addSeparator()

        act_exit = QAction("退出", self)
        act_exit.triggered.connect(self._quit)
        menu.addAction(act_exit)
        return menu

    def _on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:  # 左键点击托盘也弹出菜单
            self._menu.popup(QCursor.pos())

    def _set_passthrough(self, enabled):
        self._passthrough = enabled
        self._apply_click_state()
        cfg = load_config()
        cfg["passthrough"] = enabled
        save_config(cfg)
        # 同步托盘菜单勾选状态
        for act in self._menu.actions():
            if act.text() == "点击穿透":
                act.setChecked(enabled)
                break

    def _move_to_corner(self, corner):
        screen = QApplication.primaryScreen().availableGeometry()
        x, y = screen.left(), screen.top()
        if corner in ("tr", "br"):
            x = screen.right() - self.width() - 8
        if corner in ("bl", "br"):
            y = screen.bottom() - self.height() - 8
        self.move(x, y)

    def _open_device_dialog(self):
        dlg = DeviceDialog(self.worker, self)
        dlg.exec()

    def _quit(self):
        self.worker.shutdown()
        QApplication.instance().quit()

    # ---- 首次运行引导 ----
    def show_first_run_hint(self):
        QMessageBox.information(
            self,
            "心率显示",
            "首次使用:\n\n"
            "1. 在手环上开启: 设置 → 心率广播 → 开启\n"
            "2. 右键屏幕右下角托盘的红色❤图标 → “连接设备”\n"
            "3. 在列表中选择你的手环\n\n"
            "窗口默认点击穿透(不挡鼠标)。需要移动时:\n"
            "按住 Alt 键拖动数字, 即可移动到任意位置(松开后位置会被记住)。",
        )


# ---------------------------------------------------------------- 设备选择对话框

class DeviceDialog(QDialog):
    def __init__(self, worker: BleWorker, parent=None):
        super().__init__(parent)
        self.worker = worker
        self.setWindowTitle("选择心率设备")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.resize(440, 320)
        self._dev_conn = None

        lay = QVBoxLayout(self)

        self.status_label = QLabel("正在扫描附近的心率设备…")
        lay.addWidget(self.status_label)

        self.listw = QListWidget()
        lay.addWidget(self.listw)

        btns = QHBoxLayout()
        self.btn_scan = QPushButton("重新扫描")
        self.btn_conn = QPushButton("连接")
        self.btn_cancel = QPushButton("取消")
        btns.addWidget(self.btn_scan)
        btns.addStretch(1)
        btns.addWidget(self.btn_conn)
        btns.addWidget(self.btn_cancel)
        lay.addLayout(btns)

        self.btn_scan.clicked.connect(self._start_scan)
        self.btn_conn.clicked.connect(self._connect)
        self.btn_cancel.clicked.connect(self.reject)
        self.listw.itemDoubleClicked.connect(lambda _it: self._connect())

        self._dev_conn = worker.devices.connect(self._on_devices)
        self._start_scan()

    def _start_scan(self):
        self.listw.clear()
        self.status_label.setText("正在扫描附近的心率设备…")
        self.btn_conn.setEnabled(False)
        self.worker.start_scan(6.0)

    def _on_devices(self, devs):
        if not devs:
            self.status_label.setText("未发现设备。请确认手环已开启“心率广播”, 且电脑蓝牙已开启。")
            return
        self.status_label.setText(f"发现 {len(devs)} 个设备:")
        for d in devs:
            item = QListWidgetItem(f"{d['name']}   ({d['address']})")
            item.setData(Qt.UserRole, d)
            self.listw.addItem(item)
        self.listw.setCurrentRow(0)
        self.btn_conn.setEnabled(True)

    def _connect(self):
        item = self.listw.currentItem()
        if item is None:
            return
        d = item.data(Qt.UserRole)
        save_config({"device_mac": d["address"], "device_name": d["name"]})
        self.worker.connect(d["address"], d["name"])
        self.accept()

    def closeEvent(self, event):
        if self._dev_conn is not None:
            try:
                self.worker.devices.disconnect(self._dev_conn)
            except Exception:
                pass
        super().closeEvent(event)


# ---------------------------------------------------------------- 入口

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("心率显示")
    app.setQuitOnLastWindowClosed(False)  # 托盘常驻, 不因窗口关闭退出

    # 单实例保护: 重复启动时提示并退出
    _lock = QLockFile(os.path.join(tempfile.gettempdir(), "band_hr_singleton.lock"))
    _lock.setStaleLockTime(0)   # 进程已退出则立即回收锁
    if not _lock.tryLock(100):
        QMessageBox.warning(None, "心率显示",
                            "程序已在运行（查看右下角托盘图标）。\n如需重启，请先在托盘菜单选择“退出”。")
        return

    worker = BleWorker()
    worker.start()
    # 等待事件循环就绪
    while worker._loop is None:
        time.sleep(0.01)

    win = HRWindow(worker)
    win.show()

    # 记忆上次位置, 否则默认摆到屏幕右上角
    _cfg0 = load_config()
    _pos = _cfg0.get("window_pos")
    if isinstance(_pos, (list, tuple)) and len(_pos) == 2:
        win.move(int(_pos[0]), int(_pos[1]))
    else:
        screen = app.primaryScreen().availableGeometry()
        win.move(screen.right() - win.width() - 48, screen.top() + 48)

    cfg = load_config()
    saved_mac = cfg.get("device_mac")
    if saved_mac:
        worker.connect(saved_mac, cfg.get("device_name", ""))
    else:
        QTimer.singleShot(800, win.show_first_run_hint)

    def on_quit():
        worker.shutdown()

    app.aboutToQuit.connect(on_quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
