(() => {
  "use strict";

  const app = document.querySelector("#forecastApp");
  const sourceMode = new URLSearchParams(window.location.search).get("source") === "live" ? "live" : "demo";
  const DATA_URL = sourceMode === "live" ? "./data/forecasts-v1.json" : "./demo/forecasts-v1.json";
  const sourceQuery = sourceMode === "live" ? "&source=live" : "";
  const PAGE_SIZE = 50;

  const state = {
    data: null,
    loading: true,
    refreshing: false,
    error: null,
    market: "cn",
    query: "",
    sector: "all",
    status: "all",
    sort: "reliability",
    page: 1,
    selectedByMarket: { cn: null, us: null },
    chartCache: new Map(),
    chartLoading: new Set(),
  };

  const icons = {
    refresh: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M20 7v5h-5M4 17v-5h5" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"></path>
        <path d="M6.1 8.5A7 7 0 0 1 18.7 7L20 12M4 12l1.3 5A7 7 0 0 0 17.9 15.5" fill="none" stroke="currentColor" stroke-linecap="round"></path>
      </svg>`,
    search: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="10.8" cy="10.8" r="6.2" fill="none" stroke="currentColor" stroke-width="1.8"></circle>
        <path d="m15.4 15.4 4.2 4.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"></path>
      </svg>`,
    alert: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M12 3 2.7 19h18.6L12 3Z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"></path>
        <path d="M12 8v5M12 16.8v.2" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"></path>
      </svg>`,
    database: `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <ellipse cx="12" cy="5.5" rx="7.5" ry="3" fill="none" stroke="currentColor" stroke-width="1.6"></ellipse>
        <path d="M4.5 5.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6M4.5 11.5v6c0 1.7 3.4 3 7.5 3s7.5-1.3 7.5-3v-6" fill="none" stroke="currentColor" stroke-width="1.6"></path>
      </svg>`,
  };

  const esc = (value) =>
    String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

  const finite = (value) => {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  };

  const clamp = (value, min, max) => Math.max(min, Math.min(max, value));

  const firstValue = (object, paths, fallback = null) => {
    for (const path of paths) {
      const value = path.split(".").reduce((current, key) => current?.[key], object);
      if (value !== null && value !== undefined && value !== "") return value;
    }
    return fallback;
  };

  const arrayValue = (value) => (Array.isArray(value) ? value : []);

  const formatNumber = (value, digits = 2, fallback = "-") => {
    const parsed = finite(value);
    if (parsed === null) return fallback;
    return parsed.toLocaleString("zh-CN", {
      minimumFractionDigits: digits,
      maximumFractionDigits: digits,
    });
  };

  const formatInteger = (value, fallback = "-") => {
    const parsed = finite(value);
    return parsed === null ? fallback : Math.round(parsed).toLocaleString("zh-CN");
  };

  const asProbability = (value) => {
    const parsed = finite(value);
    if (parsed === null) return null;
    return clamp(Math.abs(parsed) > 1 ? parsed / 100 : parsed, 0, 1);
  };

  const formatProbability = (value, digits = 1) => {
    const parsed = asProbability(value);
    return parsed === null ? "-" : `${(parsed * 100).toFixed(digits)}%`;
  };

  const formatReturn = (value, digits = 2) => {
    const parsed = finite(value);
    if (parsed === null) return "-";
    const percent = parsed * 100;
    return `${percent > 0 ? "+" : ""}${percent.toFixed(digits)}%`;
  };

  const returnClass = (value) => {
    const parsed = finite(value);
    if (parsed === null || parsed === 0) return "";
    return parsed > 0 ? "positive" : "negative";
  };

  const formatMetric = (value, type = "number") => {
    if (type === "probability") return formatProbability(value, 1);
    if (type === "coverage") return formatProbability(value, 1);
    if (type === "integer") return formatInteger(value);
    return formatNumber(value, 3);
  };

  const marketLabel = (market) => (market === "us" ? "美股预测" : "A股预测");
  const marketCurrency = (market) => (market === "us" ? "$" : "¥");

  const formatPrice = (value, market = state.market) => {
    const parsed = finite(value);
    if (parsed === null) return "-";
    return `${marketCurrency(market)}${parsed.toLocaleString("zh-CN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  };

  const statusTone = (status) => {
    const text = String(status || "").toLowerCase();
    if (/拒绝|不通过|失败|error|reject|blocked/.test(text)) return "bad";
    if (/观察|谨慎|待|暂|积累|warn|shadow/.test(text)) return "warn";
    if (/通过|正常|良好|优秀|pass|good|ok|active/.test(text)) return "good";
    return "";
  };

  const isRejected = (forecast) =>
    String(forecast?.decision_status || "") !== "可研究" ||
    arrayValue(forecast?.abstain_reasons).length > 0;

  const marketFromURL = () => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("market") === "us") return "us";
    if (params.get("market") === "cn") return "cn";
    if (params.get("mode") === "a-share") return "cn";
    return "cn";
  };

  const writeMarketURL = (market, replace = false) => {
    const url = new URL(window.location.href);
    url.searchParams.delete("mode");
    url.searchParams.delete("desktop");
    url.searchParams.set("market", market);
    const method = replace ? "replaceState" : "pushState";
    window.history[method]({ market }, "", `${url.pathname}${url.search}${url.hash}`);
  };

  const setTheme = () => {
    document.body.dataset.market = state.market;
    document.documentElement.style.colorScheme = state.market === "us" ? "dark" : "light";
    document.title = `${marketLabel(state.market)} · 市场预测 V1`;
  };

  const normalizeForecast = (forecast) => ({
    ...forecast,
    symbol: String(forecast?.symbol || ""),
    name: String(forecast?.name || ""),
    sector: String(forecast?.sector || "未分类"),
    exchange: String(forecast?.exchange || ""),
    board: String(forecast?.board || ""),
    abstain_reasons: arrayValue(forecast?.abstain_reasons),
    evidence_gaps: arrayValue(forecast?.evidence_gaps),
    execution_restrictions: arrayValue(forecast?.execution_restrictions),
    component_predictions: arrayValue(forecast?.component_predictions),
    factor_contributions: arrayValue(forecast?.factor_contributions),
    quantiles: forecast?.quantiles || {},
  });

  const diagnosticScore = (forecast) => {
    const breakdown = forecast?.diagnostic_score_breakdown || forecast?.research_readiness;
    const score = finite(breakdown?.score);
    const maxScore = finite(breakdown?.max_score);
    if (score !== null && maxScore !== null && maxScore > 0) {
      return clamp((score / maxScore) * 100, 0, 100);
    }
    return finite(forecast?.reliability_score);
  };

  const currentMarket = () => state.data?.markets?.[state.market] || null;

  const allForecasts = () =>
    arrayValue(currentMarket()?.forecasts)
      .map(normalizeForecast)
      .filter((forecast) => forecast.symbol || forecast.name);

  const availableSectors = () =>
    [...new Set(allForecasts().map((forecast) => forecast.sector).filter(Boolean))].sort((left, right) =>
      left.localeCompare(right, "zh-CN"),
    );

  const sortForecasts = (forecasts) => {
    const rows = [...forecasts];
    const value = (forecast) => {
      if (state.sort === "probability") return asProbability(forecast.probability_up) ?? -Infinity;
      if (state.sort === "return") return finite(forecast.expected_return) ?? -Infinity;
      if (state.sort === "symbol") return forecast.symbol;
      return diagnosticScore(forecast) ?? -Infinity;
    };
    rows.sort((left, right) => {
      if (state.sort === "symbol") return String(value(left)).localeCompare(String(value(right)));
      return Number(value(right)) - Number(value(left));
    });
    return rows;
  };

  const filteredForecasts = () => {
    const query = state.query.trim().toLowerCase();
    const rows = allForecasts().filter((forecast) => {
      if (query && !`${forecast.symbol} ${forecast.name}`.toLowerCase().includes(query)) return false;
      if (state.sector !== "all" && forecast.sector !== state.sector) return false;
      if (state.status !== "all" && readinessBand(diagnosticScore(forecast)).id !== state.status) return false;
      return true;
    });
    return sortForecasts(rows);
  };

  const selectedForecast = () => {
    const forecasts = allForecasts();
    const selected = state.selectedByMarket[state.market];
    return forecasts.find((forecast) => forecast.symbol === selected) || forecasts[0] || null;
  };

  const ensureSelection = () => {
    const forecasts = allForecasts();
    const current = state.selectedByMarket[state.market];
    if (!forecasts.some((forecast) => forecast.symbol === current)) {
      state.selectedByMarket[state.market] = forecasts[0]?.symbol || null;
    }
  };

  const universeInfo = (market) => {
    const universe = market?.universe;
    if (finite(universe) !== null) {
      return { value: `${formatInteger(universe)} 只`, sub: state.market === "cn" ? "全部A股板块" : "美股覆盖池" };
    }
    const total = firstValue(universe, ["total", "count", "size", "eligible_count"], allForecasts().length);
    const covered = firstValue(universe, ["covered", "forecast_count", "covered_count"], allForecasts().length);
    const label = firstValue(universe, ["label", "description", "scope"], state.market === "cn" ? "全部A股板块" : "美股覆盖池");
    return {
      value: total === null ? String(label) : `${formatInteger(total)} 只`,
      sub: covered !== null && finite(covered) !== null ? `${formatInteger(covered)} 已生成预测 · ${label}` : String(label),
    };
  };

  const sessionInfo = (market) => {
    const session = market?.session || {};
    const horizon = firstValue(session, ["horizon_label", "horizon", "primary_horizon"], firstValue(state.data?.model, ["primary_horizon", "horizon", "primary_horizon_days"], 5));
    const start = firstValue(session, ["target_start", "start", "from"], "");
    const end = firstValue(session, ["target_end", "end", "to"], "");
    return {
      value: typeof horizon === "number" || /^\d+$/.test(String(horizon)) ? `未来 ${horizon} 个交易日` : String(horizon || "未来 5 个交易日"),
      sub: start && end ? `${start} → ${end}` : String(firstValue(session, ["description", "entry_rule"], "固定主终点，其他周期仅供诊断")),
    };
  };

  const validationInfo = (market) => {
    const validation = market?.validation || {};
    const reviewCadence = firstValue(
      state.data?.improvement,
      ["review_cadence"],
      "每次刷新先结算到期样本，再滚动复评",
    );
    return {
      status: firstValue(validation, ["status", "sample_status", "grade"], "待验证"),
      statusDetail: firstValue(validation, ["status_detail", "message", "method"], "Walk-forward 样本外验证"),
      nextReview: firstValue(
        validation,
        ["next_review", "next_review_date"],
        firstValue(state.data?.improvement, ["next_review", "next_review_date"], "每周滚动复评"),
      ),
      nextReviewDetail: firstValue(validation, ["next_review_detail"], reviewCadence),
      brier: firstValue(validation, ["brier", "brier_score", "metrics.brier"], null),
      brierSkill: firstValue(validation, ["brier_skill", "metrics.brier_skill"], null),
      returnSkill: firstValue(validation, ["return_skill", "metrics.return_skill"], null),
      calibration: firstValue(validation, ["calibration_error", "ece", "metrics.calibration_error"], null),
      coverage: firstValue(
        validation,
        ["empirical_interval_coverage", "coverage", "interval_coverage", "metrics.coverage"],
        null,
      ),
      sampleCount: firstValue(validation, ["sample_count", "samples", "oos_samples"], null),
    };
  };

  const resolveLedger = () => {
    const market = currentMarket() || {};
    const improvement = state.data?.improvement || {};
    return (
      market.forward_ledger ||
      market.forward_validation ||
      market.validation?.forward_ledger ||
      improvement?.markets?.[state.market] ||
      improvement?.forward_ledger?.[state.market] ||
      improvement?.ledger?.[state.market] ||
      improvement?.ledger_summary?.[state.market] ||
      improvement?.[state.market] ||
      improvement
    );
  };

  const modelVersion = () => firstValue(state.data?.model, ["version", "model_version", "name"], "-");

  const renderHeader = () => `
    <header class="app-header">
      <div class="brand">
        <h1>市场预测</h1>
        <span class="brand-version">V1 · ${esc(modelVersion())}</span>
      </div>
      <div class="market-switch" role="tablist" aria-label="预测市场">
        ${["cn", "us"]
          .map(
            (market) => `
              <button
                class="market-tab ${state.market === market ? "active" : ""}"
                type="button"
                role="tab"
                aria-selected="${state.market === market ? "true" : "false"}"
                data-market-tab="${market}"
              >${market === "cn" ? "A股预测" : "美股预测"}</button>
            `,
          )
          .join("")}
      </div>
      <div class="header-actions">
        <nav class="view-switcher" data-view="list" aria-label="页面切换">
          <a class="view-switch-link active" href="./index.html?market=${state.market}${sourceQuery}" aria-current="page">预测列表</a>
          <a class="view-switch-link" href="./model-3d.html?market=${state.market}${sourceQuery}">三维模型</a>
        </nav>
        <button class="refresh-button" type="button" data-refresh ${state.refreshing ? "disabled" : ""}>
          ${state.refreshing ? '<span class="refresh-spinner" aria-hidden="true"></span>' : icons.refresh}
          <span>${state.refreshing ? "读取中" : "读取最新"}</span>
        </button>
        <span class="data-cutoff">预测生成于 ${esc(state.data?.generated_at || "-")}</span>
      </div>
    </header>
  `;

  const renderSummary = () => {
    const market = currentMarket();
    const universe = universeInfo(market);
    const session = sessionInfo(market);
    const validation = validationInfo(market);
    const firstForecast = allForecasts()[0];
    const readiness = firstForecast ? readinessBreakdown(firstForecast) : null;
    const modelEvidence = arrayValue(readiness?.dimensions).find((dimension) => dimension.id === "model_evidence");
    const evidenceScore = finite(modelEvidence?.score);
    const evidenceMax = finite(modelEvidence?.max_score);
    const evidencePercent =
      evidenceScore !== null && evidenceMax && evidenceMax > 0 ? (evidenceScore / evidenceMax) * 100 : null;
    const evidenceTone = evidencePercent === null ? "" : evidencePercent >= 70 ? "good" : evidencePercent >= 40 ? "warn" : "bad";
    return `
      <section class="summary-panel" aria-label="${esc(marketLabel(state.market))}概况">
        <div class="summary-item">
          <span class="summary-label">预测范围</span>
          <strong class="summary-value">${esc(universe.value)}</strong>
          <span class="summary-sub">${esc(universe.sub)}</span>
        </div>
        <div class="summary-item">
          <span class="summary-label">预测周期</span>
          <strong class="summary-value">${esc(session.value)}</strong>
          <span class="summary-sub">${esc(session.sub)}</span>
        </div>
        <div class="summary-item">
          <span class="summary-label">市场模型证据</span>
          <strong class="summary-value ${evidenceTone ? `status-${evidenceTone}` : ""}">${
            evidenceScore === null || evidenceMax === null
              ? "-"
              : `${formatNumber(evidenceScore, 1)} / ${formatNumber(evidenceMax, 0)}`
          }</strong>
          <span class="summary-sub">${esc(validation.status)} · 方向、收益、校准与区间</span>
        </div>
        <div class="summary-item">
          <span class="summary-label">下次复评</span>
          <strong class="summary-value">${esc(validation.nextReview)}</strong>
          <span class="summary-sub">${esc(validation.nextReviewDetail)}</span>
        </div>
        <div class="summary-item validation-item">
          <span class="summary-label">样本外综合表现</span>
          <div class="validation-summary">
            <div class="validation-mini">
              <span>方向 Brier 增益</span>
              <strong class="${returnClass(validation.brierSkill)}">${formatReturn(validation.brierSkill, 1)}</strong>
            </div>
            <div class="validation-mini">
              <span>收益 MAE 增益</span>
              <strong class="${returnClass(validation.returnSkill)}">${formatReturn(validation.returnSkill, 1)}</strong>
            </div>
            <div class="validation-mini">
              <span>校准误差</span>
              <strong>${formatMetric(validation.calibration)}</strong>
            </div>
            <div class="validation-mini">
              <span>区间覆盖率</span>
              <strong>${formatMetric(validation.coverage, "coverage")}</strong>
            </div>
          </div>
        </div>
      </section>
    `;
  };

  const renderFilters = () => `
    <div class="section-toolbar">
      <h2 class="section-title">预测列表</h2>
      <div class="filter-grid">
        <label class="search-field">
          <span class="sr-only">搜索代码或名称</span>
          ${icons.search}
          <input
            class="filter-control"
            type="search"
            inputmode="search"
            value="${esc(state.query)}"
            placeholder="搜索代码或名称"
            data-filter-query
          />
        </label>
        <label>
          <span class="sr-only">${state.market === "cn" ? "板块" : "行业"}</span>
          <select class="filter-control" data-filter-sector>
            <option value="all">全部${state.market === "cn" ? "板块" : "行业"}</option>
            ${availableSectors()
              .map((sector) => `<option value="${esc(sector)}" ${state.sector === sector ? "selected" : ""}>${esc(sector)}</option>`)
              .join("")}
          </select>
        </label>
        <label>
          <span class="sr-only">综合诊断分</span>
          <select class="filter-control" data-filter-status>
            <option value="all">全部诊断分</option>
            <option value="high" ${state.status === "high" ? "selected" : ""}>70–100 · 较高</option>
            <option value="medium" ${state.status === "medium" ? "selected" : ""}>58–69 · 中等</option>
            <option value="low" ${state.status === "low" ? "selected" : ""}>40–57 · 偏低</option>
            <option value="very-low" ${state.status === "very-low" ? "selected" : ""}>0–39 · 低</option>
          </select>
        </label>
        <label>
          <span class="sr-only">排序方式</span>
          <select class="filter-control" data-filter-sort>
            <option value="reliability" ${state.sort === "reliability" ? "selected" : ""}>按综合诊断分</option>
            <option value="probability" ${state.sort === "probability" ? "selected" : ""}>按上涨概率</option>
            <option value="return" ${state.sort === "return" ? "selected" : ""}>按预期收益</option>
            <option value="symbol" ${state.sort === "symbol" ? "selected" : ""}>按代码</option>
          </select>
        </label>
      </div>
      <div class="toolbar-legend" aria-label="上涨概率色标">
        <div class="toolbar-legend-head"><span>5日上涨概率（校准后）</span><span>高</span></div>
        <div class="probability-scale"></div>
        <div class="probability-scale-labels"><span>0%</span><span>50%</span><span>100%</span></div>
      </div>
    </div>
  `;

  const reliabilityDisplay = (value) => {
    const parsed = finite(value);
    if (parsed === null) return { score: "-", normalized: 0 };
    return {
      score: parsed.toFixed(1),
      normalized: clamp(parsed / 100, 0, 1),
    };
  };

  const readinessBand = (value) => {
    const parsed = finite(value);
    if (parsed === null) return { id: "unavailable", label: "未计算" };
    if (parsed >= 70) return { id: "high", label: "较高" };
    if (parsed >= 58) return { id: "medium", label: "中等" };
    if (parsed >= 40) return { id: "low", label: "偏低" };
    return { id: "very-low", label: "低" };
  };

  const isExecutionRestriction = (reason) =>
    /冻结窗口|信号日尚未收盘|不进入前向账本/.test(String(reason || ""));

  const evidenceGaps = (forecast) => {
    const explicit = arrayValue(forecast?.evidence_gaps);
    if (explicit.length) return explicit;
    return arrayValue(forecast?.abstain_reasons).filter((reason) => !isExecutionRestriction(reason));
  };

  const executionRestrictions = (forecast) => {
    const explicit = arrayValue(forecast?.execution_restrictions);
    if (explicit.length) return explicit;
    return arrayValue(forecast?.abstain_reasons).filter(isExecutionRestriction);
  };

  const readinessBreakdown = (forecast) => {
    const explicit = forecast?.diagnostic_score_breakdown || forecast?.research_readiness;
    if (explicit && arrayValue(explicit.dimensions).length) return explicit;
    const validation = forecast?.validation || {};
    const brierSkill = finite(validation.brier_skill) ?? 0;
    const returnSkill = finite(validation.return_skill) ?? 0;
    const calibrationError = finite(validation.calibration_error) ?? 1;
    const coverage = finite(validation.empirical_interval_coverage) ?? 0;
    const dataQuality = finite(forecast?.data_quality) ?? 0;
    const agreement = finite(forecast?.ensemble_agreement) ?? 0;
    const probability = asProbability(forecast?.probability_up) ?? 0.5;
    const directionScore = clamp(brierSkill / 0.1, 0, 1) * 20;
    const returnScore = clamp(returnSkill / 0.1, 0, 1) * 10;
    const calibrationScore = Math.max(0, 1 - calibrationError / 0.1) * 20;
    const coverageScore = Math.max(0, 1 - Math.abs(coverage - 0.8) / 0.2) * 15;
    const dimensions = [
      {
        id: "model_evidence",
        label: "样本外模型证据",
        score: directionScore + returnScore + calibrationScore + coverageScore,
        max_score: 65,
      },
      { id: "data_quality", label: "数据完整性", score: dataQuality * 15, max_score: 15 },
      { id: "ensemble_agreement", label: "组件一致性", score: agreement * 15, max_score: 15 },
      {
        id: "signal_separation",
        label: "信号区分度",
        score: Math.min(Math.abs(probability - 0.5) / 0.15, 1) * 5,
        max_score: 5,
      },
    ];
    return {
      score: dimensions.reduce((sum, dimension) => sum + dimension.score, 0),
      max_score: 100,
      dimensions,
      note: "四维线性综合诊断分；模型证据、数据、组件一致性和信号区分度须分开判断",
    };
  };

  const quantilePosition = (value, bound) => {
    const parsed = finite(value) ?? 0;
    return clamp(((parsed + bound) / (bound * 2)) * 100, 0, 100);
  };

  const renderQuantileMini = (forecast) => {
    const q10 = finite(forecast.quantiles?.q10) ?? 0;
    const q50 = finite(forecast.quantiles?.q50) ?? finite(forecast.expected_return) ?? 0;
    const q90 = finite(forecast.quantiles?.q90) ?? 0;
    const bound = Math.max(0.03, Math.abs(q10), Math.abs(q90), Math.abs(q50)) * 1.16;
    const left = quantilePosition(Math.min(q10, q90), bound);
    const right = quantilePosition(Math.max(q10, q90), bound);
    const median = quantilePosition(q50, bound);
    return `
      <div class="quantile-mini" aria-label="预测分位区间 ${formatReturn(q10)} 至 ${formatReturn(q90)}">
        <div class="quantile-track">
          <i class="quantile-zero"></i>
          <i class="quantile-range" style="left:${left.toFixed(1)}%;width:${Math.max(right - left, 1).toFixed(1)}%"></i>
          <i class="quantile-median" style="left:${median.toFixed(1)}%"></i>
        </div>
        <span class="quantile-label">中位 ${formatReturn(q50, 1)}</span>
      </div>
    `;
  };

  const renderForecastRow = (forecast) => {
    const probability = asProbability(forecast.probability_up);
    const score = diagnosticScore(forecast);
    const reliability = reliabilityDisplay(score);
    const band = readinessBand(score);
    const gaps = evidenceGaps(forecast);
    const restrictions = executionRestrictions(forecast);
    const q10 = forecast.quantiles?.q10;
    const q90 = forecast.quantiles?.q90;
    const selected = state.selectedByMarket[state.market] === forecast.symbol;
    const tone = restrictions.length ? "bad" : gaps.length ? "warn" : "good";
    return `
      <tr
        class="${selected ? "selected" : ""}"
        tabindex="0"
        data-forecast-symbol="${esc(forecast.symbol)}"
        aria-selected="${selected ? "true" : "false"}"
      >
        <td class="symbol-cell">
          <div class="symbol-line">
            <strong>${esc(forecast.symbol)}</strong>
            <span>${esc(forecast.name)}</span>
          </div>
          <span class="cell-secondary">${esc(forecast.exchange || "")}</span>
        </td>
        <td>
          <span class="cell-primary">${esc(forecast.board || forecast.sector || "-")}</span>
          <span class="cell-secondary">${esc(forecast.sector || "-")}</span>
        </td>
        <td>
          <div class="probability-cell">
            <strong>${formatProbability(probability)}</strong>
            <span class="probability-bar" style="--probability:${((probability ?? 0) * 100).toFixed(1)}%"><i></i></span>
          </div>
        </td>
        <td>
          <span class="interval-value">
            <span class="${returnClass(q10)}">${formatReturn(q10)}</span>
            <span>~</span>
            <span class="${returnClass(q90)}">${formatReturn(q90)}</span>
          </span>
          <span class="cell-secondary">期望 ${formatReturn(forecast.expected_return)}</span>
        </td>
        <td>
          <div class="maturity-score">
            <strong>${esc(reliability.score)}</strong><span>/100</span>
          </div>
          <span class="maturity-bar" style="--maturity:${(reliability.normalized * 100).toFixed(1)}%"><i></i></span>
          <span class="cell-secondary">${esc(band.label)} · ${formatInteger(forecast.sample_count)}观测行</span>
        </td>
        <td>
          <strong class="status-text ${tone}">${gaps.length ? `${gaps.length}项证据缺口` : "证据达标"}</strong>
          <span class="cell-secondary">${restrictions.length ? `${restrictions.length}项执行限制` : "执行窗口有效"}</span>
        </td>
        <td>${renderQuantileMini(forecast)}</td>
      </tr>
    `;
  };

  const paginationButtons = (pageCount) => {
    if (pageCount <= 1) return "";
    const pages = new Set([1, pageCount, state.page - 1, state.page, state.page + 1]);
    const visible = [...pages].filter((page) => page >= 1 && page <= pageCount).sort((a, b) => a - b);
    const output = [];
    visible.forEach((page, index) => {
      if (index && page - visible[index - 1] > 1) output.push('<span aria-hidden="true">…</span>');
      output.push(
        `<button class="page-button ${page === state.page ? "active" : ""}" type="button" data-page="${page}" aria-label="第${page}页">${page}</button>`,
      );
    });
    return `
      <div class="pagination">
        <button class="page-button" type="button" data-page="${state.page - 1}" ${state.page <= 1 ? "disabled" : ""} aria-label="上一页">‹</button>
        ${output.join("")}
        <button class="page-button" type="button" data-page="${state.page + 1}" ${state.page >= pageCount ? "disabled" : ""} aria-label="下一页">›</button>
      </div>
    `;
  };

  const renderForecastSection = () => {
    const all = allForecasts();
    const rows = filteredForecasts();
    if (rows.length && !rows.some((forecast) => forecast.symbol === state.selectedByMarket[state.market])) {
      state.selectedByMarket[state.market] = rows[0].symbol;
    }
    const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
    state.page = clamp(state.page, 1, pageCount);
    const start = (state.page - 1) * PAGE_SIZE;
    const pageRows = rows.slice(start, start + PAGE_SIZE);
    const allRejected = all.length > 0 && all.every(isRejected);
    const notice = allRejected
      ? `<p class="global-notice"><strong>当前无“可研究”标的</strong><span>不再用单一“暂缓”压平差异：请比较综合诊断分及四个独立维度。证据缺口用于连续判断，执行限制仍作为硬提示。</span></p>`
      : "";
    return `
      <section class="forecast-section" aria-label="${esc(marketLabel(state.market))}列表">
        ${renderFilters()}
        ${notice}
        ${
          rows.length
            ? `
              <div class="table-scroll">
                <table class="forecast-table">
                  <colgroup>
                    <col style="width:19%" />
                    <col style="width:12%" />
                    <col style="width:14%" />
                    <col style="width:14%" />
                    <col style="width:14%" />
                    <col style="width:12%" />
                    <col style="width:15%" />
                  </colgroup>
                  <thead>
                    <tr>
                      <th>代码 / 名称</th>
                      <th>${state.market === "cn" ? "板块" : "行业"}</th>
                      <th>5日上涨概率</th>
                      <th>预测收益区间（5日）</th>
                      <th>综合诊断分</th>
                      <th>证据 / 执行</th>
                      <th>预测分布</th>
                    </tr>
                  </thead>
                  <tbody>${pageRows.map(renderForecastRow).join("")}</tbody>
                </table>
              </div>
              <footer class="table-footer">
                <span>共 ${formatInteger(rows.length)} 条${rows.length !== all.length ? `，由 ${formatInteger(all.length)} 条筛选` : ""} · 每页 ${PAGE_SIZE} 条</span>
                ${paginationButtons(pageCount)}
              </footer>
            `
            : `
              <div class="empty-inline">
                <div>
                  <strong>没有符合当前筛选条件的预测</strong>
                  <p>可清空搜索词或切换板块、状态筛选。</p>
                </div>
              </div>
            `
        }
      </section>
    `;
  };

  const chartRowsFor = (forecast) => {
    const ref = forecast?.chart_ref;
    if (!ref) return null;
    return state.chartCache.get(ref) || null;
  };

  const chartSeries = (payload) => {
    const rows = Array.isArray(payload) ? payload : arrayValue(payload?.rows);
    return rows
      .map((row) => ({ date: String(row?.date || ""), close: finite(row?.close) }))
      .filter((row) => row.date && row.close !== null)
      .slice(-60);
  };

  const renderPathChart = (forecast) => {
    const cached = chartRowsFor(forecast);
    const history = chartSeries(cached);
    const current = finite(forecast.current_price) ?? history.at(-1)?.close ?? 100;
    const q10 = finite(forecast.quantiles?.q10) ?? 0;
    const q50 = finite(forecast.quantiles?.q50) ?? finite(forecast.expected_return) ?? 0;
    const q90 = finite(forecast.quantiles?.q90) ?? 0;
    const futureLow = current * (1 + Math.min(q10, q90));
    const futureMid = current * (1 + q50);
    const futureHigh = current * (1 + Math.max(q10, q90));
    const historyValues = history.map((row) => row.close);
    const values = [...historyValues, current, futureLow, futureMid, futureHigh];
    const min = Math.min(...values);
    const max = Math.max(...values);
    const padding = (max - min || Math.max(current * 0.04, 1)) * 0.15;
    const low = min - padding;
    const high = max + padding;
    const width = 620;
    const height = 250;
    const left = 46;
    const right = 16;
    const top = 16;
    const bottom = 34;
    const splitX = history.length ? width * 0.58 : width * 0.36;
    const historyWidth = splitX - left;
    const forecastWidth = width - right - splitX;
    const y = (value) => top + ((high - value) / (high - low || 1)) * (height - top - bottom);
    const historyPoints = history
      .map((row, index) => {
        const x = history.length > 1 ? left + (index / (history.length - 1)) * historyWidth : splitX;
        return `${x.toFixed(1)},${y(row.close).toFixed(1)}`;
      })
      .join(" ");
    const futureX = (day) => splitX + (day / 5) * forecastWidth;
    const interpolate = (target, day) => current + (target - current) * (day / 5);
    const bandTop = Array.from({ length: 6 }, (_, day) => `${futureX(day).toFixed(1)},${y(interpolate(futureHigh, day)).toFixed(1)}`);
    const bandBottom = Array.from({ length: 6 }, (_, day) => `${futureX(5 - day).toFixed(1)},${y(interpolate(futureLow, 5 - day)).toFixed(1)}`);
    const midPoints = Array.from({ length: 6 }, (_, day) => `${futureX(day).toFixed(1)},${y(interpolate(futureMid, day)).toFixed(1)}`).join(" ");
    const ticks = [high, (high + low) / 2, low];
    return `
      <svg class="forecast-chart" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(forecast.symbol)}历史收盘与5日预测区间示意">
        ${ticks
          .map(
            (tick) => `
              <line class="grid" x1="${left}" y1="${y(tick).toFixed(1)}" x2="${width - right}" y2="${y(tick).toFixed(1)}"></line>
              <text class="axis-text" x="${left - 6}" y="${(y(tick) + 3).toFixed(1)}" text-anchor="end">${formatNumber(tick, 1)}</text>
            `,
          )
          .join("")}
        <line class="cutoff" x1="${splitX}" y1="${top}" x2="${splitX}" y2="${height - bottom}"></line>
        ${historyPoints ? `<polyline class="history-line" points="${historyPoints}"></polyline>` : ""}
        <polygon class="forecast-band" points="${[...bandTop, ...bandBottom].join(" ")}"></polygon>
        <polyline class="forecast-line" points="${midPoints}"></polyline>
        ${Array.from(
          { length: 6 },
          (_, day) => `<circle class="forecast-dot" cx="${futureX(day).toFixed(1)}" cy="${y(interpolate(futureMid, day)).toFixed(1)}" r="${day === 5 ? 3.5 : 2.3}"></circle>`,
        ).join("")}
        <text class="axis-text" x="${left}" y="${height - 10}">${history.length ? esc(history[0].date.slice(5)) : "历史"}</text>
        <text class="axis-text" x="${splitX - 5}" y="${height - 10}" text-anchor="end">T</text>
        ${Array.from({ length: 5 }, (_, index) => `<text class="axis-text" x="${futureX(index + 1).toFixed(1)}" y="${height - 10}" text-anchor="middle">T+${index + 1}</text>`).join("")}
      </svg>
    `;
  };

  const renderFactorPanel = (forecast) => {
    const factors = forecast.factor_contributions;
    const max = Math.max(...factors.map((factor) => Math.abs(finite(factor.value) ?? 0)), 0.001);
    return `
      <section class="detail-panel factors-panel">
        <div class="panel-heading">
          <h3>因子状态</h3>
          <span>方向性标准分，仅作状态解释</span>
        </div>
        ${
          factors.length
            ? `
              <div class="factor-list">
                ${factors
                  .slice(0, 9)
                  .map((factor) => {
                    const value = finite(factor.value) ?? 0;
                    const tone = value >= 0 ? "positive" : "negative";
                    return `
                      <div class="factor-row">
                        <span class="factor-name">${esc(factor.name || "-")}</span>
                        <span class="factor-track"><i class="${tone}" style="--factor-width:${((Math.abs(value) / max) * 48).toFixed(1)}%"></i></span>
                        <strong class="factor-value ${tone}">${value > 0 ? "+" : ""}${formatNumber(value, 3)}</strong>
                      </div>
                    `;
                  })
                  .join("")}
              </div>
            `
            : '<div class="empty-inline">暂无因子状态明细</div>'
        }
      </section>
    `;
  };

  const renderComponentPanel = (forecast) => {
    const components = forecast.component_predictions;
    const weightSum = components.reduce((total, component) => total + (asProbability(component.weight) ?? finite(component.weight) ?? 0), 0);
    return `
      <section class="detail-panel components-panel">
        <div class="panel-heading">
          <h3>模型组合</h3>
          <span>集成权重 · 一致度 ${formatProbability(forecast.ensemble_agreement, 0)}</span>
        </div>
        ${
          components.length
            ? `
              <div class="component-list">
                ${components
                  .map((component) => {
                    const rawWeight = finite(component.weight) ?? 0;
                    const normalized = rawWeight > 1 ? rawWeight / 100 : rawWeight;
                    return `
                      <div class="component-row">
                        <span class="component-name">${esc(component.label || component.id || "模型")}</span>
                        <span class="component-track"><i style="--component-weight:${clamp(normalized * 100, 0, 100).toFixed(1)}%"></i></span>
                        <strong class="component-value">${formatProbability(normalized, 0)}</strong>
                      </div>
                    `;
                  })
                  .join("")}
              </div>
              <table class="component-table">
                <thead><tr><th>模型</th><th>上涨概率</th><th>预期收益</th></tr></thead>
                <tbody>
                  ${components
                    .map(
                      (component) => `
                        <tr>
                          <td>${esc(component.label || component.id || "-")}</td>
                          <td>${formatProbability(component.probability)}</td>
                          <td class="${returnClass(component.expected_return)}">${formatReturn(component.expected_return)}</td>
                        </tr>
                      `,
                    )
                    .join("")}
                </tbody>
              </table>
              <div class="data-quality-line">
                <i class="quality-dot"></i>
                <span>权重合计 ${formatProbability(weightSum, 0)} · 数据质量 ${esc(formatDataQuality(forecast.data_quality))}</span>
              </div>
            `
            : '<div class="empty-inline">暂无模型组件明细</div>'
        }
      </section>
    `;
  };

  const formatDataQuality = (quality) => {
    if (quality === null || quality === undefined) return "未标记";
    if (typeof quality === "number") {
      return quality >= 0 && quality <= 1 ? `${Math.round(quality * 100)}%` : String(quality);
    }
    if (typeof quality === "string") return quality;
    return String(firstValue(quality, ["grade", "status", "label", "score"], "已记录"));
  };

  const renderReadinessPanel = (forecast) => {
    const readiness = readinessBreakdown(forecast);
    const score = diagnosticScore(forecast);
    const reliability = reliabilityDisplay(score);
    const band = readinessBand(score);
    const gaps = evidenceGaps(forecast);
    const restrictions = executionRestrictions(forecast);
    const dimensions = arrayValue(readiness.dimensions);
    return `
      <section class="readiness-panel" aria-label="综合诊断分拆解">
        <div class="readiness-heading">
          <div>
            <span>线性综合诊断分</span>
            <strong>${esc(reliability.score)}<small>/100 · ${esc(band.label)}</small></strong>
          </div>
          <p>${esc(readiness.note || "各维度线性加总；分数不等于上涨概率，执行限制单独列示。")}</p>
        </div>
        <div class="readiness-grid">
          ${dimensions
            .map((dimension) => {
              const score = finite(dimension.score) ?? 0;
              const maxScore = finite(dimension.max_score) ?? 0;
              const ratio = maxScore > 0 ? clamp(score / maxScore, 0, 1) : 0;
              const componentText = arrayValue(dimension.components)
                .map((component) => `${component.label || component.id} ${formatNumber(component.score, 1)}/${formatNumber(component.max_score, 0)}`)
                .join(" · ");
              return `
                <div class="readiness-dimension">
                  <div><span>${esc(dimension.label || dimension.id || "维度")}</span><strong>${formatNumber(score, 1)}/${formatNumber(maxScore, 0)}</strong></div>
                  <span class="readiness-track" style="--readiness:${(ratio * 100).toFixed(1)}%"><i></i></span>
                  ${componentText ? `<small>${esc(componentText)}</small>` : ""}
                </div>
              `;
            })
            .join("")}
        </div>
        <div class="readiness-notes">
          <div class="${gaps.length ? "warn" : "good"}">
            <strong>${gaps.length ? `证据缺口 ${gaps.length}项` : "证据条件已通过"}</strong>
            <span>${gaps.length ? gaps.slice(0, 5).map(esc).join(" · ") : "当前没有模型证据缺口"}</span>
          </div>
          <div class="${restrictions.length ? "bad" : "good"}">
            <strong>${restrictions.length ? `执行限制 ${restrictions.length}项` : "执行窗口有效"}</strong>
            <span>${restrictions.length ? restrictions.slice(0, 3).map(esc).join(" · ") : "当前没有时间窗口类执行限制"}</span>
          </div>
        </div>
      </section>
    `;
  };

  const renderValidation = () => {
    const validation = validationInfo(currentMarket());
    return `
      <section class="validation-block">
        <div class="panel-heading">
          <h3>Walk-forward 验证（滚动样本外）</h3>
          <span>${esc(validation.status)} · ${formatInteger(validation.sampleCount)} 样本外观测行</span>
        </div>
        <div class="validation-cards">
          <div class="validation-card">
            <span>方向 Brier 增益</span>
            <strong class="${returnClass(validation.brierSkill)}">${formatReturn(validation.brierSkill, 2)}</strong>
            <small>相对市场先验；正数才有增益</small>
          </div>
          <div class="validation-card">
            <span>收益 MAE 增益</span>
            <strong class="${returnClass(validation.returnSkill)}">${formatReturn(validation.returnSkill, 2)}</strong>
            <small>相对历史中位数；正数才有增益</small>
          </div>
          <div class="validation-card">
            <span>校准误差</span>
            <strong>${formatMetric(validation.calibration)}</strong>
            <small>概率与结果偏差</small>
          </div>
          <div class="validation-card">
            <span>预测区间覆盖率</span>
            <strong>${formatMetric(validation.coverage, "coverage")}</strong>
            <small>历史经验覆盖</small>
          </div>
        </div>
      </section>
    `;
  };

  const ledgerRows = (ledger) => {
    if (Array.isArray(ledger)) return ledger;
    return arrayValue(ledger?.recent || ledger?.records || ledger?.history || ledger?.windows);
  };

  const renderLedger = () => {
    const ledger = resolveLedger() || {};
    const summary = ledger?.summary || ledger;
    const rows = ledgerRows(ledger).slice(-10);
    const settled = firstValue(summary, ["settled", "resolved", "completed"], null);
    const pending = firstValue(summary, ["pending", "unresolved"], null);
    const voided = firstValue(summary, ["void", "voided", "untradable"], null);
    const qualified = firstValue(
      summary,
      ["accepted_resolved", "qualified", "passed", "qualified_samples"],
      null,
    );
    const brier = firstValue(summary, ["brier", "brier_score"], null);
    const hitRate = firstValue(summary, ["hit_rate", "direction_hit_rate"], null);
    return `
      <section class="ledger-block">
        <div class="panel-heading">
          <h3>前向账本</h3>
          <span>冻结预测，结果到期后持续结算</span>
        </div>
        <div class="ledger-summary">
          ${ledgerStat("已结算", formatInteger(settled))}
          ${ledgerStat("待结算", formatInteger(pending))}
          ${ledgerStat("作废", formatInteger(voided))}
          ${ledgerStat("通过样本", typeof qualified === "object" ? formatInteger(qualified.samples) : formatInteger(qualified))}
          ${ledgerStat("前向 Brier", formatMetric(brier))}
          ${ledgerStat("方向命中", formatProbability(hitRate))}
        </div>
        ${
          rows.length
            ? `
              <div class="ledger-table-wrap">
                <table class="ledger-table">
                  <thead><tr><th>预测日</th><th>样本</th><th>Brier</th><th>校准误差</th><th>命中率</th></tr></thead>
                  <tbody>
                    ${rows
                      .map(
                        (row) => `
                          <tr>
                            <td>${esc(firstValue(row, ["date", "prediction_date", "label", "window"], "-"))}</td>
                            <td>${formatInteger(firstValue(row, ["sample_count", "samples", "count"], null))}</td>
                            <td>${formatMetric(firstValue(row, ["brier", "brier_score"], null))}</td>
                            <td>${formatMetric(firstValue(row, ["calibration_error", "ece"], null))}</td>
                            <td>${formatProbability(firstValue(row, ["hit_rate", "direction_hit_rate"], null))}</td>
                          </tr>
                        `,
                      )
                      .join("")}
                  </tbody>
                </table>
              </div>
            `
            : ""
        }
      </section>
    `;
  };

  const ledgerStat = (label, value) => `
    <div class="ledger-stat">
      <span>${esc(label)}</span>
      <strong>${esc(value)}</strong>
    </div>
  `;

  const renderDetail = () => {
    const forecast = selectedForecast();
    if (!forecast) return "";
    const score = diagnosticScore(forecast);
    const reliability = reliabilityDisplay(score);
    const band = readinessBand(score);
    const gaps = evidenceGaps(forecast);
    const restrictions = executionRestrictions(forecast);
    const q10 = forecast.quantiles?.q10;
    const q90 = forecast.quantiles?.q90;
    const chartLoading = forecast.chart_ref && state.chartLoading.has(forecast.chart_ref);
    const chartPayload = chartRowsFor(forecast);
    const chartStatus = chartLoading
      ? "历史行情读取中…"
      : chartPayload?.error
        ? `历史行情读取失败：${chartPayload.error}`
        : "区间为5日终点插值示意，不代表逐日确定路径";
    return `
      <section class="detail-section" aria-label="${esc(forecast.symbol)}预测详情">
        <header class="detail-header">
          <div class="detail-identity">
            <div class="detail-identity-line">
              <strong>${esc(forecast.symbol)}</strong>
              <span>${esc(forecast.name)}</span>
            </div>
            <div class="detail-meta">
              <span>当前价（参考） ${formatPrice(forecast.current_price)}</span>
              <span>${esc(forecast.exchange || "")} ${esc(forecast.board || "")}</span>
              <span>行业 ${esc(forecast.sector || "-")}</span>
              <span>更新于 ${esc(forecast.as_of || state.data?.generated_at || "-")}</span>
              <span>${forecast.forward_eligible ? "已在下一开盘前冻结" : "未进入前向账本：冻结窗口已过"}</span>
              <span>目标窗口 ${esc(forecast.target_start_estimate || "-")} → ${esc(forecast.target_end_estimate || "-")}</span>
            </div>
          </div>
          <div class="detail-metric">
            <span>5日上涨概率</span>
            <strong class="accent">${formatProbability(forecast.probability_up)}</strong>
          </div>
          <div class="detail-metric">
            <span>预测收益区间</span>
            <strong><span class="${returnClass(q10)}">${formatReturn(q10, 1)}</span> ~ <span class="${returnClass(q90)}">${formatReturn(q90, 1)}</span></strong>
          </div>
          <div class="detail-metric">
            <span>综合诊断分</span>
            <strong>${esc(reliability.score)}<small>/100 · ${esc(band.label)}</small></strong>
          </div>
          <div class="detail-metric">
            <span>证据 / 执行</span>
            <strong class="status-text ${restrictions.length ? "bad" : gaps.length ? "warn" : "good"}">${gaps.length} / ${restrictions.length}<small>项</small></strong>
          </div>
        </header>
        <nav class="detail-nav" aria-label="详情内容">
          <span class="active">预测路径</span>
          <span>模型组合</span>
          <span>因子状态</span>
          <span>模型验证</span>
          <span>前向账本</span>
        </nav>
        ${renderReadinessPanel(forecast)}
        <div class="detail-content">
          <section class="detail-panel path-panel">
            <div class="panel-heading">
              <h3>预测路径</h3>
              <span>${esc(chartStatus)}</span>
            </div>
            ${renderPathChart(forecast)}
          </section>
          ${renderFactorPanel(forecast)}
          ${renderComponentPanel(forecast)}
        </div>
        <div class="validation-band">
          ${renderValidation()}
          ${renderLedger()}
        </div>
      </section>
    `;
  };

  const renderFooter = () => `
    <footer class="app-footer">
      <span>仅用于研究，不构成投资建议</span>
      <span>综合诊断分由四维线性加总；模型证据、信号区分度、收益区间与执行限制必须分开判断。</span>
    </footer>
  `;

  const renderError = () => `
    ${renderHeader()}
    <section class="state-panel">
      <div class="state-content">
        <div class="state-icon">${icons.alert}</div>
        <h2>预测数据读取失败</h2>
        <p>${esc(state.error || "无法读取 forecasts-v1.json。")}<br />页面没有使用旧数据或前端临时计算替代结果。</p>
        <div class="state-actions"><button class="state-button" type="button" data-refresh>重新读取</button></div>
      </div>
    </section>
    ${renderFooter()}
  `;

  const renderMarketUnavailable = () => `
    ${renderHeader()}
    <section class="state-panel">
      <div class="state-content">
        <div class="state-icon">${icons.database}</div>
        <h2>${esc(marketLabel(state.market))}尚未生成</h2>
        <p>数据文件已读取，但 markets.${state.market} 不存在。请等待该市场模型完成并重新刷新预测。</p>
      </div>
    </section>
    ${renderFooter()}
  `;

  const renderEmptyMarket = () => `
    ${renderHeader()}
    ${renderSummary()}
    <section class="state-panel">
      <div class="state-content">
        <div class="state-icon">${icons.database}</div>
        <h2>${esc(marketLabel(state.market))}暂无预测记录</h2>
        <p>模型已生成市场级状态，但当前没有可展示的标的。可能是预测宇宙为空、数据质量不足，或本轮全部停留在市场级门控。</p>
        <div class="state-actions"><button class="state-button" type="button" data-refresh>刷新预测</button></div>
      </div>
    </section>
    ${renderFooter()}
  `;

  const render = () => {
    setTheme();
    if (state.loading && !state.data) {
      app.innerHTML = `
        <section class="initial-loading" aria-label="正在读取预测">
          <div class="initial-loading-mark" aria-hidden="true"></div>
          <div><strong>正在读取双市场预测</strong><span>加载模型结果、验证指标与前向记录</span></div>
        </section>
      `;
      return;
    }
    if (state.error && !state.data) {
      app.innerHTML = renderError();
      return;
    }
    if (!currentMarket()) {
      app.innerHTML = renderMarketUnavailable();
      return;
    }
    ensureSelection();
    if (!allForecasts().length) {
      app.innerHTML = renderEmptyMarket();
      return;
    }
    app.innerHTML = `
      ${renderHeader()}
      ${sourceMode === "demo" ? '<p class="global-notice demo"><strong>合成演示数据</strong><span>此页面默认不展示真实行情或投资结论。运行实时刷新后使用 <code>?source=live</code> 查看本地结果。</span></p>' : ""}
      ${
        state.error
          ? `<p class="global-notice error"><strong>刷新失败</strong><span>${esc(
              state.error,
            )}。当前继续显示上一次成功读取的预测。</span></p>`
          : ""
      }
      ${renderSummary()}
      ${renderForecastSection()}
      ${renderDetail()}
      ${renderFooter()}
    `;
    loadSelectedChart();
  };

  const validatePayload = (payload) => {
    if (!payload || typeof payload !== "object") throw new Error("预测数据不是有效对象");
    if (!payload.schema_version) throw new Error("缺少 schema_version");
    if (!payload.generated_at) throw new Error("缺少 generated_at");
    if (!payload.model || typeof payload.model !== "object") throw new Error("缺少 model");
    if (!payload.markets || typeof payload.markets !== "object") throw new Error("缺少 markets");
    return payload;
  };

  const loadData = async ({ refresh = false } = {}) => {
    if (refresh) state.refreshing = true;
    else state.loading = true;
    state.error = null;
    render();
    try {
      const response = await fetch(`${DATA_URL}?ts=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const payload = validatePayload(await response.json());
      state.data = payload;
      state.error = null;
      ensureSelection();
    } catch (error) {
      state.error = error?.message || String(error);
      if (!refresh) state.data = null;
    } finally {
      state.loading = false;
      state.refreshing = false;
      render();
    }
  };

  const loadSelectedChart = async () => {
    const forecast = selectedForecast();
    const ref = forecast?.chart_ref;
    if (!ref || state.chartCache.has(ref) || state.chartLoading.has(ref)) return;
    state.chartLoading.add(ref);
    render();
    try {
      const response = await fetch(`${ref}${String(ref).includes("?") ? "&" : "?"}ts=${Date.now()}`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      state.chartCache.set(ref, await response.json());
    } catch (error) {
      state.chartCache.set(ref, { rows: [], error: error?.message || String(error) });
    } finally {
      state.chartLoading.delete(ref);
      render();
    }
  };

  const switchMarket = (market, { replace = false } = {}) => {
    if (!["cn", "us"].includes(market)) market = "cn";
    const changed = state.market !== market;
    state.market = market;
    state.query = "";
    state.sector = "all";
    state.status = "all";
    state.sort = "reliability";
    state.page = 1;
    if (changed || replace) writeMarketURL(market, replace);
    render();
  };

  app.addEventListener("click", (event) => {
    const marketButton = event.target.closest?.("[data-market-tab]");
    if (marketButton) {
      event.preventDefault();
      switchMarket(marketButton.getAttribute("data-market-tab"));
      return;
    }

    const refreshButton = event.target.closest?.("[data-refresh]");
    if (refreshButton) {
      event.preventDefault();
      if (!state.refreshing) loadData({ refresh: Boolean(state.data) });
      return;
    }

    const row = event.target.closest?.("[data-forecast-symbol]");
    if (row) {
      event.preventDefault();
      state.selectedByMarket[state.market] = row.getAttribute("data-forecast-symbol");
      render();
      document.querySelector(".detail-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
      return;
    }

    const pageButton = event.target.closest?.("[data-page]");
    if (pageButton && !pageButton.disabled) {
      event.preventDefault();
      state.page = Number(pageButton.getAttribute("data-page")) || 1;
      render();
      document.querySelector(".forecast-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  });

  app.addEventListener("input", (event) => {
    const query = event.target.closest?.("[data-filter-query]");
    if (!query) return;
    const cursor = query.selectionStart;
    state.query = query.value;
    state.page = 1;
    render();
    requestAnimationFrame(() => {
      const next = app.querySelector("[data-filter-query]");
      if (!next) return;
      next.focus();
      if (cursor !== null) next.setSelectionRange(cursor, cursor);
    });
  });

  app.addEventListener("change", (event) => {
    const sector = event.target.closest?.("[data-filter-sector]");
    if (sector) {
      state.sector = sector.value || "all";
      state.page = 1;
      render();
      return;
    }
    const status = event.target.closest?.("[data-filter-status]");
    if (status) {
      state.status = status.value || "all";
      state.page = 1;
      render();
      return;
    }
    const sort = event.target.closest?.("[data-filter-sort]");
    if (sort) {
      state.sort = sort.value || "reliability";
      state.page = 1;
      render();
    }
  });

  app.addEventListener("keydown", (event) => {
    if (!["Enter", " "].includes(event.key)) return;
    const row = event.target.closest?.("[data-forecast-symbol]");
    if (!row) return;
    event.preventDefault();
    state.selectedByMarket[state.market] = row.getAttribute("data-forecast-symbol");
    render();
    document.querySelector(".detail-section")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });

  window.addEventListener("popstate", () => {
    const market = marketFromURL();
    state.market = market;
    state.query = "";
    state.sector = "all";
    state.status = "all";
    state.page = 1;
    render();
  });

  state.market = marketFromURL();
  writeMarketURL(state.market, true);
  setTheme();
  loadData();
})();
