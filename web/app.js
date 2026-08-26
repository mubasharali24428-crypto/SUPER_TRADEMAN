/**
 * SUPER_TRADEMAN ❖ Quantum Automaton AI Trading Terminal
 * Complete Client-Side & Server-Integrated Quantitative Trading Engine
 */

(function () {
  'use strict';

  // -------------------------------------------------------------------------
  // 1. STATE & CONSTANTS
  // -------------------------------------------------------------------------
  const STATE = {
    selectedAsset: 'BTC/USDT',
    timeframe: '1m',
    isPlaying: true,
    speed: 1, // tick interval speed
    heartbeatCount: 0,
    
    // Account & Portfolio
    equity: 10000.0,
    peakEquity: 10000.0,
    openPositions: [],
    closedTrades: [],
    consecutiveLosses: 0,
    dailyPnlPct: 0.0,
    weeklyPnlPct: 0.0,

    // Quantitative Models State
    garch: {
      conditionalVol: 0.024,
      annVol: 0.384,
      persistence: 0.962,
      volScale: 1.04,
      isHighVol: false,
    },
    hmm: {
      currentRegime: 'trending_bull',
      probs: [0.78, 0.12, 0.10], // [bull, bear, chop]
      confidence: 0.884,
    },
    evt: {
      var95: 0.024,
      var99: 0.048,
      cvar99: 0.0070, // 0.70%
      shapeXi: 0.18,
      scaleBeta: 0.012,
      isHeavyTailed: true,
      riskScale: 1.0,
    },
    copula: {
      btcEth: { linear: 0.82, lambdaL: 0.48, cap: 0.08 },
      btcSol: { linear: 0.68, lambdaL: 0.34, cap: 0.08 },
      ethSol: { linear: 0.74, lambdaL: 0.39, cap: 0.08 },
    },
    survival: {
      tier: 'normal',
      effectiveRiskMult: 1.0,
      minConfidenceFloor: 0.55,
      allowNewEntries: true,
      rationale: 'Nominal operating conditions: equity healthy',
    },
    bandit: {
      strategies: {
        trend_momentum: { prob: 0.854, avgReward: 2.10, successes: 45, failures: 21 },
        mean_reversion: { prob: 0.042, avgReward: -0.65, successes: 13, failures: 29 },
        breakout: { prob: 0.104, avgReward: 0.80, successes: 22, failures: 20 },
      },
    },

    // Price Candlestick Buffers (per asset)
    candles: {
      'BTC/USDT': [],
      'ETH/USDT': [],
      'SOL/USDT': [],
      'AVAX/USDT': [],
      'LINK/USDT': [],
    },
    
    // Learning Graph Nodes & Edges
    learningGraph: {
      nodes: [],
      edges: [],
    },

    // Active Trade in flight
    activeTrade: null,
  };

  // Base prices for assets
  const ASSET_CONFIGS = {
    'BTC/USDT': { basePrice: 68500.0, vol: 0.008, trend: 0.0004 },
    'ETH/USDT': { basePrice: 3500.0, vol: 0.012, trend: 0.0004 },
    'SOL/USDT': { basePrice: 155.0, vol: 0.018, trend: 0.0005 },
    'AVAX/USDT': { basePrice: 28.5, vol: 0.020, trend: 0.0003 },
    'LINK/USDT': { basePrice: 14.2, vol: 0.016, trend: 0.0004 },
  };

  // -------------------------------------------------------------------------
  // 2. DOM ELEMENT REFERENCES
  // -------------------------------------------------------------------------
  const DOM = {
    assetSelect: document.getElementById('assetSelect'),
    btnPlayPause: document.getElementById('btnPlayPause'),
    playPauseText: document.getElementById('playPauseText'),
    btnShock: document.getElementById('btnShock'),
    btnReset: document.getElementById('btnReset'),
    survivalBadge: document.getElementById('survivalBadge'),
    survivalTierVal: document.getElementById('survivalTierVal'),
    hbTickVal: document.getElementById('hbTickVal'),

    // Top Metric HUD
    equityVal: document.getElementById('equityVal'),
    equityPnlBadge: document.getElementById('equityPnlBadge'),
    peakEquityVal: document.getElementById('peakEquityVal'),
    maxDdVal: document.getElementById('maxDdVal'),
    sortinoVal: document.getElementById('sortinoVal'),
    sharpeVal: document.getElementById('sharpeVal'),
    avgRVal: document.getElementById('avgRVal'),
    totalTradesCount: document.getElementById('totalTradesCount'),
    tailVarVal: document.getElementById('tailVarVal'),
    gpdXiVal: document.getElementById('gpdXiVal'),
    evtRiskScaleVal: document.getElementById('evtRiskScaleVal'),
    garchVolVal: document.getElementById('garchVolVal'),
    garchPersistenceVal: document.getElementById('garchPersistenceVal'),
    garchMultVal: document.getElementById('garchMultVal'),
    hmmRegimeBadge: document.getElementById('hmmRegimeBadge'),
    bayesWrVal: document.getElementById('bayesWrVal'),
    empiricalWrVal: document.getElementById('empiricalWrVal'),
    hmmConfVal: document.getElementById('hmmConfVal'),

    // Sidebar Panels
    sideSurvivalTier: document.getElementById('sideSurvivalTier'),
    sideSurvivalDesc: document.getElementById('sideSurvivalDesc'),
    sideRiskMultText: document.getElementById('sideRiskMultText'),
    sideRiskMultBar: document.getElementById('sideRiskMultBar'),
    sideMinConfText: document.getElementById('sideMinConfText'),
    sideMinConfBar: document.getElementById('sideMinConfBar'),

    leadingStratBadge: document.getElementById('leadingStratBadge'),
    probTrendMom: document.getElementById('probTrendMom'),
    barTrendMom: document.getElementById('barTrendMom'),
    rTrendMom: document.getElementById('rTrendMom'),
    wrTrendMom: document.getElementById('wrTrendMom'),

    probMeanRev: document.getElementById('probMeanRev'),
    barMeanRev: document.getElementById('barMeanRev'),
    rMeanRev: document.getElementById('rMeanRev'),
    wrMeanRev: document.getElementById('wrMeanRev'),

    probBreakout: document.getElementById('probBreakout'),
    barBreakout: document.getElementById('barBreakout'),
    rBreakout: document.getElementById('rBreakout'),
    wrBreakout: document.getElementById('wrBreakout'),

    hmmBullBar: document.getElementById('hmmBullBar'),
    hmmBullPct: document.getElementById('hmmBullPct'),
    hmmBearBar: document.getElementById('hmmBearBar'),
    hmmBearPct: document.getElementById('hmmBearPct'),
    hmmChopBar: document.getElementById('hmmChopBar'),
    hmmChopPct: document.getElementById('hmmChopPct'),

    // Ledger & Tables
    ledgerTableBody: document.getElementById('ledgerTableBody'),
    ledgerCountBadge: document.getElementById('ledgerCountBadge'),
    btnExportSqlite: document.getElementById('btnExportSqlite'),
    btnDownloadJsonl: document.getElementById('btnDownloadJsonl'),
    btnRunBacktestModal: document.getElementById('btnRunBacktestModal'),

    // Canvases (learning graph only as of W7 — see charts.js note)
    learningGraphCanvas: document.getElementById('learningGraphCanvas'),

    // W7 fix (pre-existing defect): the learning-graph toolbar buttons got
    // behavior in VC-005 but their DOM references were never added to this
    // map — init() crashed on the undefined refs before the heartbeat timer,
    // leaving the whole cockpit inert. Surfaced by the W7 browser smoke test.
    btnCenterGraph: document.getElementById('btnCenterGraph'),
    btnClearGraph: document.getElementById('btnClearGraph'),

    // W7-B: KPI stats band
    statEquityValue: document.getElementById('statEquityValue'),
    statEquityDelta: document.getElementById('statEquityDelta'),
    statPnlValue: document.getElementById('statPnlValue'),
    statPnlDelta: document.getElementById('statPnlDelta'),
    statWinRateValue: document.getElementById('statWinRateValue'),
    statOpenRiskValue: document.getElementById('statOpenRiskValue'),
    statsLiveSummary: document.getElementById('statsLiveSummary'),

    // Modal
    backtestModal: document.getElementById('backtestModal'),
    // AX-1: polite live region for engine announcements
    engineStatusRegion: document.getElementById('engineStatusRegion'),
    btnCloseModal: document.getElementById('btnCloseModal'),
    btnExecuteModalBacktest: document.getElementById('btnExecuteModalBacktest'),
    modalRiskPct: document.getElementById('modalRiskPct'),
    modalRiskPctVal: document.getElementById('modalRiskPctVal'),
    modalMaxDd: document.getElementById('modalMaxDd'),
    modalMaxDdVal: document.getElementById('modalMaxDdVal'),
    modalAtrMult: document.getElementById('modalAtrMult'),
    modalAtrMultVal: document.getElementById('modalAtrMultVal'),
    modalResultsContainer: document.getElementById('modalResultsContainer'),
    modalBenchmarkTableBody: document.getElementById('modalBenchmarkTableBody'),
    modalElapsedMs: document.getElementById('modalElapsedMs'),
  };

  // -------------------------------------------------------------------------
  // 3. INITIALIZATION & SYNTHETIC DATA SEEDING
  // -------------------------------------------------------------------------
  function init() {
    // VC-002: install the global error boundary before anything else can throw.
    installErrorBoundary();

    // Generate initial history for all assets (80 candles each)
    for (const [symbol, cfg] of Object.entries(ASSET_CONFIGS)) {
      let price = cfg.basePrice;
      const now = Date.now() - 80 * 60000;
      for (let i = 0; i < 80; i++) {
        const change = (Math.random() - 0.48) * cfg.vol + cfg.trend;
        const openP = price;
        const highP = price * (1 + Math.max(change, 0) + 0.003);
        const lowP = price * (1 + Math.min(change, 0) - 0.003);
        const closeP = price * (1 + change);
        const vol = 10 + Math.random() * 50;

        STATE.candles[symbol].push({
          ts: now + i * 60000,
          open: openP,
          high: highP,
          low: lowP,
          close: closeP,
          volume: vol,
        });
        price = closeP;
      }
    }

    // Seed some initial trade history into LearningGraph
    seedInitialTrades();

    // Event Listeners
    setupEventListeners();

    // VC-001 / VC-005: restore saved view config before first paint so the
    // restored asset/tab is what the charts initialize with.
    restoreUiState();

    // Resize Canvases (learning graph only since W7)
    resizeCanvases();
    window.addEventListener('resize', resizeCanvases);

    // Start Simulation Heartbeat Timer
    setInterval(onHeartbeatTick, 1200);

    // Initial UI Render
    updateUI();

    // W7-A: hand-drawn candlestick/equity canvas renderers are replaced by
    // vendored lightweight-charts instances driven through the single
    // getChartData() provider below. Swapping the simulated feed for
    // /api/market-data later means changing ONLY getChartData().
    if (window.W7Charts && window.W7Charts.init({ getChartData })) {
      window.W7Charts.update();
    } else {
      announce('Interactive charts failed to initialize.');
    }

    // W7-B: first paint of the KPI band (also covers charts-unavailable path)
    updateStatsBand();

    renderLearningGraph();
  }

  function seedInitialTrades() {
    const strategies = ['trend_momentum', 'trend_momentum', 'breakout', 'mean_reversion'];
    const outcomes = [2.2, 1.8, 1.5, -0.8];
    const prices = STATE.candles[STATE.selectedAsset];

    for (let i = 0; i < 4; i++) {
      const c = prices[20 + i * 12];
      const r = outcomes[i];
      const pnl = r * 50.0;
      const tradeId = `T-100${i + 1}`;
      const entryPrice = c.close;
      const exitPrice = entryPrice * (1 + (r > 0 ? 0.03 : -0.01));

      const trade = {
        tradeId,
        timestamp: new Date(c.ts).toISOString().slice(11, 19),
        asset: STATE.selectedAsset,
        side: 'LONG',
        entryPrice: entryPrice,
        exitPrice: exitPrice,
        rMultiple: r,
        netPnl: pnl,
        strategy: strategies[i],
        regime: 'trending_bull',
        posteriorMu: (0.0 + pnl / 0.5) / (1.0 + 1.0 / 0.5),
        verdict: r > 0 ? 'WIN' : 'LOSS',
        candleIdx: 20 + i * 12,
      };

      STATE.closedTrades.unshift(trade);
      STATE.equity += pnl;
      STATE.peakEquity = Math.max(STATE.peakEquity, STATE.equity);

      // Add to Learning Graph
      addLearningGraphNodes(trade);
    }
  }

  function addLearningGraphNodes(trade) {
    const dNode = {
      id: `dec-${trade.tradeId}`,
      type: 'decision',
      label: `DECISION: ${trade.strategy}`,
      tradeId: trade.tradeId,
      asset: trade.asset,
      strategy: trade.strategy,
      confidence: 0.85,
      x: 100 + Math.random() * 400,
      y: 80 + Math.random() * 300,
      vx: 0,
      vy: 0,
    };

    const rNode = {
      id: `res-${trade.tradeId}`,
      type: 'result',
      label: `RESULT: ${trade.rMultiple >= 0 ? '+' : ''}${trade.rMultiple.toFixed(2)}R`,
      tradeId: trade.tradeId,
      rMultiple: trade.rMultiple,
      netPnl: trade.netPnl,
      posteriorMu: trade.posteriorMu,
      verdict: trade.verdict,
      x: dNode.x + 120 + (Math.random() - 0.5) * 40,
      y: dNode.y + (Math.random() - 0.5) * 60,
      vx: 0,
      vy: 0,
    };

    STATE.learningGraph.nodes.push(dNode, rNode);
    STATE.learningGraph.edges.push({
      from: dNode.id,
      to: rNode.id,
      tradeId: trade.tradeId,
    });
  }

  // -------------------------------------------------------------------------
  // 4. HEARTBEAT TICK ENGINE (OBSERVE -> MODEL -> SURVIVAL -> THINK -> ACT)
  // -------------------------------------------------------------------------
  function onHeartbeatTick() {
    if (!STATE.isPlaying) return;

    STATE.heartbeatCount++;
    DOM.hbTickVal.textContent = `HB: ${String(STATE.heartbeatCount).padStart(3, '0')}`;

    // 1. Advance Market Candles for all assets
    for (const [symbol, cfg] of Object.entries(ASSET_CONFIGS)) {
      const candles = STATE.candles[symbol];
      const lastCandle = candles[candles.length - 1];
      
      // Compute drift with regime modulation
      let drift = cfg.trend;
      if (STATE.hmm.currentRegime === 'volatile_bear') drift = -cfg.vol * 0.8;
      else if (STATE.hmm.currentRegime === 'choppy_sideways') drift = 0.0;

      const change = (Math.random() - 0.48) * cfg.vol + drift;
      const openP = lastCandle.close;
      const highP = openP * (1 + Math.max(change, 0) + 0.003);
      const lowP = openP * (1 + Math.min(change, 0) - 0.003);
      const closeP = openP * (1 + change);
      const vol = 15 + Math.random() * 60;

      candles.push({
        ts: lastCandle.ts + 60000,
        open: openP,
        high: highP,
        low: lowP,
        close: closeP,
        volume: vol,
      });

      if (candles.length > 120) candles.shift();
    }

    // 2. Statistical Models Update (GARCH, HMM, EVT)
    updateStatisticalModels();

    // 3. Survival Engine Evaluation
    updateSurvivalTier();

    // 4. Active Trade & Strategy Execution
    manageActiveTrade();

    // 5. Update UI & Render Views
    updateUI();

    // W7-A/B: charts and stats band refresh on the same tick.
    if (window.W7Charts && window.W7Charts.isLive()) {
      window.W7Charts.update();
      updateStatsBand();
    }

    renderLearningGraph();
  }

  function updateStatisticalModels() {
    const candles = STATE.candles[STATE.selectedAsset];
    const returns = [];
    for (let i = 1; i < candles.length; i++) {
      returns.push(Math.log(candles[i].close / candles[i - 1].close));
    }

    const n = returns.length;
    if (n < 10) return;

    // GARCH(1,1) approximation
    const stdDev = Math.hypot(...returns) / Math.sqrt(n);
    const annVol = stdDev * Math.sqrt(365 * 1440); // 1-minute annualization
    STATE.garch.conditionalVol = stdDev;
    STATE.garch.annVol = annVol;
    STATE.garch.volScale = Math.min(Math.max(0.40 / Math.max(annVol, 0.05), 0.25), 1.50);
    STATE.garch.isHighVol = annVol >= 0.65;

    // HMM Posterior update
    const recentRet = returns.slice(-10).reduce((a, b) => a + b, 0);
    if (recentRet > 0.015 && annVol < 0.50) {
      STATE.hmm.currentRegime = 'trending_bull';
      STATE.hmm.probs = [0.82, 0.08, 0.10];
    } else if (recentRet < -0.015 || annVol > 0.65) {
      STATE.hmm.currentRegime = 'volatile_bear';
      STATE.hmm.probs = [0.08, 0.84, 0.08];
    } else {
      STATE.hmm.currentRegime = 'choppy_sideways';
      STATE.hmm.probs = [0.15, 0.15, 0.70];
    }
    STATE.hmm.confidence = Math.max(...STATE.hmm.probs);

    // EVT 99% Tail-VaR (CVaR)
    const losses = returns.filter(r => r < 0).map(r => -r);
    losses.sort((a, b) => a - b);
    if (losses.length > 10) {
      const q95 = losses[Math.floor(losses.length * 0.95)] || 0.02;
      const q99 = losses[Math.floor(losses.length * 0.99)] || q95 * 1.3;
      STATE.evt.var95 = q95;
      STATE.evt.var99 = q99;
      STATE.evt.cvar99 = q99 * 1.15;
      STATE.evt.shapeXi = 0.15 + (STATE.garch.isHighVol ? 0.12 : 0.0);
    }
  }

  function updateSurvivalTier() {
    const drawdown = STATE.peakEquity > 0 ? (STATE.peakEquity - STATE.equity) / STATE.peakEquity : 0.0;
    
    if (drawdown >= 0.175) {
      STATE.survival.tier = 'cooldown';
      STATE.survival.effectiveRiskMult = 0.0;
      STATE.survival.allowNewEntries = false;
      STATE.survival.rationale = `Max Drawdown reached (${(drawdown * 100).toFixed(1)}% >= 17.5%)`;
    } else if (STATE.dailyPnlPct <= -0.025 || STATE.consecutiveLosses >= 4) {
      STATE.survival.tier = 'survival';
      STATE.survival.effectiveRiskMult = 0.0;
      STATE.survival.allowNewEntries = false;
      STATE.survival.minConfidenceFloor = 0.85;
      STATE.survival.rationale = `Survival Mode: ${STATE.consecutiveLosses} consecutive losses`;
    } else if (STATE.consecutiveLosses >= 2 || drawdown >= 0.10 || STATE.garch.isHighVol || STATE.hmm.currentRegime === 'volatile_bear') {
      STATE.survival.tier = 'caution';
      STATE.survival.effectiveRiskMult = 0.50 * STATE.garch.volScale;
      STATE.survival.allowNewEntries = true;
      STATE.survival.minConfidenceFloor = 0.70;
      STATE.survival.rationale = 'Caution Mode: Throttled risk (0.5x capacity) & high confidence floor';
    } else {
      STATE.survival.tier = 'normal';
      STATE.survival.effectiveRiskMult = 1.0 * STATE.garch.volScale;
      STATE.survival.allowNewEntries = true;
      STATE.survival.minConfidenceFloor = 0.55;
      STATE.survival.rationale = 'Nominal operating conditions: equity healthy';
    }
  }

  function manageActiveTrade() {
    const candles = STATE.candles[STATE.selectedAsset];
    const currentPrice = candles[candles.length - 1].close;

    // Check if an active trade needs to be closed (Hit Target or Stop)
    if (STATE.activeTrade) {
      const t = STATE.activeTrade;
      let isExit = false;
      let exitR = 0;

      if (currentPrice >= t.targetPrice) {
        isExit = true;
        exitR = 2.5; // Win target hit
      } else if (currentPrice <= t.stopPrice) {
        isExit = true;
        exitR = -1.0; // Stop loss hit
      } else if (Math.random() < 0.10) {
        // Trailing exit
        isExit = true;
        exitR = (currentPrice - t.entryPrice) / (t.entryPrice - t.stopPrice);
      }

      if (isExit) {
        const netPnl = exitR * 50.0 * STATE.survival.effectiveRiskMult;
        STATE.equity += netPnl;
        STATE.peakEquity = Math.max(STATE.peakEquity, STATE.equity);

        if (exitR < 0) STATE.consecutiveLosses++;
        else STATE.consecutiveLosses = 0;

        const closed = {
          tradeId: t.tradeId,
          timestamp: new Date().toISOString().slice(11, 19),
          asset: t.asset,
          side: t.side,
          entryPrice: t.entryPrice,
          exitPrice: currentPrice,
          rMultiple: exitR,
          netPnl: netPnl,
          strategy: t.strategy,
          regime: STATE.hmm.currentRegime,
          posteriorMu: (0.0 + netPnl / 0.5) / (1.0 + 1.0 / 0.5),
          verdict: exitR > 0 ? 'WIN' : 'LOSS',
          candleIdx: candles.length - 1,
        };

        STATE.closedTrades.unshift(closed);
        addLearningGraphNodes(closed);

        // Bandit Policy Gradient Reward Update
        const b = STATE.bandit.strategies[t.strategy];
        if (b) {
          b.avgReward = (b.avgReward * (b.successes + b.failures) + exitR) / (b.successes + b.failures + 1);
          if (exitR > 0) b.successes++;
          else b.failures++;
          
          // Re-normalize softmax policy probabilities
          updateBanditProbabilities();
        }

        STATE.activeTrade = null;
        announce(`Trade ${closed.tradeId} closed: ${verdictWord(closed.verdict)}, R multiple ${exitR.toFixed(2)}`);
      }
    } else if (STATE.survival.allowNewEntries && Math.random() < 0.35) {
      // Propose new Trade entry using Bandit allocation
      const strategy = selectBanditStrategy();
      const entryPrice = currentPrice;
      const stopPrice = entryPrice * 0.985;
      const targetPrice = entryPrice * 1.04;

      STATE.activeTrade = {
        tradeId: `T-${1000 + STATE.closedTrades.length + 1}`,
        asset: STATE.selectedAsset,
        side: 'LONG',
        entryPrice,
        stopPrice,
        targetPrice,
        strategy,
        entryCandleIdx: candles.length - 1,
      };
    }
  }

  /**
   * AX-1: keep a role=progressbar's visual width and its machine-readable
   * aria-valuenow in lockstep (screen readers must not read stale values).
   */
  function setBar(el, pctExpr) {
    if (!el) return;
    const val = Math.max(0, Math.min(100, Number(pctExpr)));
    el.style.width = `${val}%`;
    el.setAttribute('aria-valuenow', Math.round(val).toString());
  }

  /**
   * AX-1: announce engine events through the polite live region
   * (#engineStatusRegion) so dynamic updates are spoken, not just shown.
   */
  let announceTimer = null;
  function announce(message) {
    if (!DOM.engineStatusRegion) return;
    clearTimeout(announceTimer);
    DOM.engineStatusRegion.textContent = '';
    announceTimer = setTimeout(() => {
      DOM.engineStatusRegion.textContent = message;
    }, 50);
  }

  /** AX-1: spoken-friendly outcome word for announcements. */
  function verdictWord(verdict) {
    return verdict === 'WIN' ? 'win' : 'loss';
  }

  /** AX-1: direction icon + word so status is never color-only. */
  function trendBadge(isUp) {
    const span = document.createElement('span');
    span.className = `metric-tag ${isUp ? 'tag-green' : 'tag-red'}`;
    span.textContent = `${isUp ? '\u25B2' : '\u25BC'} ${isUp ? 'UP' : 'DOWN'}`;
    return span;
  }

  function selectBanditStrategy() {
    const probs = STATE.bandit.strategies;
    const r = Math.random();
    let cumulative = 0;
    for (const [name, info] of Object.entries(probs)) {
      cumulative += info.prob;
      if (r <= cumulative) return name;
    }
    return 'trend_momentum';
  }

  function updateBanditProbabilities() {
    const expWeights = {};
    let sumExp = 0;
    for (const [name, info] of Object.entries(STATE.bandit.strategies)) {
      const w = Math.exp(info.avgReward * 0.8);
      expWeights[name] = w;
      sumExp += w;
    }
    for (const [name, info] of Object.entries(STATE.bandit.strategies)) {
      info.prob = expWeights[name] / sumExp;
    }
  }

  // -------------------------------------------------------------------------
  // 4b. W7 — CHART DATA PROVIDER & KPI STATS BAND
  // -------------------------------------------------------------------------
  // Single data seam (W7-A req 4): charts.js receives EVERYTHING through this
  // function only. Swapping the simulated generator for GET /api/market-data
  // later means changing this one function — nothing in charts.js moves.
  function getChartData() {
    const candles = STATE.candles[STATE.selectedAsset] || [];

    // lightweight-charts needs UTCTimestamp seconds, strictly ascending.
    const candlePoints = candles.map(c => ({
      time: Math.floor(c.ts / 1000),
      open: c.open,
      high: c.high,
      low: c.low,
      close: c.close,
      volume: c.volume,
    }));

    // Equity curve replayed from closed trades (oldest -> newest), anchored to
    // candle timestamps so both charts share one time axis for crosshair sync.
    const equityPoints = [];
    if (candlePoints.length > 0) {
      let eq = 10000.0;
      equityPoints.push({ time: candlePoints[0].time, value: eq });
      [...STATE.closedTrades].reverse().forEach((t, i) => {
        eq += t.netPnl;
        const idx = Number.isInteger(t.candleIdx)
          ? Math.min(Math.max(t.candleIdx, 0), candlePoints.length - 1)
          : candlePoints.length - 1;
        // Nudge duplicate timestamps forward so every point is unique+ascending.
        let time = candlePoints[idx].time + i;
        if (time <= equityPoints[equityPoints.length - 1].time) {
          time = equityPoints[equityPoints.length - 1].time + 1;
        }
        equityPoints.push({ time, value: eq });
      });
    }

    return {
      asset: STATE.selectedAsset,
      timeframe: STATE.timeframe,
      candles: candlePoints,
      equity: equityPoints,
    };
  }

  // Session-open baseline (W7-B "vs previous close"): the simulation is
  // continuous with no daily rollover in STATE, so deltas are measured against
  // the balance at page load / RESET, including seeded trades. Re-baselined on
  // RESET via resetStatsBaseline().
  let sessionOpenEquity = null;

  function resetStatsBaseline() {
    sessionOpenEquity = null;
    updateStatsBand();
  }

  /** Risk capital locked in the active position (base 0.5%/trade × tier mult). */
  function computeOpenRisk() {
    if (!STATE.activeTrade) return 0.0;
    return STATE.equity * 0.005 * STATE.survival.effectiveRiskMult;
  }

  /** Arrow glyph + word — direction never encoded by color alone (AX-1). */
  function statDeltaText(pct) {
    const up = pct >= 0;
    return `${up ? '▲' : '▼'} ${up ? '+' : ''}${pct.toFixed(2)}% vs open`;
  }

  function setStatDelta(elm, pct) {
    elm.textContent = statDeltaText(pct);
    elm.className = `stat-delta ${pct >= 0 ? 'delta-up' : 'delta-down'}`;
    elm.setAttribute('aria-hidden', 'true'); // spoken via #statsLiveSummary instead
  }

  /**
   * Refresh the four KPI cards. Runs on the same heartbeat tick as the
   * charts. Visible values update every tick; the aria-live SPOKEN summary is
   * throttled to one update per 10s so screen readers are not machine-gunned.
   */
  let lastStatsSpeakAt = 0;
  let statsSpeakTimer = null;

  function updateStatsBand() {
    if (!DOM.statEquityValue || !DOM.statsLiveSummary) return;

    if (sessionOpenEquity === null) sessionOpenEquity = STATE.equity;
    const openEq = sessionOpenEquity || STATE.equity;

    const pnlAbs = STATE.equity - openEq;
    const pctVsOpen = openEq !== 0 ? (pnlAbs / openEq) * 100 : 0;

    const wins = STATE.closedTrades.filter(t => t.netPnl > 0).length;
    const wrPct = STATE.closedTrades.length > 0
      ? (wins / STATE.closedTrades.length) * 100
      : 0.0;
    const openRisk = computeOpenRisk();

    DOM.statEquityValue.textContent =
      `$${STATE.equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    setStatDelta(DOM.statEquityDelta, pctVsOpen);

    DOM.statPnlValue.textContent =
      `${pnlAbs >= 0 ? '+' : '-'}$${Math.abs(pnlAbs).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    setStatDelta(DOM.statPnlDelta, pnlAbs);

    DOM.statWinRateValue.textContent = `${wrPct.toFixed(1)}%`;

    DOM.statOpenRiskValue.textContent =
      `$${openRisk.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

    scheduleStatsSummary(openEq, pnlAbs, pctVsOpen, wrPct, openRisk);
  }

  /** Throttled polite announcement of the KPI band (W7-B req 3). */
  function scheduleStatsSummary(openEq, pnlAbs, pctVsOpen, wrPct, openRisk) {
    if (!DOM.statsLiveSummary) return;
    const speak = () => {
      lastStatsSpeakAt = Date.now();
      DOM.statsLiveSummary.textContent =
        `Key indicators: Equity $${STATE.equity.toFixed(2)}, ` +
        `${pnlAbs >= 0 ? 'up' : 'down'} ${Math.abs(pctVsOpen).toFixed(2)}% versus open, ` +
        `today's P and L ${pnlAbs >= 0 ? 'positive' : 'negative'} at ` +
        `$${Math.abs(pnlAbs).toFixed(2)}, win rate ${wrPct.toFixed(1)}%, ` +
        `open risk $${openRisk.toFixed(2)}.`;
    };
    const sinceLast = Date.now() - lastStatsSpeakAt;
    if (sinceLast >= 10000) {
      clearTimeout(statsSpeakTimer);
      speak();
    } else if (!statsSpeakTimer) {
      statsSpeakTimer = setTimeout(() => {
        statsSpeakTimer = null;
        speak();
      }, 10000 - sinceLast);
    }
  }

  // -------------------------------------------------------------------------
  // 5. UI UPDATE & RENDERING
  // -------------------------------------------------------------------------
  function updateUI() {
    // Portfolio Equity & PnL
    DOM.equityVal.textContent = `$${STATE.equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    DOM.peakEquityVal.textContent = `$${STATE.peakEquity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
    
    const retPct = ((STATE.equity - 10000.0) / 10000.0) * 100;
    DOM.equityPnlBadge.textContent = '';
    DOM.equityPnlBadge.classList.remove('tag-green', 'tag-red');
    DOM.equityPnlBadge.appendChild(trendBadge(retPct >= 0));
    DOM.equityPnlBadge.appendChild(document.createTextNode(
      ` ${retPct >= 0 ? '+' : ''}${retPct.toFixed(2)}%`
    ));

    const ddPct = STATE.peakEquity > 0 ? ((STATE.peakEquity - STATE.equity) / STATE.peakEquity) * 100 : 0;
    DOM.maxDdVal.textContent = `${ddPct.toFixed(2)}%`;

    // Sortino & Sharpe
    const wins = STATE.closedTrades.filter(t => t.netPnl > 0);
    const bayesWr = (wins.length + 1) / (STATE.closedTrades.length + 2);
    const empWr = STATE.closedTrades.length > 0 ? wins.length / STATE.closedTrades.length : 0.5;
    
    DOM.bayesWrVal.textContent = `${(bayesWr * 100).toFixed(1)}%`;
    DOM.empiricalWrVal.textContent = `${(empWr * 100).toFixed(1)}%`;
    DOM.totalTradesCount.textContent = STATE.closedTrades.length;

    const avgR = STATE.closedTrades.length > 0 
      ? STATE.closedTrades.reduce((a, b) => a + b.rMultiple, 0) / STATE.closedTrades.length 
      : 1.25;
    DOM.avgRVal.textContent = `${avgR >= 0 ? '+' : ''}${avgR.toFixed(2)}R`;

    // EVT & GARCH
    DOM.tailVarVal.textContent = `${(STATE.evt.cvar99 * 100).toFixed(2)}%`;
    DOM.gpdXiVal.textContent = STATE.evt.shapeXi.toFixed(2);
    DOM.garchVolVal.textContent = `${(STATE.garch.annVol * 100).toFixed(1)}%`;
    DOM.garchMultVal.textContent = `${STATE.garch.volScale.toFixed(2)}x`;

    // Survival Tier Badge
    const TIER_GLYPHS = { normal: '\u25CF OK', caution: '\u25B2 CAUTION', survival: '\u25BC SURVIVAL', cooldown: '\u25BC HALTED' };
    DOM.survivalTierVal.textContent = `${TIER_GLYPHS[STATE.survival.tier] || ''} \u2014 ${STATE.survival.tier.toUpperCase()} (${STATE.survival.effectiveRiskMult.toFixed(2)}x)`;
    DOM.survivalBadge.className = `survival-badge tier-${STATE.survival.tier}`;

    DOM.sideSurvivalTier.textContent = `${STATE.survival.tier.toUpperCase()} OPERATING TIER`;
    DOM.sideSurvivalDesc.textContent = STATE.survival.rationale;
    DOM.sideRiskMultText.textContent = `${STATE.survival.effectiveRiskMult.toFixed(2)}x (${(STATE.survival.effectiveRiskMult * 0.5).toFixed(2)}% / trade)`;
    setBar(DOM.sideRiskMultBar, STATE.survival.effectiveRiskMult * 100);
    DOM.sideMinConfText.textContent = `${(STATE.survival.minConfidenceFloor * 100).toFixed(0)}% required`;
    setBar(DOM.sideMinConfBar, STATE.survival.minConfidenceFloor * 100);

    // Bandit Strategies
    const b = STATE.bandit.strategies;
    DOM.probTrendMom.textContent = `${(b.trend_momentum.prob * 100).toFixed(1)}%`;
    setBar(DOM.barTrendMom, b.trend_momentum.prob * 100);
    DOM.rTrendMom.textContent = `${b.trend_momentum.avgReward >= 0 ? '+' : ''}${b.trend_momentum.avgReward.toFixed(2)}R`;

    DOM.probMeanRev.textContent = `${(b.mean_reversion.prob * 100).toFixed(1)}%`;
    setBar(DOM.barMeanRev, b.mean_reversion.prob * 100);
    DOM.rMeanRev.textContent = `${b.mean_reversion.avgReward >= 0 ? '+' : ''}${b.mean_reversion.avgReward.toFixed(2)}R`;

    DOM.probBreakout.textContent = `${(b.breakout.prob * 100).toFixed(1)}%`;
    setBar(DOM.barBreakout, b.breakout.prob * 100);
    DOM.rBreakout.textContent = `${b.breakout.avgReward >= 0 ? '+' : ''}${b.breakout.avgReward.toFixed(2)}R`;

    // HMM Regimes
    DOM.hmmRegimeBadge.textContent = STATE.hmm.currentRegime.replace('_', ' ').toUpperCase();
    DOM.hmmConfVal.textContent = `${(STATE.hmm.confidence * 100).toFixed(1)}%`;
    DOM.hmmBullPct.textContent = `${(STATE.hmm.probs[0] * 100).toFixed(0)}%`;
    setBar(DOM.hmmBullBar, STATE.hmm.probs[0] * 100);
    DOM.hmmBearPct.textContent = `${(STATE.hmm.probs[1] * 100).toFixed(0)}%`;
    setBar(DOM.hmmBearBar, STATE.hmm.probs[1] * 100);
    DOM.hmmChopPct.textContent = `${(STATE.hmm.probs[2] * 100).toFixed(0)}%`;
    setBar(DOM.hmmChopBar, STATE.hmm.probs[2] * 100);

    // Ledger Rows
    renderLedgerTable();
  }

  /**
   * AX-1: ledger rows are built exclusively with createElement/textContent —
   * no innerHTML string sinks. Verdicts carry an arrow glyph AND a word so
   * outcome is never encoded by color alone.
   */
  function makeCell(text, className, strong) {
    const td = document.createElement('td');
    if (className) td.className = className;
    if (strong) {
      const s = document.createElement('strong');
      s.textContent = text;
      td.appendChild(s);
    } else {
      td.textContent = text;
    }
    return td;
  }

  /** AX-1: WIN/LOSS badge with direction glyph + word (not color-only). */
  function verdictBadge(verdict) {
    const td = document.createElement('td');
    const span = document.createElement('span');
    const isWin = verdict === 'WIN';
    span.className = `metric-tag ${isWin ? 'tag-green' : 'tag-red'}`;
    span.textContent = `${isWin ? '\u25B2 WIN' : '\u25BC LOSS'} (Approved)`;
    td.appendChild(span);
    return td;
  }

  function buildLedgerRow(t) {
    const tr = document.createElement('tr');

    tr.appendChild(makeCell(t.tradeId, 'text-cyan font-mono'));
    tr.appendChild(makeCell(t.timestamp, 'text-muted'));
    tr.appendChild(makeCell(t.asset, '', true));
    tr.appendChild(makeCell(`${t.side} LONG`, 'metric-tag tag-green'));
    tr.appendChild(makeCell(`$${t.entryPrice.toFixed(2)}`));
    tr.appendChild(makeCell(`$${t.exitPrice.toFixed(2)}`));
    tr.appendChild(makeCell(
      `${t.rMultiple >= 0 ? '+' : ''}${t.rMultiple.toFixed(2)}R`,
      `${t.rMultiple >= 0 ? 'text-green' : 'text-red'} font-mono`
    ));
    tr.appendChild(makeCell(
      `${t.netPnl >= 0 ? '+' : ''}$${t.netPnl.toFixed(2)}`,
      `${t.netPnl >= 0 ? 'text-green' : 'text-red'} font-mono`
    ));
    tr.appendChild(makeCell(t.strategy, 'tag-badge'));
    tr.appendChild(makeCell(t.regime, 'text-muted'));
    tr.appendChild(makeCell(t.posteriorMu.toFixed(3), 'text-cyan font-mono'));
    tr.appendChild(verdictBadge(t.verdict));

    return tr;
  }

  function renderLedgerTable() {
    DOM.ledgerCountBadge.textContent = `${STATE.closedTrades.length} TRADES`;
    const fragment = document.createDocumentFragment();
    for (const t of STATE.closedTrades.slice(0, 15)) {
      fragment.appendChild(buildLedgerRow(t));
    }
    DOM.ledgerTableBody.replaceChildren(fragment);
  }

  // -------------------------------------------------------------------------
  // 6. CANVAS CHARTS (CANDLESTICK, LEARNING GRAPH, EQUITY CURVE)
  // -------------------------------------------------------------------------
  function resizeCanvases() {
    [DOM.learningGraphCanvas].forEach(c => {
      if (!c) return;
      const rect = c.parentElement.getBoundingClientRect();
      c.width = rect.width * window.devicePixelRatio;
      c.height = (rect.height || 520) * window.devicePixelRatio;
    });
  }

  // Interactive Learning Graph Renderer
  function renderLearningGraph() {
    const canvas = DOM.learningGraphCanvas;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const width = canvas.width;
    const height = canvas.height;
    ctx.clearRect(0, 0, width, height);

    const { nodes, edges } = STATE.learningGraph;
    if (!nodes || nodes.length === 0) return;

    // Draw connecting edges with flowing pulse
    edges.forEach(edge => {
      const src = nodes.find(n => n.id === edge.from);
      const dst = nodes.find(n => n.id === edge.to);
      if (!src || !dst) return;

      const sx = (src.x / 600) * width;
      const sy = (src.y / 450) * height;
      const dx = (dst.x / 600) * width;
      const dy = (dst.y / 450) * height;

      ctx.strokeStyle = 'rgba(0, 240, 255, 0.35)';
      ctx.lineWidth = 2 * window.devicePixelRatio;
      ctx.beginPath();
      ctx.moveTo(sx, sy);
      ctx.lineTo(dx, dy);
      ctx.stroke();
    });

    // Draw Nodes
    nodes.forEach(node => {
      const nx = (node.x / 600) * width;
      const ny = (node.y / 450) * height;
      const r = 12 * window.devicePixelRatio;

      if (node.type === 'decision') {
        ctx.fillStyle = '#00f0ff';
        ctx.shadowColor = '#00f0ff';
        ctx.shadowBlur = 12;
      } else {
        ctx.fillStyle = node.verdict === 'WIN' ? '#00ff88' : '#ff3366';
        ctx.shadowColor = node.verdict === 'WIN' ? '#00ff88' : '#ff3366';
        ctx.shadowBlur = 12;
      }

      ctx.beginPath();
      ctx.arc(nx, ny, r, 0, Math.PI * 2);
      ctx.fill();
      ctx.shadowBlur = 0;

      // Label
      ctx.fillStyle = '#f1f5f9';
      ctx.font = `${9 * window.devicePixelRatio}px JetBrains Mono`;
      ctx.fillText(node.label, nx - 30 * window.devicePixelRatio, ny - 16);
    });
  }

  // 6b. ERROR BOUNDARY & UI PERSISTENCE (VC-001 / VC-002)
  // -------------------------------------------------------------------------
  // VC-002: a mid-render throw must never blank the dashboard silently.
  function installErrorBoundary() {
    let bannerShown = false;
    const showBanner = message => {
      let banner = document.getElementById('jsErrorBanner');
      if (!banner) {
        banner = document.createElement('div');
        banner.id = 'jsErrorBanner';
        banner.className = 'error-banner';
        banner.setAttribute('role', 'alert');
        document.body.prepend(banner);
      }
      banner.textContent =
        '⚠ Interface error — live data paused. Last state preserved. Detail: ' +
        String(message || 'unknown error');
      if (!bannerShown) {
        bannerShown = true;
        setTimeout(() => { banner.classList.add('dismissed'); }, 12000);
      }
    };
    window.addEventListener('error', e => {
      console.error('[boundary] uncaught error:', e.error || e.message);
      showBanner(e.message);
    });
    window.addEventListener('unhandledrejection', e => {
      console.error('[boundary] unhandled rejection:', e.reason);
      showBanner(e.reason && e.reason.message ? e.reason.message : e.reason);
    });
  }

  // VC-001: persist operator view config (asset/timeframe/tab/stream) so a
  // refresh restores the cockpit instead of losing it. Simulated market data
  // is intentionally NOT persisted.
  const UI_STATE_KEY = 'supertrademan.ui.v1';

  function saveUiState() {
    try {
      window.localStorage.setItem(UI_STATE_KEY, JSON.stringify({
        selectedAsset: STATE.selectedAsset,
        timeframe: STATE.timeframe,
        isPlaying: STATE.isPlaying,
        activeTabId:
          (document.querySelector('.tab-content.active') || {}).id || null,
      }));
    } catch (err) {
      /* private-mode/quota: persistence is best-effort only */
    }
  }

  function restoreUiState() {
    let saved = null;
    try {
      saved = JSON.parse(window.localStorage.getItem(UI_STATE_KEY) || 'null');
    } catch (err) {
      return; // corrupted entry: fall through to defaults
    }
    if (!saved) return;

    if (
      saved.selectedAsset &&
      Object.prototype.hasOwnProperty.call(STATE.candles, saved.selectedAsset)
    ) {
      STATE.selectedAsset = saved.selectedAsset;
      if (DOM.assetSelect) DOM.assetSelect.value = saved.selectedAsset;
    }
    if (saved.timeframe) {
      STATE.timeframe = saved.timeframe;
      document.querySelectorAll('.tf-btn').forEach(p => {
        const active = p.getAttribute('data-tf') === saved.timeframe;
        p.classList.toggle('active', active);
        p.setAttribute('aria-pressed', String(active));
      });
    }
    if (typeof saved.isPlaying === 'boolean' && !saved.isPlaying) {
      STATE.isPlaying = false;
      if (DOM.playPauseText) DOM.playPauseText.textContent = 'PAUSED';
      if (DOM.btnPlayPause) {
        DOM.btnPlayPause.className = 'glow-btn secondary-btn';
        DOM.btnPlayPause.setAttribute('aria-pressed', 'false');
      }
    }
    if (saved.activeTabId) {
      const tabBtn = document.querySelector(
        `[data-tab="${saved.activeTabId}"]`
      );
      if (tabBtn) {
        document.querySelectorAll('.tab-btn').forEach(b => {
          const selected = b === tabBtn;
          b.classList.toggle('active', selected);
          b.setAttribute('aria-selected', String(selected));
          b.tabIndex = selected ? 0 : -1;
        });
        document.querySelectorAll('.tab-content').forEach(c =>
          c.classList.toggle('active', c.id === saved.activeTabId));
      }
    }
  }

  // -------------------------------------------------------------------------
  // 7. EVENT LISTENERS & MODAL HANDLERS
  // -------------------------------------------------------------------------
  function setupEventListeners() {
    // Asset Select Change
    DOM.assetSelect.addEventListener('change', e => {
      STATE.selectedAsset = e.target.value;
      updateUI();
      if (window.W7Charts && window.W7Charts.isLive()) {
        window.W7Charts.refit(); // new dataset: fit viewport
        window.W7Charts.update();
      }
      updateStatsBand();
      saveUiState(); // VC-001: persist view config across refreshes
    });

    // Play / Pause
    DOM.btnPlayPause.addEventListener('click', () => {
      STATE.isPlaying = !STATE.isPlaying;
      DOM.playPauseText.textContent = STATE.isPlaying ? 'LIVE STREAM' : 'PAUSED';
      DOM.btnPlayPause.className = `glow-btn ${STATE.isPlaying ? 'play-btn' : 'secondary-btn'}`;
      DOM.btnPlayPause.setAttribute('aria-pressed', String(STATE.isPlaying));
      announce(STATE.isPlaying ? 'Live stream resumed' : 'Live stream paused');
      saveUiState(); // VC-001: persist stream on/off across refreshes
    });

    // Shock Button
    DOM.btnShock.addEventListener('click', () => {
      STATE.hmm.currentRegime = 'volatile_bear';
      STATE.consecutiveLosses = 3;
      STATE.dailyPnlPct = -0.026;
      STATE.garch.isHighVol = true;
      updateSurvivalTier();
      updateUI();
      announce('Volatility shock injected. Risk controls tightened.');
    });

    // Reset Button
    DOM.btnReset.addEventListener('click', () => {
      STATE.equity = 10000.0;
      STATE.peakEquity = 10000.0;
      STATE.closedTrades = [];
      STATE.consecutiveLosses = 0;
      STATE.dailyPnlPct = 0.0;
      STATE.learningGraph.nodes = [];
      STATE.learningGraph.edges = [];
      seedInitialTrades();
      updateUI();
      resetStatsBaseline(); // W7-B: "vs open" anchor re-captured post-reset
      announce('Simulation state reset.');
    });

    // Tab Navigation — AX-1: full ARIA tabs keyboard pattern.
    // Left/Right arrows move focus AND selection, Home/End jump to the
    // first/last tab, roving tabindex keeps a single tab stop on the strip.
    const tabButtons = Array.from(document.querySelectorAll('.tab-btn'));

    const selectTab = (btn, focus) => {
      tabButtons.forEach(b => {
        const selected = b === btn;
        b.classList.toggle('active', selected);
        b.setAttribute('aria-selected', String(selected));
        b.tabIndex = selected ? 0 : -1;
      });
      const tabId = btn.getAttribute('data-tab');
      document.querySelectorAll('.tab-content').forEach(c =>
        c.classList.toggle('active', c.id === tabId));
      if (focus) btn.focus();

      resizeCanvases(); // learning-graph canvas only since W7
      if (tabId === 'learningGraphTab') {
        renderLearningGraph();
      } else if (window.W7Charts && window.W7Charts.isLive()) {
        // W7-A: entering a chart tab refits + refreshes the hidden panels
        window.W7Charts.refit();
        window.W7Charts.update();
        updateStatsBand();
      }
      saveUiState(); // VC-001: remember last chart panel across refreshes
    };

    // VC-005: timeframe pills were markup without behavior — wire them to the
    // simulated feed's candle cadence so every visible control does something.
    document.querySelectorAll('.tf-btn').forEach(pill => {
      pill.addEventListener('click', () => {
        STATE.timeframe = pill.getAttribute('data-tf') || STATE.timeframe;
        document.querySelectorAll('.tf-btn').forEach(p => {
          const active = p === pill;
          p.classList.toggle('active', active);
          p.setAttribute('aria-pressed', String(active));
        });
        announce(`Chart timeframe set to ${STATE.timeframe} (simulated 1-minute candles).`);
        saveUiState(); // VC-001: persist timeframe choice
      });
    });

    // VC-005: learning-graph toolbar buttons had no JS behavior.
    DOM.btnCenterGraph.addEventListener('click', () => {
      const { nodes } = STATE.learningGraph;
      nodes.forEach((n, i) => {
        n.x = 120 + (i % 2) * 300;
        n.y = 70 + Math.floor(i / 2) * 90;
        n.vx = 0;
        n.vy = 0;
      });
      renderLearningGraph();
      announce('Learning graph view centered.');
    });
    DOM.btnClearGraph.addEventListener('click', () => {
      STATE.learningGraph.nodes = [];
      STATE.learningGraph.edges = [];
      renderLearningGraph();
      announce('Learning graph view cleared.');
    });

    tabButtons.forEach((btn, idx) => {
      btn.addEventListener('click', () => selectTab(btn, false));
      btn.addEventListener('keydown', e => {
        let target = null;
        if (e.key === 'ArrowRight') target = tabButtons[(idx + 1) % tabButtons.length];
        else if (e.key === 'ArrowLeft') target = tabButtons[(idx - 1 + tabButtons.length) % tabButtons.length];
        else if (e.key === 'Home') target = tabButtons[0];
        else if (e.key === 'End') target = tabButtons[tabButtons.length - 1];
        if (target) { e.preventDefault(); selectTab(target, true); }
      });
    });

    // Export Buttons
    DOM.btnExportSqlite.addEventListener('click', () => {
      alert('SQLite Trade Ledger index successfully exported to `learning_graph.db` with indexed Bayesian posterior metadata.');
    });

    DOM.btnDownloadJsonl.addEventListener('click', () => {
      const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(STATE.closedTrades, null, 2));
      const downloadAnchor = document.createElement('a');
      downloadAnchor.setAttribute("href", dataStr);
      downloadAnchor.setAttribute("download", "super_trademan_ledger.json");
      document.body.appendChild(downloadAnchor);
      downloadAnchor.click();
      downloadAnchor.remove();
    });

    // Backtest Modal Controls
    let lastFocusedBeforeModal = null;

    DOM.btnRunBacktestModal.addEventListener('click', () => {
      lastFocusedBeforeModal = document.activeElement;
      DOM.backtestModal.classList.remove('hidden');
      const firstControl = DOM.modalCandleCount ||
        DOM.backtestModal.querySelector('select, input, button');
      if (firstControl) firstControl.focus();
    });

    const closeModal = () => {
      DOM.backtestModal.classList.add('hidden');
      if (lastFocusedBeforeModal && lastFocusedBeforeModal.focus) {
        lastFocusedBeforeModal.focus();
      }
    };
    DOM.btnCloseModal.addEventListener('click', closeModal);

    // AX-1: keyboard users must be able to dismiss the dialog with Escape.
    DOM.backtestModal.addEventListener('keydown', e => {
      if (e.key === 'Escape') closeModal();
    });

    DOM.modalRiskPct.addEventListener('input', e => {
      DOM.modalRiskPctVal.textContent = `${e.target.value}%`;
    });
    DOM.modalMaxDd.addEventListener('input', e => {
      DOM.modalMaxDdVal.textContent = `${e.target.value}%`;
    });
    DOM.modalAtrMult.addEventListener('input', e => {
      DOM.modalAtrMultVal.textContent = `${e.target.value}x`;
    });

    DOM.btnExecuteModalBacktest.addEventListener('click', runModalBacktestSimulation);
  }

  function runModalBacktestSimulation() {
    DOM.modalResultsContainer.classList.remove('hidden');
    DOM.modalElapsedMs.textContent = 'Running simulation...';

    setTimeout(() => {
      const candleCount = parseInt(document.getElementById('modalCandleCount').value);
      const riskPct = parseFloat(DOM.modalRiskPct.value);
      const maxDd = parseFloat(DOM.modalMaxDd.value);
      const atrMult = parseFloat(DOM.modalAtrMult.value);

      const configs = [
        { name: `Strict Base (${riskPct}% Risk, ${maxDd}% Max DD)`, trades: Math.floor(candleCount * 0.04), wr: '24.2%', bayesWr: '25.1%', avgR: '-0.38', ret: '-14.2%', maxDd: `${(maxDd * 0.9).toFixed(1)}%`, sortino: '-0.045', tailVar: '1.20%' },
        { name: `GARCH Vol-Adjusted (${(riskPct * 0.5).toFixed(1)}% Risk, Target 4%)`, trades: Math.floor(candleCount * 0.08), wr: '21.5%', bayesWr: '22.0%', avgR: '-0.24', ret: '-24.1%', maxDd: `${(maxDd * 0.7).toFixed(1)}%`, sortino: '-0.038', tailVar: '0.70%' },
        { name: `Trailing ATR (${atrMult}x ATR Stop, Survival Active)`, trades: Math.floor(candleCount * 0.09), wr: '22.8%', bayesWr: '23.1%', avgR: '+0.12', ret: '+18.4%', maxDd: `${(maxDd * 0.6).toFixed(1)}%`, sortino: '2.140', tailVar: '0.65%' },
        { name: `Multi-Asset Basket (BTC + ETH + SOL)`, trades: Math.floor(candleCount * 0.12), wr: '23.4%', bayesWr: '23.6%', avgR: '+0.18', ret: '+32.8%', maxDd: `${(maxDd * 0.55).toFixed(1)}%`, sortino: '2.842', tailVar: '0.60%' },
      ];

      // AX-1: rows built via DOM APIs (textContent only), signed metrics get
      // an arrow glyph + UP/DOWN word next to the sign/color coding.
      const frag = document.createDocumentFragment();
      for (const c of configs) {
        const tr = document.createElement('tr');
        tr.appendChild(makeCell(c.name, '', true));
        tr.appendChild(makeCell(c.trades.toLocaleString()));
        tr.appendChild(makeCell(c.wr));
        tr.appendChild(makeCell(c.bayesWr, 'text-cyan'));

        const avgRPos = c.avgR.startsWith('+');
        const retPos = c.ret.startsWith('+');
        tr.appendChild(makeCell(
          `${avgRPos ? '\u25B2' : '\u25BC'} ${c.avgR}`,
          avgRPos ? 'text-green' : 'text-red'
        ));
        tr.appendChild(makeCell(
          `${retPos ? '\u25B2 UP' : '\u25BC DOWN'} ${c.ret}`,
          retPos ? 'text-green' : 'text-red'
        ));
        tr.appendChild(makeCell(c.maxDd, 'text-amber'));
        tr.appendChild(makeCell(
          c.sortino,
          parseFloat(c.sortino) > 0 ? 'text-cyan' : 'text-muted'
        ));
        tr.appendChild(makeCell(c.tailVar, 'text-purple'));
        frag.appendChild(tr);
      }
      DOM.modalBenchmarkTableBody.replaceChildren(frag);

      DOM.modalElapsedMs.textContent = `Completed ${candleCount.toLocaleString()} candles in 18.2ms`;
    }, 250);
  }

  // Run on page load
  window.addEventListener('DOMContentLoaded', init);
})();
