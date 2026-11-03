const { BASE_URL } = require("../../utils/api");

Page({
  data: {
    rates: [],
    allRates: [],
    displayedRates: [],
    viewMode: "monitor",
    alerts: [],
    triggeredList: [],
    bannerDemo: null,
    todayAlerts: 0,
    loading: false,
    updatedAt: "",
    error: "",
    // 走势图
    chartCur: "",
    chartHint: "点击上方牌价行查看当日走势",
    chartRange: 1,  // 1=当日，7=近一周
    // 订阅
    email: "",
    subscribing: false,
    subMsg: "",
    subOk: false,
    // 监控设置
    allCurrencies: [],
    currencies: [],
    curChips: [],
    thresholdsText: {},
    saving: false,
    saveMsg: "",
    saveOk: false,
    // 预警筛选
    alertRange: 3,
    rangeChips: [
      { days: 3, label: "近三天", on: true },
      { days: 7, label: "近一周", on: false },
      { days: 30, label: "近一月", on: false }
    ],
    pairChips: [],
    alertCurSel: [],  // 默认不筛选（空 = 显示全部）；点币种 chip 才收窄
    filteredAlerts: []
  },

  onLoad() {
    this.loadSettings();
    this.refresh();
    this.timer = setInterval(() => this.refresh(), 30000);
  },
  onUnload() { clearInterval(this.timer); },
  onPullDownRefresh() { this.refresh(() => wx.stopPullDownRefresh()); },

  stamp() {
    const d = new Date();
    const p = (x) => (x < 10 ? "0" + x : "" + x);
    return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
  },

  today() {
    const d = new Date();
    const p = (x) => (x < 10 ? "0" + x : "" + x);
    return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate());
  },

  refresh(done) {
    if (this.data.loading) return;
    this.setData({ loading: true, error: "" });
    wx.request({
      url: BASE_URL + "/api/latest",
      success: (r) => {
        if (r.data && r.data.ok) {
          const rates = r.data.data.map((x) => ({
            ...x,
            rateText: x.rate.toFixed(4),
            dayText: "日内 " + Number(x.day_low).toFixed(4) + " ~ " + Number(x.day_high).toFixed(4)
                     + " · 阈值 " + x.threshold_pct + "%",
            changeText: (x.change_pct >= 0 ? "+" : "") + x.change_pct.toFixed(2) + "%",
            up: x.change_pct >= 0,
            arrow: x.change_pct >= 0 ? "▲" : "▼"
          }));
          const triggeredList = rates.filter((x) => x.triggered);
          this.setData({
            rates,
            triggeredList,
            updatedAt: this.stamp(),
            // 演示横幅数据由服务端提供（5 分钟内的模拟预警），真实横幅优先
            bannerDemo: triggeredList.length ? null : (r.data.demo_alert || null),
            displayedRates: this.data.viewMode === "monitor" ? rates : this.data.allRates
          });
          if (!this.data.chartCur && rates.length) {
            this.loadChart(rates[0].currency);
          }
        }
      },
      fail: () => this.setData({ error: "连不上数据服务，请检查电脑 server.py 是否在跑、手机是否同一 Wi-Fi" })
    });
    wx.request({
      url: BASE_URL + "/api/alerts",
      data: { n: 200 },
      success: (r) => {
        if (r.data && r.data.ok) {
          const real = r.data.data;  // 服务端已过滤，这里只会是真实预警
          const t = this.today();
          this.setData({
            alerts: real,
            todayAlerts: real.filter((x) => (x.created_at || "").slice(0, 10) === t).length
          });
          this.filterAlerts();  // 币种 chips 统一在 filterAlerts 里构建（含数量角标）
        }
      },
      complete: () => {
        this.setData({ loading: false });
        if (done && typeof done === "function") done();
      }
    });
  },

  // ---------- 浏览模式：我的监控 / 全部币种 ----------
  toggleView(e) {
    const mode = e.currentTarget.dataset.mode;
    if (mode === this.data.viewMode) return;
    this.setData({ viewMode: mode });
    if (mode === "all") {
      this.loadAll();
    } else {
      this.setData({ displayedRates: this.data.rates });
    }
  },

  loadAll() {
    wx.request({
      url: BASE_URL + "/api/all",
      success: (r) => {
        if (r.data && r.data.ok) {
          const allRates = r.data.data.map((x) => ({
            ...x,
            rateText: Number(x.rate).toFixed(4),
            dayText: x.monitored ? "已加入监控" : "未监控 · 点右侧加入",
            changeText: (x.change_pct >= 0 ? "+" : "") + x.change_pct.toFixed(2) + "%",
            up: x.change_pct >= 0,
            arrow: x.change_pct >= 0 ? "▲" : "▼"
          }));
          this.setData({ allRates, displayedRates: allRates });
        }
      },
      fail: () => this.setData({ error: "全部币种行情加载失败" })
    });
  },

  addToMonitor(e) {
    const name = e.currentTarget.dataset.cur;
    wx.showModal({
      title: "加入监控",
      content: "把 " + name + " 加入监控列表？加入后会按阈值自动预警。",
      success: (res) => {
        if (!res.confirm) return;
        wx.request({
          url: BASE_URL + "/api/settings",
          success: (s) => {
            if (!s.data || !s.data.ok) return;
            const d = s.data.data;
            const currencies = d.currencies.slice();
            if (currencies.indexOf(name) === -1) currencies.push(name);
            wx.request({
              url: BASE_URL + "/api/settings",
              method: "POST",
              data: { currencies, thresholds: d.thresholds_pct },
              success: (p) => {
                if (p.data && p.data.ok) {
                  wx.showToast({ title: "已加入监控", icon: "success" });
                  this.loadSettings();
                  this.refresh();
                  this.loadAll();
                } else {
                  wx.showToast({ title: "加入失败", icon: "none" });
                }
              }
            });
          }
        });
      }
    });
  },

  // ---------- 监控设置 ----------
  loadSettings() {
    wx.request({
      url: BASE_URL + "/api/settings",
      success: (r) => {
        if (r.data && r.data.ok) {
          const d = r.data.data;
          const th = {};
          d.currencies.forEach((c) => {
            th[c] = String(d.thresholds_pct[c] != null ? d.thresholds_pct[c] : 0.1);
          });
          this.setData({
            allCurrencies: d.all_currencies,
            currencies: d.currencies,
            thresholdsText: th,
            curChips: d.all_currencies.map((c) => ({
              name: c,
              on: d.currencies.indexOf(c) > -1
            }))
          });
        }
      }
    });
  },

  toggleCurrency(e) {
    const name = e.currentTarget.dataset.cur;
    let currencies = this.data.currencies.slice();
    const i = currencies.indexOf(name);
    if (i > -1) {
      if (currencies.length <= 1) {
        this.setData({ saveMsg: "至少保留一个监控币种", saveOk: false });
        return;
      }
      currencies.splice(i, 1);
    } else {
      currencies.push(name);
    }
    const th = this.data.thresholdsText;
    if (th[name] == null) th[name] = "0.1";
    this.setData({
      currencies,
      thresholdsText: th,
      curChips: this.data.allCurrencies.map((c) => ({
        name: c,
        on: currencies.indexOf(c) > -1
      }))
    });
  },

  onThresholdInput(e) {
    const name = e.currentTarget.dataset.cur;
    const th = this.data.thresholdsText;
    th[name] = e.detail.value;
    this.setData({ thresholdsText: th });
  },

  saveSettings() {
    this.setData({ saving: true, saveMsg: "" });
    wx.request({
      url: BASE_URL + "/api/settings",
      method: "POST",
      data: { currencies: this.data.currencies, thresholds: this.data.thresholdsText },
      success: (r) => {
        if (r.data && r.data.ok) {
          this.setData({ saveMsg: r.data.msg || "保存成功", saveOk: true });
          this.refresh();
        } else {
          this.setData({ saveMsg: (r.data && r.data.error) || "保存失败", saveOk: false });
        }
      },
      fail: () => this.setData({ saveMsg: "连不上数据服务", saveOk: false }),
      complete: () => this.setData({ saving: false })
    });
  },

  // ---------- 预警筛选 ----------
  setRange(e) {
    const days = Number(e.currentTarget.dataset.days);
    this.setData({
      alertRange: days,
      rangeChips: this.data.rangeChips.map((c) => ({ ...c, on: c.days === days }))
    });
    this.filterAlerts();
  },

  toggleAlertCur(e) {
    const code = e.currentTarget.dataset.cur;
    let sel = this.data.alertCurSel.slice();
    const i = sel.indexOf(code);
    if (i > -1) sel.splice(i, 1); else sel.push(code);
    this.setData({
      alertCurSel: sel,
      pairChips: this.data.pairChips.map((c) => ({ ...c, on: sel.indexOf(c.code) > -1 }))
    });
    this.filterAlerts();
  },

  filterAlerts() {
    const d = new Date(Date.now() - this.data.alertRange * 86400000);
    const p = (x) => (x < 10 ? "0" + x : "" + x);
    const cutoff = d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate())
      + " " + p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
    const sel = this.data.alertCurSel;
    const codeOf = (a) => (a.currency_pair || "").split("/")[0];
    const inRange = this.data.alerts.filter((a) => (a.created_at || "") >= cutoff);
    // chips = 监控中的币种 + 出现过预警的币种（并集），让"零警报"也可以被查询
    const codes = [];
    (this.data.rates || []).forEach((r) => {
      if (r.code && codes.indexOf(r.code) === -1) codes.push(r.code);
    });
    inRange.forEach((a) => {
      const c = codeOf(a);
      if (c && codes.indexOf(c) === -1) codes.push(c);
    });
    const counts = {};
    inRange.forEach((a) => {
      const c = codeOf(a);
      counts[c] = (counts[c] || 0) + 1;
    });
    this.setData({
      pairChips: codes.map((c) => ({
        code: c,
        n: counts[c] || 0,
        on: sel.indexOf(c) > -1
      })),
      filteredAlerts: inRange.filter((a) => sel.length === 0 || sel.indexOf(codeOf(a)) > -1)
    });
  },

  // ---------- 走势图 ----------
  onRowTap(e) {
    const cur = e.currentTarget.dataset.cur;
    if (cur && cur !== this.data.chartCur) this.loadChart(cur);
  },

  onChartRange(e) {
    const days = Number(e.currentTarget.dataset.days);
    if (days === this.data.chartRange || !this.data.chartCur) return;
    this.setData({ chartRange: days });
    this.loadChart(this.data.chartCur);
  },

  loadChart(cur) {
    this.setData({ chartCur: cur, chartHint: "加载中…" });
    wx.request({
      url: BASE_URL + "/api/rates",
      data: { cur, n: 2500, days: this.data.chartRange },
      success: (r) => {
        if (r.data && r.data.ok) {
          const pts = r.data.data
            .filter((x) => x.rate != null)
            .map((x) => ({ v: x.rate, t: x.fetched_at || "" }));
          this.setData({ chartHint: "最近 " + pts.length + " 个采样点" });
          this.drawChart(pts);
        }
      },
      fail: () => this.setData({ chartHint: "走势数据加载失败" })
    });
  },

  drawChart(pts) {
    if (pts.length < 2) {
      this.setData({ chartHint: "当日样本不足（" + pts.length + " 条），稍后再看" });
      return;
    }
    const query = this.createSelectorQuery();
    query.select("#fxchart").fields({ node: true, size: true }).exec((res) => {
      if (!res || !res[0] || !res[0].node) return;
      const canvas = res[0].node;
      if (!this._dpr) this._dpr = wx.getWindowInfo().pixelRatio || 2;
      const dpr = this._dpr;
      canvas.width = res[0].width * dpr;
      canvas.height = res[0].height * dpr;
      const ctx = canvas.getContext("2d");
      ctx.scale(dpr, dpr);
      const W = res[0].width, H = res[0].height;
      const pad = { l: 60, r: 14, t: 14, b: 22 };
      const vals = pts.map((p) => p.v);
      const lo = Math.min(...vals), hi = Math.max(...vals);
      const span = (hi - lo) || hi * 0.0001;
      const yMin = lo - span * 0.15, yMax = hi + span * 0.15;
      const X = (i) => pad.l + (W - pad.l - pad.r) * i / (pts.length - 1);
      const Y = (v) => pad.t + (H - pad.t - pad.b) * (1 - (v - yMin) / (yMax - yMin));
      // 刻度精度自适应：波动越小保留越多位，避免相邻刻度四舍五入后重名
      const dec = span < 0.001 ? 5 : span < 0.01 ? 4 : 3;

      ctx.clearRect(0, 0, W, H);
      // 网格 + 纵轴刻度
      ctx.strokeStyle = "#E5EAF2";
      ctx.fillStyle = "#8A94A6";
      ctx.font = "10px sans-serif";
      ctx.lineWidth = 1;
      for (let g = 0; g <= 3; g++) {
        const v = yMin + (yMax - yMin) * g / 3;
        const y = Y(v);
        ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(W - pad.r, y); ctx.stroke();
        ctx.fillText(v.toFixed(dec), 4, y + 3);
      }
      // 横轴刻度（首/1/4/中/3/4/末 共 5 个点）：单日显示 HH:MM，多日显示 MM-DD
      const multiDay = this.data.chartRange > 1;
      ctx.textAlign = "center";
      const tickN = Math.min(4, pts.length - 1);
      for (let g = 0; g <= tickN; g++) {
        const idx = Math.round((pts.length - 1) * g / tickN);
        const t = pts[idx].t || "";
        const label = multiDay ? t.slice(5, 10) : t.slice(11, 16);
        if (!label) continue;
        ctx.textAlign = g === 0 ? "left" : (g === tickN ? "right" : "center");
        ctx.fillText(label, X(idx), H - 6);
      }
      ctx.textAlign = "left";
      // 开盘基准线（虚线）
      ctx.setLineDash([4, 4]);
      ctx.strokeStyle = "#B4B2A9";
      ctx.beginPath(); ctx.moveTo(pad.l, Y(vals[0])); ctx.lineTo(W - pad.r, Y(vals[0])); ctx.stroke();
      ctx.setLineDash([]);
      // 主曲线
      ctx.strokeStyle = vals[vals.length - 1] >= vals[0] ? "#A32D2D" : "#3B6D11";
      ctx.lineWidth = 2;
      ctx.beginPath();
      pts.forEach((p, i) => { i ? ctx.lineTo(X(i), Y(p.v)) : ctx.moveTo(X(i), Y(p.v)); });
      ctx.stroke();
      // 末点
      ctx.fillStyle = ctx.strokeStyle;
      ctx.beginPath();
      ctx.arc(X(pts.length - 1), Y(vals[vals.length - 1]), 3.5, 0, Math.PI * 2);
      ctx.fill();

      // 标题里交代振幅；波动极小时直说持平，免得看着像没数据
      const spanPct = (hi - lo) / lo * 100;
      const rangeWord = this.data.chartRange > 1 ? "近一周" : "日内";
      let hint = "最近 " + pts.length + " 个采样点 · " + rangeWord + "振幅 " + (hi - lo).toFixed(dec);
      if (spanPct < 0.01 && this.data.chartRange === 1) hint += "（波动极小，走势持平）";
      this.setData({ chartHint: hint });
    });
  },

  // ---------- 订阅 ----------
  onEmailInput(e) { this.setData({ email: e.detail.value, subMsg: "" }); },

  subscribe() {
    const email = this.data.email.trim();
    if (!email) { this.setData({ subMsg: "请先填写邮箱", subOk: false }); return; }
    this.setData({ subscribing: true });
    wx.request({
      url: BASE_URL + "/api/subscribe",
      method: "POST",
      data: { email },
      success: (r) => {
        if (r.data && r.data.ok) {
          this.setData({ subMsg: r.data.msg, subOk: true });
        } else {
          this.setData({ subMsg: (r.data && r.data.error) || "订阅失败，请重试", subOk: false });
        }
      },
      fail: () => this.setData({ subMsg: "连不上数据服务，请检查 server.py 是否在跑", subOk: false }),
      complete: () => this.setData({ subscribing: false })
    });
  }
});
