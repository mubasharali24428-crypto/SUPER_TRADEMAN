/**
 * SUPER_TRADEMAN ❖ W7 Workstream A — Interactive Charts (TradingView
 * lightweight-charts v4, vendored locally at vendor/lightweight-charts.standalone.js,
 * CSP default-src 'self' compliant — no CDN).
 *
 * Replaces the hand-drawn canvas price + equity renderers in app.js with:
 *   - CandlestickSeries  (live simulated price, per-asset)
 *   - HistogramSeries    (volume overlay on the price chart's bottom 20%)
 *   - AreaSeries         (equity curve across closed simulated trades)
 *
 * WCAG contract preserved from Phase-2 (AX-1):
 *   the chart surface is decorative to assistive tech (host wrapper carries
 *   role="img" + aria-label); the SAME data is mirrored into hidden data
 *   tables (#priceDataTable / #equityDataTable) built exclusively with
 *   createElement/textContent — never innerHTML.
 *
 * DATA ISOLATION: this module never touches the application's internal state
 * object. All input arrives through the single getChartData() provider
 * injected by init(); swapping the simulated generator for
 * GET /api/market-data later means changing that ONE function in app.js.
 */

(function () {
  'use strict';

  // -------------------------------------------------------------------------
  // Environment / constants
  // -------------------------------------------------------------------------
  const REDUCED_MOTION =
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // How many recent rows the hidden screen-reader data tables mirror.
  const TABLE_ROWS = 10;

  // Theme tokens mirrored from style.css :root (kept literal: the vendored
  // library cannot resolve CSS custom properties).
  const THEME = {
    textMuted: '#708198',
    gridLine: 'rgba(255, 255, 255, 0.04)',
    border: 'rgba(255, 255, 255, 0.08)',
    upColor: '#00ff88',
    downColor: '#ff3366',
    cyan: '#00f0ff',
    crosshairLabelBg: '#0d111b',
    fontMono: "'JetBrains Mono', monospace",
  };

  // -------------------------------------------------------------------------
  // Module state
  // -------------------------------------------------------------------------
  let getChartData = null; // injected data provider (see header comment)
  let priceChart = null;
  let equityChart = null;
  let candleSeries = null;
  let volumeSeries = null;
  let areaSeries = null;
  let priceResizeObserver = null;
  let equityResizeObserver = null;
  let syncingCrosshair = false; // re-entrancy guard for crosshair sync
  let initialFitDone = false;

  // time -> value maps rebuilt on every setData; used to place the synced
  // crosshair on the counterpart chart without guessing prices.
  let candleCloseByTime = new Map();
  let equityValueByTime = new Map();

  // -------------------------------------------------------------------------
  // Small helpers
  // -------------------------------------------------------------------------
  function el(id) {
    return document.getElementById(id);
  }

  function baseChartOptions() {
    return {
      layout: {
        background: { type: 'solid', color: 'rgba(0, 0, 0, 0)' },
        textColor: THEME.textMuted,
        fontSize: 11,
        fontFamily: THEME.fontMono,
        // The built-in TradingView <a> logo lands INSIDE a role="img" host,
        // tripping axe "nested-interactive" (focusable descendants of an
        // image role). Disabled here; Apache-2.0 attribution remains in the
        // vendored file's license header.
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: THEME.gridLine },
        horzLines: { color: THEME.gridLine },
      },
      rightPriceScale: { borderColor: THEME.border },
      timeScale: {
        borderColor: THEME.border,
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: 1 /* LightweightCharts.CrosshairMode.Normal */,
        vertLine: { color: THEME.cyan, labelBackgroundColor: THEME.crosshairLabelBg },
        horzLine: { color: THEME.cyan, labelBackgroundColor: THEME.crosshairLabelBg },
      },
      // v4 renders on an immediate-mode canvas with no autonomous tweening;
      // REDUCED_MOTION additionally gates every animated viewport helper
      // (scrollToPosition / scrollToRealTime) below — none are used, so the
      // chart is already reduced-motion-safe by construction.
    };
  }

  // -------------------------------------------------------------------------
  // Hidden WCAG data tables (textContent-only DOM construction)
  // -------------------------------------------------------------------------
  function fillTable(tbodyId, head, rows) {
    const tbody = el(tbodyId);
    if (!tbody) return;
    const frag = document.createDocumentFragment();

    const hr = document.createElement('tr');
    for (const cellText of head) {
      const th = document.createElement('th');
      th.scope = 'col';
      th.textContent = cellText;
      hr.appendChild(th);
    }
    frag.appendChild(hr);

    for (const row of rows) {
      const tr = document.createElement('tr');
      for (const cellText of row) {
        const td = document.createElement('td');
        td.textContent = cellText;
        tr.appendChild(td);
      }
      frag.appendChild(tr);
    }
    tbody.replaceChildren(frag);
  }

  function hhmmss(timeSec) {
    return new Date(timeSec * 1000).toISOString().slice(11, 19);
  }

  function updateHiddenTables(data) {
    const candleRows = data.candles.slice(-TABLE_ROWS).map(c => [
      hhmmss(c.time),
      c.open.toFixed(2),
      c.high.toFixed(2),
      c.low.toFixed(2),
      c.close.toFixed(2),
      String(Math.round(c.volume)),
    ]);
    fillTable(
      'priceTableBody',
      ['Time (UTC)', 'Open', 'High', 'Low', 'Close', 'Volume'],
      candleRows
    );

    const equityRows = data.equity.slice(-TABLE_ROWS).map((p, i) => [
      String(i + 1),
      hhmmss(p.time),
      `$${p.value.toFixed(2)}`,
    ]);
    fillTable(
      'equityTableBody',
      ['Trade #', 'Time (UTC)', 'Account Balance'],
      equityRows
    );
  }

  // -------------------------------------------------------------------------
  // Series population
  // -------------------------------------------------------------------------
  function updateSeries(data) {
    if (!priceChart || !equityChart) return;

    candleCloseByTime = new Map();
    const candlePoints = [];
    const volumePoints = [];
    for (const c of data.candles) {
      candlePoints.push({
        time: c.time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      });
      volumePoints.push({
        time: c.time,
        value: c.volume,
        color: c.close >= c.open
          ? 'rgba(0, 255, 136, 0.35)'
          : 'rgba(255, 51, 102, 0.35)',
      });
      candleCloseByTime.set(c.time, c.close);
    }
    candleSeries.setData(candlePoints);
    volumeSeries.setData(volumePoints);

    equityValueByTime = new Map();
    const equityPoints = [];
    for (const p of data.equity) {
      equityPoints.push({ time: p.time, value: p.value });
      equityValueByTime.set(p.time, p.value);
    }
    areaSeries.setData(equityPoints);

    // Fit the viewport once real dimensions exist, and again whenever the
    // dataset is fully replaced (asset switch / reset). Per-tick updates do
    // NOT steal the operator's zoom/pan — that is what keeps requirement
    // "native zoom/pan" usable alongside "fit-content on data update".
    if (!initialFitDone) {
      fitAll();
    }
  }

  function fitAll() {
    if (!priceChart || !equityChart) return;
    priceChart.timeScale().fitContent();
    equityChart.timeScale().fitContent();
    initialFitDone = true;
  }

  /** Full replacement (asset change / RESET) — refit the viewport. */
  function refit() {
    initialFitDone = false;
  }

  // -------------------------------------------------------------------------
  // Crosshair sync across the two charts
  // -------------------------------------------------------------------------
  function wireCrosshairSync(srcChart, srcMap, dstChart, dstSeries) {
    srcChart.subscribeCrosshairMove(param => {
      if (syncingCrosshair) return;
      syncingCrosshair = true;
      try {
        if (param && param.time !== undefined && param.time !== null &&
            dstSeries && srcMap.has(param.time)) {
          dstChart.setCrosshairPosition(srcMap.get(param.time), param.time, dstSeries);
        } else {
          dstChart.clearCrosshairPosition();
        }
      } finally {
        syncingCrosshair = false;
      }
    });
  }

  // -------------------------------------------------------------------------
  // Responsive resize (ResizeObserver — survives tab activation + window
  // resizes + sidebar reflow without listening to window.resize)
  // -------------------------------------------------------------------------
  function observeResize(chart, host) {
    if (typeof ResizeObserver === 'undefined') {
      chart.resize(host.clientWidth || 640, host.clientHeight || 420);
      return null;
    }
    const ro = new ResizeObserver(() => {
      const w = host.clientWidth;
      const h = host.clientHeight;
      if (w > 0 && h > 0) {
        chart.resize(w, h);
        if (!initialFitDone && candleSeries && candleSeries.data().length > 0) {
          fitAll();
        }
      }
    });
    ro.observe(host);
    return ro;
  }

  // -------------------------------------------------------------------------
  // Public API
  // -------------------------------------------------------------------------
  function init(options) {
    if (!window.LightweightCharts) {
      // Vendored lib failed to load: degrade loudly, never silently blank.
      console.error('[W7Charts] vendor/lightweight-charts.standalone.js missing');
      return false;
    }
    getChartData = options && typeof options.getChartData === 'function'
      ? options.getChartData
      : null;
    if (!getChartData) {
      console.error('[W7Charts] init requires { getChartData }');
      return false;
    }

    const priceHost = el('priceChartHost');
    const equityHost = el('equityChartHost');
    if (!priceHost || !equityHost) return false;

    priceChart = window.LightweightCharts.createChart(priceHost, baseChartOptions());
    candleSeries = priceChart.addCandlestickSeries({
      upColor: THEME.upColor,
      downColor: THEME.downColor,
      borderUpColor: THEME.upColor,
      borderDownColor: THEME.downColor,
      wickUpColor: THEME.upColor,
      wickDownColor: THEME.downColor,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });
    volumeSeries = priceChart.addHistogramSeries({
      priceScaleId: '', // overlay: independent scale pinned to the bottom fifth
      priceFormat: { type: 'volume' },
      priceLineVisible: false,
      lastValueVisible: false,
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    });

    equityChart = window.LightweightCharts.createChart(equityHost, baseChartOptions());
    areaSeries = equityChart.addAreaSeries({
      lineColor: THEME.cyan,
      topColor: 'rgba(0, 240, 255, 0.25)',
      bottomColor: 'rgba(0, 240, 255, 0.0)',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
    });

    wireCrosshairSync(priceChart, candleCloseByTime, equityChart, areaSeries);
    wireCrosshairSync(equityChart, equityValueByTime, priceChart, candleSeries);

    priceResizeObserver = observeResize(priceChart, priceHost);
    equityResizeObserver = observeResize(equityChart, equityHost);

    update();
    return true;
  }

  /** Called every engine heartbeat and on tab re-activation. */
  function update() {
    if (!priceChart || !getChartData) return;
    const data = getChartData();
    if (!data || !data.candles || !data.equity) return;
    updateSeries(data);
    updateHiddenTables(data);
  }

  /** Teardown for hot-swaps/tests. */
  function destroy() {
    if (priceResizeObserver) priceResizeObserver.disconnect();
    if (equityResizeObserver) equityResizeObserver.disconnect();
    if (priceChart) priceChart.remove();
    if (equityChart) equityChart.remove();
    priceChart = equityChart = candleSeries = volumeSeries = areaSeries = null;
    priceResizeObserver = equityResizeObserver = null;
    initialFitDone = false;
  }

  window.W7Charts = {
    init,
    update,
    refit,
    destroy,
    isLive: () => !!priceChart,
    /** true when the operator asked the OS for reduced motion. */
    reducedMotion: () => REDUCED_MOTION,
  };
})();
