"""GUI 入口：PyQt6 桌面界面

运行：python gui.py
功能：实时牌价表、波动显示、阈值编辑、动态增删币种、一键演示急跌、日志面板。
"""
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QColor, QBrush
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QTableWidget, QTableWidgetItem, QPlainTextEdit, QHeaderView,
    QMessageBox, QInputDialog, QSplitter, QComboBox, QToolButton, QDoubleSpinBox,
)
import pyqtgraph as pg

pg.setConfigOptions(background="w", foreground="k", antialias=True)

from fx_agent import fetcher, storage, analyzer, agent, alerter, simulate

CFG_PATH = Path(__file__).resolve().parent / "config.json"
COLS = ["币种", "代码", "现价", "日内基准", "波动", "阈值%", "状态"]
TH_COL = COLS.index("阈值%")  # 阈值列下标（表头步进按钮与行内步进器都用）


def load_cfg() -> dict:
    return json.loads(CFG_PATH.read_text(encoding="utf-8"))


def save_cfg(cfg: dict) -> None:
    CFG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


class Worker(QObject):
    """一轮完整工作流（感知-思考-行动），跑在后台线程，信号回传 UI。"""
    sig_log = pyqtSignal(str)
    sig_result = pyqtSignal(list, list, int)   # analyses, decisions, sent
    sig_done = pyqtSignal()

    def __init__(self, cfg: dict, monitor: list[str], fetch: bool = True):
        super().__init__()
        self.cfg, self.monitor, self.fetch = cfg, monitor, fetch

    def run(self):
        try:
            if self.fetch:
                records = fetcher.fetch_boc()
                storage.save_rates(records)
                self.sig_log.emit(f"[感知] 抓到 {len(records)} 个币种牌价，已落库")
            else:
                self.sig_log.emit("[感知] 演示模式：使用注入行情")

            thresholds = self.cfg["thresholds_pct"]
            src = "boc" if self.fetch else None
            analyses = []
            for cur in self.monitor:
                th = thresholds.get(cur, thresholds.get("默认", 0.1))
                a = analyzer.analyze(cur, th, source=src)
                if a:
                    analyses.append(a)
                else:
                    self.sig_log.emit(f"[分析] {cur}: 当日首批数据，暂无基准")

            decisions = agent.decide(analyses, self.cfg) if analyses else []
            if not self.fetch:  # 演示模式：打模拟标记，不进真实预警记录
                for d in decisions:
                    d["simulated"] = True
            sent = alerter.dispatch(decisions, self.cfg, storage) if decisions else 0
            self.sig_result.emit(analyses, decisions, sent)
        except Exception as e:
            self.sig_log.emit(f"[错误] 本轮失败：{e}")
        finally:
            self.sig_done.emit()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.cfg = load_cfg()
        self._busy = False
        self._loading = False
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.on_timer)

        self.setWindowTitle("汇率监测预警智能体 · 百景大赛")
        self.resize(880, 620)
        # 显式浅色主题：不跟随系统深色模式，保证文字永远可读
        self.setStyleSheet("""
            QMainWindow, QWidget { color: #2C2C2A; font-size: 13px; }
            QTableWidget { background: #FFFFFF; color: #2C2C2A; gridline-color: #D3D1C7; }
            QHeaderView::section { background: #E6F1FB; color: #0C447C;
                padding: 5px; border: none; font-weight: 500; }
            QPushButton { background: #FFFFFF; border: 1px solid #B4B2A9;
                border-radius: 6px; padding: 7px 14px; color: #2C2C2A; }
            QPushButton:hover { background: #E6F1FB; }
            QPlainTextEdit { background: #FFFFFF; color: #2C2C2A; border: 1px solid #D3D1C7; }
            QSplitter::handle { height: 6px; background: #D3D1C7; }
            QComboBox { background: #FFFFFF; color: #2C2C2A;
                border: 1px solid #B4B2A9; border-radius: 6px; padding: 6px 10px; }
            QComboBox QAbstractItemView {
                background: #FFFFFF; color: #2C2C2A;
                selection-background-color: #E6F1FB; selection-color: #0C447C;
            }
            QInputDialog, QDialog { background: #FFFFFF; color: #2C2C2A; }
            QLineEdit { background: #FFFFFF; color: #2C2C2A;
                border: 1px solid #B4B2A9; border-radius: 6px; padding: 5px 8px; }
        """)
        self._build_ui()
        self._init_table()
        storage.init_db()
        self.log(f"就绪。监控币种：{'、'.join(self.cfg['currencies'])}；"
                 f"轮询间隔 {self.cfg.get('poll_interval_seconds', 60)}s；"
                 f"Agent 引擎：{'LLM(' + self.cfg['llm']['model'] + ')' if self.cfg['llm'].get('api_key') else '规则兜底（未配置 API Key）'}")

    # ---------- UI 构建 ----------
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeaderItem(TH_COL).setTextAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)  # 靠左，避免与表头▲▼重叠
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.itemChanged.connect(self.on_threshold_edited)
        self.table.itemSelectionChanged.connect(self.on_row_selected)

        # 表头"阈值%"上的全局步进按钮：全部币种阈值 ±0.1%
        hdr = self.table.horizontalHeader()
        hdr.geometriesChanged.connect(self._place_th_header_buttons)  # 列宽任何变化都重新钉按钮
        self.th_up_btn = QToolButton(hdr)
        self.th_up_btn.setArrowType(Qt.ArrowType.UpArrow)
        self.th_up_btn.setToolTip("全部币种阈值 +0.1%")
        self.th_up_btn.clicked.connect(lambda: self.step_all_thresholds(+0.1))
        self.th_dn_btn = QToolButton(hdr)
        self.th_dn_btn.setArrowType(Qt.ArrowType.DownArrow)
        self.th_dn_btn.setToolTip("全部币种阈值 -0.1%")
        self.th_dn_btn.clicked.connect(lambda: self.step_all_thresholds(-0.1))

        # 当日走势曲线（演示急跌时的关键观感）
        self.plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.plot.setTitle("当日走势（点选表格行切换币种）", size="10pt")
        self.plot.addLegend(offset=(10, 10))
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "时间")
        self.plot.setLabel("left", "汇率")

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.plot)
        split.setSizes([340, 220])
        split.splitterMoved.connect(lambda *a: self._place_th_header_buttons())
        root.addWidget(split)

        btns = QHBoxLayout()
        self.btn_toggle = QPushButton("开始监控")
        self.btn_toggle.clicked.connect(self.toggle_monitor)
        self.btn_once = QPushButton("立即轮询一次")
        self.btn_once.clicked.connect(lambda: self.start_worker(fetch=True))
        self.btn_demo = QPushButton("演示：模拟急跌")
        self.btn_demo.clicked.connect(self.demo_shock)
        self.btn_add = QPushButton("添加币种")
        self.btn_add.clicked.connect(self.add_currency)
        self.btn_del = QPushButton("删除选中币种")
        self.btn_del.clicked.connect(self.remove_currency)
        for b in (self.btn_toggle, self.btn_once, self.btn_demo, self.btn_add, self.btn_del):
            btns.addWidget(b)
        root.addLayout(btns)

        root.addWidget(QLabel("运行日志："))
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        self.logbox.setMaximumHeight(180)
        root.addWidget(self.logbox)

    def _init_table(self):
        self._loading = True
        self.table.setRowCount(0)
        for cur in self.cfg["currencies"]:
            self._append_row(cur)
        self._loading = False
        self._place_th_header_buttons()

    def _append_row(self, cur: str):
        row = self.table.rowCount()
        self.table.insertRow(row)
        th = self.cfg["thresholds_pct"].get(cur, self.cfg["thresholds_pct"].get("默认", 0.1))
        vals = [cur, "", "—", "—", "—", None, "待采集"]  # 阈值列放步进器控件
        for col, v in enumerate(vals):
            if v is None:
                continue
            item = QTableWidgetItem(str(v))
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            item.setForeground(QBrush(QColor("#2C2C2A")))
            item.setBackground(QBrush(QColor("#FFFFFF")))
            self.table.setItem(row, col, item)
        self.table.setCellWidget(row, TH_COL, self._make_th_spin(cur, th))

    def _make_th_spin(self, cur: str, value: float) -> QDoubleSpinBox:
        """单币种阈值步进器：▲▼ 每次 0.1，也可直接键入后回车。"""
        spin = QDoubleSpinBox()
        spin.setRange(0.01, 10.0)
        spin.setDecimals(2)
        spin.setSingleStep(0.1)
        spin.setSuffix("%")
        spin.setValue(value)  # 先设值再连信号，避免初始化触发写盘
        spin.setKeyboardTracking(False)
        spin.setAlignment(Qt.AlignmentFlag.AlignCenter)
        spin.setToolTip("▲▼ 每次调 0.1%；也可直接输入数字后按回车")
        spin.setStyleSheet(self._spin_style("#FFFFFF"))  # 样式里自绘浅灰小三角，替换系统默认箭头
        spin.valueChanged.connect(lambda v, c=cur: self._on_th_changed(c, v))
        return spin

    @staticmethod
    def _spin_style(bg: str) -> str:
        """阈值步进器样式：右侧固定画一对浅色小三角，选中文字保持深色可读。"""
        return f"""
        QDoubleSpinBox {{ background: {bg}; color: #2C2C2A;
            border: 1px solid #B4B2A9; border-radius: 4px; padding: 1px 4px 1px 6px;
            selection-background-color: #E6F1FB; selection-color: #2C2C2A; }}
        QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
            subcontrol-origin: border; width: 16px; background: #FFFFFF; border: none; }}
        QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
        QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
        QDoubleSpinBox::up-arrow {{ image: none; width: 0; height: 0;
            border-left: 4px solid transparent; border-right: 4px solid transparent;
            border-bottom: 5px solid #6B6A63; }}
        QDoubleSpinBox::down-arrow {{ image: none; width: 0; height: 0;
            border-left: 4px solid transparent; border-right: 4px solid transparent;
            border-top: 5px solid #6B6A63; }}
        """

    def _on_th_changed(self, cur: str, value: float):
        if getattr(self, "_loading", False):
            return
        self.cfg["thresholds_pct"][cur] = float(value)
        save_cfg(self.cfg)
        self.log(f"[配置] {cur} 预警阈值更新为 {value:g}%")

    def step_all_thresholds(self, delta: float):
        """表头 ▲▼：全部监控币种阈值同步 ±delta（默认 0.1%）。"""
        if not self.cfg["currencies"]:
            return
        for row in range(self.table.rowCount()):
            spin = self.table.cellWidget(row, TH_COL)
            if spin is None:
                continue
            spin.blockSignals(True)  # 批量调整只写一次盘
            spin.setValue(spin.value() + delta)
            self.cfg["thresholds_pct"][self.table.item(row, 0).text()] = float(spin.value())
            spin.blockSignals(False)
        save_cfg(self.cfg)
        self.log(f"[配置] 全部币种阈值 {'+' if delta > 0 else ''}{delta:g}% → "
                 + "、".join(f"{c} {self.cfg['thresholds_pct'][c]:g}%" for c in self.cfg["currencies"]))

    def _place_th_header_buttons(self):
        """把 ▲▼ 按钮钉在表头"阈值%"一节的右侧。"""
        hdr = self.table.horizontalHeader()
        try:
            x = hdr.sectionViewportPosition(TH_COL)
            w = hdr.sectionSize(TH_COL)
        except Exception:
            return
        h = hdr.height()
        bw, bh = 24, 20
        self.th_up_btn.setGeometry(x + w - bw * 2 - 6, max(1, (h - bh) // 2), bw, bh)
        self.th_dn_btn.setGeometry(x + w - bw - 3, max(1, (h - bh) // 2), bw, bh)

    # ---------- 数据流 ----------
    def on_timer(self):
        self.start_worker(fetch=True)

    def start_worker(self, fetch: bool):
        if self._busy:
            self.log("上一轮还在跑，跳过本次")
            return
        self.cfg = load_cfg()  # 每轮重读，接受小程序端/手动的配置变更
        self._busy = True
        self.worker = Worker(self.cfg, list(self.cfg["currencies"]), fetch)
        self.worker.sig_log.connect(self.log)
        self.worker.sig_result.connect(self.on_result)
        self.worker.sig_done.connect(self.on_worker_done)
        threading.Thread(target=self.worker.run, daemon=True).start()

    def on_worker_done(self):
        self._busy = False

    def on_result(self, analyses: list, decisions: list, sent: int):
        by_cur = {a["currency"]: a for a in analyses}
        self._loading = True
        for row in range(self.table.rowCount()):
            cur = self.table.item(row, 0).text()
            a = by_cur.get(cur)
            if a is None:
                continue
            self.table.item(row, 1).setText(a["code"])
            self.table.item(row, 2).setText(f"{a['rate']:.4f}")
            self.table.item(row, 3).setText(f"{a['base_rate']:.4f}")
            chg = f"{a['change_pct']:+.2f}%"
            self.table.item(row, 4).setText(chg)
            status = "已触发" if a["triggered"] else "正常"
            self.table.item(row, 6).setText(status)
            for col in range(7):
                it = self.table.item(row, col)
                if it is None:  # 阈值列是步进器控件
                    continue
                if a["triggered"]:
                    it.setBackground(QBrush(QColor("#FCEBEB")))
                    it.setForeground(QBrush(QColor("#791F1F")))
                else:
                    it.setBackground(QBrush(QColor("#EAF3DE" if a["samples_today"] > 1 else "#FFFFFF")))
                    it.setForeground(QBrush(QColor("#27500A" if a["samples_today"] > 1 else "#2C2C2A")))
            spin = self.table.cellWidget(row, TH_COL)
            if spin is not None:
                spin.setStyleSheet(self._spin_style("#FCEBEB" if a["triggered"] else "#FFFFFF"))
        self._loading = False

        for d in decisions:
            tag = "【预警】" if d["agent_action"] == "trigger_alert" else "【观察】"
            self.log(f"[思考] {tag}{d['currency_pair']} {d['fluctuation']} "
                     f"(阈值 {d['threshold_set']}, 引擎={d.get('engine')}) — {d.get('reason','')}")
        self.log(f"[行动] 预警下发 {sent} 条；累计预警记录 {storage.alert_count()} 条")
        self.update_plot()

    # ---------- 走势曲线 ----------
    def on_row_selected(self):
        self.update_plot()

    def _selected_currency(self) -> str | None:
        row = self.table.currentRow()
        return self.table.item(row, 0).text() if row >= 0 else None

    def update_plot(self):
        cur = self._selected_currency()
        if not cur:
            return
        from datetime import date, datetime as _dt
        hist = [h for h in storage.history(cur, 2000)
                if h["fetched_at"][:10] == date.today().isoformat()]
        self.plot.clear()
        if len(hist) < 2:
            self.plot.setTitle(f"{cur} · 当日走势（样本不足，{len(hist)} 条）", size="10pt")
            return
        # 横轴用真实时间戳（DateAxisItem 自动格式化为 HH:MM）
        xs = [_dt.strptime(h["fetched_at"], "%Y-%m-%d %H:%M:%S").timestamp() for h in hist]
        ys = [h["rate"] for h in hist]
        pen = pg.mkPen("#A32D2D" if ys[-1] < ys[0] else "#3B6D11", width=2)
        self.plot.plot(xs, ys, pen=pen, name=cur)
        self.plot.setTitle(f"{cur} · 当日走势（{len(ys)} 条，起 {hist[0]['fetched_at'][11:16]}）", size="10pt")
        # 阈值参考线
        th_pct = self.cfg["thresholds_pct"].get(cur, self.cfg["thresholds_pct"].get("默认", 0.1))
        base = ys[0]
        for sign in (1, -1):
            y = base * (1 + sign * th_pct / 100.0)
            line = pg.InfiniteLine(pos=y, angle=0, movable=False,
                                   pen=pg.mkPen("#888780", width=1, style=Qt.PenStyle.DashLine))
            self.plot.addItem(line)

    # ---------- 按钮动作 ----------
    def toggle_monitor(self):
        if self.timer.isActive():
            self.timer.stop()
            self.btn_toggle.setText("开始监控")
            self.log("监控已停止")
        else:
            interval = int(self.cfg.get("poll_interval_seconds", 60)) * 1000
            self.timer.start(interval)
            self.btn_toggle.setText("停止监控")
            self.log(f"监控已启动，每 {interval // 1000}s 轮询一次")
            self.start_worker(fetch=True)

    def demo_shock(self):
        cur = "美元"
        if cur not in self.cfg["currencies"]:
            QMessageBox.information(self, "提示", "演示针对美元，请先把美元加入监控列表")
            return
        final = simulate.inject_shock()
        self.log(f"[演示] 已注入 {cur} 急跌行情，现价 {final:.4f}，开始走完整链路…")
        self.start_worker(fetch=False)
        self.table.selectRow(0)

    def add_currency(self):
        choices = sorted(fetcher.CURRENCY_CODE.keys())
        # 用实例而非静态方法：静态 QInputDialog 不继承主窗口样式，深色主题下看不清
        dlg = QInputDialog(self)
        dlg.setWindowTitle("添加币种")
        dlg.setLabelText("选择中行牌价中的币种：")
        dlg.setComboBoxItems(choices)
        dlg.setComboBoxEditable(False)
        dlg.setStyleSheet(self.styleSheet())
        accepted = dlg.exec()
        name = dlg.textValue()
        if not accepted or not name:
            return
        if name in self.cfg["currencies"]:
            QMessageBox.information(self, "提示", f"{name} 已在监控列表")
            return
        self.cfg["currencies"].append(name)
        self.cfg["thresholds_pct"].setdefault(name, self.cfg["thresholds_pct"].get("默认", 0.1))
        save_cfg(self.cfg)
        self._loading = True
        self._append_row(name)
        self._loading = False
        self.log(f"已添加监控币种：{name}（阈值 {self.cfg['thresholds_pct'][name]:g}%）")

    def remove_currency(self):
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先在表格中选中一行")
            return
        cur = self.table.item(row, 0).text()
        if QMessageBox.question(self, "确认", f"停止监控 {cur} ？") != QMessageBox.StandardButton.Yes:
            return
        self.cfg["currencies"] = [c for c in self.cfg["currencies"] if c != cur]
        save_cfg(self.cfg)
        self.table.removeRow(row)
        self.log(f"已移除监控币种：{cur}")

    def on_threshold_edited(self, item: QTableWidgetItem):
        if self._loading or item.column() != 5:
            return
        cur = self.table.item(item.row(), 0).text()
        try:
            th = abs(float(item.text()))
        except ValueError:
            self.log(f"[配置] {cur} 阈值 '{item.text()}' 不是数字，已还原")
            self._loading = True
            old = self.cfg["thresholds_pct"].get(cur, self.cfg["thresholds_pct"].get("默认", 0.1))
            item.setText(f"{old:g}")
            self._loading = False
            return
        self.cfg["thresholds_pct"][cur] = th
        save_cfg(self.cfg)
        self.log(f"[配置] {cur} 预警阈值更新为 {th:g}%")

    # ---------- 日志 ----------
    def log(self, msg: str):
        self.logbox.appendPlainText(msg)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "th_up_btn"):
            self._place_th_header_buttons()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
