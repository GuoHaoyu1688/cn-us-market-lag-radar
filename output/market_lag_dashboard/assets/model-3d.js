(() => {
  "use strict";

  const app = document.querySelector("#modelApp");
  const sourceMode = new URLSearchParams(window.location.search).get("source") === "live" ? "live" : "demo";
  const DATA_URL = sourceMode === "live" ? "./data/forecasts-v1.json" : "./demo/forecasts-v1.json";
  const sourceQuery = sourceMode === "live" ? "&source=live" : "";

  const MODELS = [
    {
      id: "prior",
      label: "市场先验",
      shortLabel: "市场先验",
      color: "#1769d2",
      role: "用训练截面的上涨基准率形成收缩锚，避免挑战模型在弱证据下过度拟合。",
    },
    {
      id: "elastic_net",
      label: "正则逻辑回归",
      shortLabel: "正则逻辑",
      color: "#e87911",
      role: "对标准化因子进行带 Elastic Net 正则的逻辑回归，提供可解释的线性概率输入。",
    },
    {
      id: "gradient_boosting",
      label: "梯度提升树",
      shortLabel: "梯度提升树",
      color: "#0b8f8b",
      role: "用直方图梯度提升捕捉因子之间的非线性关系和交互，但必须通过样本外门槛。",
    },
    {
      id: "robust_trend",
      label: "稳健趋势模型",
      shortLabel: "稳健趋势",
      color: "#6d48b5",
      role: "按趋势强度、相对强弱和市场状态分桶，以贝叶斯式先验收缩得到稳健概率。",
    },
  ];

  const CONDITIONS = [
    {
      id: "mean_probability",
      group: "input",
      label: "平均概率输入",
      formula: "100 × mean(pₘ)",
      explanation: "当前市场截面中，该模型输出概率的算术均值。",
    },
    {
      id: "weight",
      group: "input",
      label: "集成权重",
      formula: "100 × wₘ",
      explanation: "早段样本外定权后进入凸组合的权重。",
    },
    {
      id: "weighted_contribution",
      group: "input",
      label: "加权贡献",
      formula: "100 × wₘ × mean(pₘ)",
      explanation: "该模型对市场截面平均原始集成概率的贡献百分点。",
    },
    {
      id: "brier_accuracy",
      group: "evaluation",
      label: "Brier 准确度",
      formula: "100 × (1 − Brierₘ)",
      explanation: "Brier 损失的补数，仅用于统一柱高方向；原始 Brier 同时展示。",
    },
    {
      id: "calibration_accuracy",
      group: "evaluation",
      label: "校准准确度",
      formula: "100 × (1 − ECEₘ)",
      explanation: "期望校准误差的补数；越高表示概率与实际频率越一致。",
    },
    {
      id: "likelihood_quality",
      group: "evaluation",
      label: "似然质量",
      formula: "100 × exp(−LogLossₘ)",
      explanation: "对数损失对应的几何平均真类概率，越高越好。",
    },
  ];

  const state = {
    data: null,
    loading: true,
    refreshing: false,
    error: null,
    market: "cn",
    conditionView: "all",
    selectedModel: "prior",
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

  const formatPercent = (value, digits = 1, fallback = "-") => {
    const parsed = finite(value);
    return parsed === null ? fallback : `${(parsed * 100).toFixed(digits)}%`;
  };

  const formatSignedPercent = (value, digits = 2, fallback = "-") => {
    const parsed = finite(value);
    if (parsed === null) return fallback;
    const percent = parsed * 100;
    return `${percent > 0 ? "+" : ""}${percent.toFixed(digits)}%`;
  };

  const toneClass = (value) => {
    const parsed = finite(value);
    if (parsed === null || parsed === 0) return "";
    return parsed > 0 ? "good" : "bad";
  };

  const marketLabel = (market = state.market) => (market === "us" ? "美股预测" : "A股预测");
  const currentMarket = () => state.data?.markets?.[state.market] || null;
  const validation = () => currentMarket()?.validation || {};
  const modelVersion = () => state.data?.model?.version || "-";

  const marketFromURL = () =>
    new URLSearchParams(window.location.search).get("market") === "us" ? "us" : "cn";

  const writeMarketURL = (market, replace = false) => {
    const url = new URL(window.location.href);
    url.searchParams.set("market", market);
    window.history[replace ? "replaceState" : "pushState"](
      { market },
      "",
      `${url.pathname}${url.search}${url.hash}`,
    );
  };

  const setTheme = () => {
    document.body.dataset.market = state.market;
    document.documentElement.style.colorScheme = state.market === "us" ? "dark" : "light";
    document.title = `${marketLabel()} · 模型评价透视`;
  };

  const componentProbabilityMean = (modelId) => {
    const values = [];
    for (const forecast of arrayValue(currentMarket()?.forecasts)) {
      const component = arrayValue(forecast?.component_predictions).find((item) => item.id === modelId);
      const probability = finite(component?.probability);
      if (probability !== null) values.push(probability);
    }
    return values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : null;
  };

  const modelMetric = (modelId) => validation()?.component_metrics?.[modelId] || {};
  const modelWeight = (modelId) => finite(validation()?.weights?.[modelId]) ?? 0;

  const conditionHeight = (modelId, conditionId) => {
    const metrics = modelMetric(modelId);
    const meanProbability = componentProbabilityMean(modelId) ?? 0;
    const weight = modelWeight(modelId);
    if (conditionId === "mean_probability") return clamp(meanProbability * 100, 0, 100);
    if (conditionId === "weight") return clamp(weight * 100, 0, 100);
    if (conditionId === "weighted_contribution") return clamp(weight * meanProbability * 100, 0, 100);
    if (conditionId === "brier_accuracy") return clamp((1 - (finite(metrics.brier) ?? 1)) * 100, 0, 100);
    if (conditionId === "calibration_accuracy") {
      return clamp((1 - (finite(metrics.calibration_error) ?? 1)) * 100, 0, 100);
    }
    if (conditionId === "likelihood_quality") {
      return clamp(Math.exp(-(finite(metrics.log_loss) ?? Infinity)) * 100, 0, 100);
    }
    return 0;
  };

  const conditionRaw = (modelId, conditionId) => {
    const metrics = modelMetric(modelId);
    const meanProbability = componentProbabilityMean(modelId);
    const weight = modelWeight(modelId);
    if (conditionId === "mean_probability") return `mean(pₘ) = ${formatNumber(meanProbability, 6)}`;
    if (conditionId === "weight") return `wₘ = ${formatNumber(weight, 6)}`;
    if (conditionId === "weighted_contribution") {
      return `wₘ·mean(pₘ) = ${formatNumber(weight * (meanProbability ?? 0), 6)}`;
    }
    if (conditionId === "brier_accuracy") return `Brier = ${formatNumber(metrics.brier, 6)}`;
    if (conditionId === "calibration_accuracy") return `ECE = ${formatNumber(metrics.calibration_error, 6)}`;
    if (conditionId === "likelihood_quality") return `LogLoss = ${formatNumber(metrics.log_loss, 6)}`;
    return "-";
  };

  const visibleConditions = () => {
    if (state.conditionView === "input") return CONDITIONS.filter((item) => item.group === "input");
    if (state.conditionView === "evaluation") return CONDITIONS.filter((item) => item.group === "evaluation");
    return CONDITIONS;
  };

  const selectedModelDefinition = () =>
    MODELS.find((model) => model.id === state.selectedModel) || MODELS[0];

  const modelStatus = (model) => {
    const weight = modelWeight(model.id);
    if (model.id === "prior") return { label: "收缩锚", detail: "先验锚点" };
    if (weight > 0) return { label: "已保留", detail: "非零权重" };
    return { label: "权重归零", detail: "未进入当前组合" };
  };

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
        <nav class="view-switcher" data-view="model" aria-label="页面切换">
          <a class="view-switch-link" href="./index.html?market=${state.market}${sourceQuery}">预测列表</a>
          <a class="view-switch-link active" href="./model-3d.html?market=${state.market}${sourceQuery}" aria-current="page">三维模型</a>
        </nav>
        <button class="refresh-button" type="button" data-refresh ${state.refreshing ? "disabled" : ""}>
          ${
            state.refreshing
              ? '<span class="refresh-spinner" aria-hidden="true"></span>'
              : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 7v5h-5M4 17v-5h5" fill="none" stroke="currentColor" stroke-linecap="round" stroke-linejoin="round"></path><path d="M6.1 8.5A7 7 0 0 1 18.7 7L20 12M4 12l1.3 5A7 7 0 0 0 17.9 15.5" fill="none" stroke="currentColor" stroke-linecap="round"></path></svg>'
          }
          <span>${state.refreshing ? "读取中" : "读取最新"}</span>
        </button>
        <span class="data-cutoff">预测生成于 ${esc(state.data?.generated_at || "-")}</span>
      </div>
    </header>
  `;

  const renderPageHeading = () => {
    const session = currentMarket()?.session || {};
    return `
      <section class="model-page-heading">
        <div>
          <h2>模型评价透视</h2>
          <p>X 轴是四个数学模型输入，Y 轴是六个定权与评价条件，Z 轴把每个条件换算到 0—100 的线性高度。这里不展示任何单一标的，只解释模型怎样被评价、保留、归零并组合。</p>
        </div>
        <div class="model-meta">
          <strong>${esc(marketLabel())} · 截面 ${esc(session.data_as_of || "-")}</strong>
          <span>4 个数学模型 · 6 个评价条件 · ${formatInteger(validation().samples)} 个样本外观测</span>
        </div>
      </section>
    `;
  };

  const points = (pairs) => pairs.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");

  const renderChartSvg = () => {
    const conditions = visibleConditions();
    const originX = 86;
    const baseline = 395;
    const barWidth = conditions.length > 3 ? 31 : 38;
    const depthX = 9;
    const depthY = 7;
    const xStep = conditions.length > 3 ? 145 : 155;
    const yDx = conditions.length > 3 ? 13 : 19;
    const yDy = conditions.length > 3 ? 20 : 29;
    const plotHeight = 270;
    const xEnd = originX + (MODELS.length - 1) * xStep + barWidth + 30;
    const depthTotalX = (conditions.length - 1) * yDx + depthX;
    const depthTotalY = (conditions.length - 1) * yDy + depthY;
    const width = Math.max(850, xEnd + depthTotalX + 190);
    const ticks = [0, 20, 40, 60, 80, 100];

    const floor = points([
      [originX, baseline],
      [xEnd, baseline],
      [xEnd + depthTotalX, baseline - depthTotalY],
      [originX + depthTotalX, baseline - depthTotalY],
    ]);

    const zGrid = ticks
      .map((tick) => {
        const y = baseline - (tick / 100) * plotHeight;
        return `
          <polyline class="z-grid" points="${points([
            [originX, y],
            [xEnd, y],
            [xEnd + depthTotalX, y - depthTotalY],
          ])}" />
          <text class="axis-tick" x="${originX - 12}" y="${y + 3}" text-anchor="end">${tick}</text>
        `;
      })
      .join("");

    const floorGridX = MODELS.map((_, index) => {
      const x = originX + index * xStep + barWidth / 2;
      return `<line class="floor-grid" x1="${x}" y1="${baseline}" x2="${x + depthTotalX}" y2="${baseline - depthTotalY}" />`;
    }).join("");

    const floorGridY = conditions.map((_, index) => {
      const xOffset = index * yDx;
      const yOffset = index * yDy;
      return `<line class="floor-grid" x1="${originX + xOffset}" y1="${baseline - yOffset}" x2="${xEnd + xOffset}" y2="${baseline - yOffset}" />`;
    }).join("");

    const bars = [];
    for (let yIndex = conditions.length - 1; yIndex >= 0; yIndex -= 1) {
      MODELS.forEach((model, xIndex) => {
        const condition = conditions[yIndex];
        const value = conditionHeight(model.id, condition.id);
        const height = (value / 100) * plotHeight;
        const x = originX + xIndex * xStep + yIndex * yDx;
        const baseY = baseline - yIndex * yDy;
        const topY = baseY - height;
        const raw = conditionRaw(model.id, condition.id);
        const aria = `${model.label}，${condition.label}，柱高 ${formatNumber(value, 1)}，${raw}`;
        bars.push(`
          <g
            class="bar-group ${state.selectedModel === model.id ? "selected" : ""}"
            role="button"
            tabindex="0"
            aria-label="${esc(aria)}"
            data-model-id="${model.id}"
            data-tooltip-title="${esc(`${model.label} · ${condition.label}`)}"
            data-tooltip-detail="${esc(`柱高 ${formatNumber(value, 1)} / 100 · ${raw} · ${condition.formula}`)}"
          >
            <rect fill="transparent" x="${x}" y="${baseY - Math.max(height, 9)}" width="${barWidth + depthX}" height="${Math.max(height, 9)}"></rect>
            <polygon class="bar-front" fill="${model.color}" points="${points([
              [x, baseY],
              [x + barWidth, baseY],
              [x + barWidth, topY],
              [x, topY],
            ])}" />
            <polygon class="bar-side" fill="${model.color}" points="${points([
              [x + barWidth, baseY],
              [x + barWidth + depthX, baseY - depthY],
              [x + barWidth + depthX, topY - depthY],
              [x + barWidth, topY],
            ])}" />
            <polygon class="bar-top" fill="${model.color}" points="${points([
              [x, topY],
              [x + barWidth, topY],
              [x + barWidth + depthX, topY - depthY],
              [x + depthX, topY - depthY],
            ])}" />
            <text class="bar-value" x="${x + barWidth / 2 + depthX / 2}" y="${Math.max(17, topY - depthY - 5)}">${formatNumber(value, 1)}</text>
          </g>
        `);
      });
    }

    const modelLabels = MODELS.map((model, index) => {
      const x = originX + index * xStep + barWidth / 2;
      return `
        <text class="model-label" x="${x}" y="${baseline + 24}" data-model-id="${model.id}">
          <tspan x="${x}" dy="0">${esc(model.shortLabel)}</tspan>
          <tspan x="${x}" dy="13">w=${formatNumber(modelWeight(model.id), 3)}</tspan>
        </text>
      `;
    }).join("");

    const conditionLabels = conditions.map((condition, index) => {
      const x = xEnd + index * yDx + depthX + 7;
      const y = baseline - index * yDy - depthY + 3;
      return `<text class="condition-label" x="${x}" y="${y}">${esc(condition.label)}</text>`;
    }).join("");

    return `
      <svg viewBox="0 0 ${width} 515" width="${width}" height="515" role="img" aria-labelledby="modelInputTitle modelInputDesc">
        <title id="modelInputTitle">${esc(marketLabel())}数学模型评价矩阵</title>
        <desc id="modelInputDesc">X轴为四个数学模型，Y轴为${conditions.length}个评价条件，Z轴从零开始显示0到100的换算值。</desc>
        ${zGrid}
        <polygon class="floor-plane" points="${floor}" />
        ${floorGridX}${floorGridY}
        <line class="axis-line" x1="${originX}" y1="${baseline}" x2="${originX}" y2="${baseline - plotHeight - 10}" />
        <line class="axis-line" x1="${originX}" y1="${baseline}" x2="${xEnd + 8}" y2="${baseline}" />
        <line class="axis-line" x1="${xEnd}" y1="${baseline}" x2="${xEnd + depthTotalX + 8}" y2="${baseline - depthTotalY - 8}" />
        ${bars.join("")}
        ${modelLabels}
        ${conditionLabels}
        <text class="axis-label" x="${originX - 50}" y="${baseline - plotHeight - 18}">Z 数值高度</text>
        <text class="axis-label" x="${originX + (xEnd - originX) / 2}" y="${baseline + 61}" text-anchor="middle">X 数学模型输入</text>
        <text class="axis-label" x="${xEnd + depthTotalX + 18}" y="${baseline - depthTotalY - 14}" text-anchor="end">Y 评价条件</text>
      </svg>
    `;
  };

  const renderChartCard = () => `
    <section class="model-card chart-card" aria-label="数学模型三坐标评价矩阵">
      <header class="card-heading">
        <div class="card-title">
          <h3>数学模型输入矩阵</h3>
          <p>四种颜色代表四个数学模型；每根柱对应一个模型在一个评价条件上的值。点击任一模型柱，右侧显示其完整评价数据。</p>
        </div>
        <div class="condition-switch" role="group" aria-label="评价条件范围">
          <button type="button" class="${state.conditionView === "all" ? "active" : ""}" data-condition-view="all">全部条件</button>
          <button type="button" class="${state.conditionView === "input" ? "active" : ""}" data-condition-view="input">集成输入</button>
          <button type="button" class="${state.conditionView === "evaluation" ? "active" : ""}" data-condition-view="evaluation">样本外评价</button>
        </div>
      </header>
      <div class="chart-stage" data-chart-stage>
        ${renderChartSvg()}
        <div class="chart-tooltip" data-chart-tooltip role="status"></div>
      </div>
      <footer class="chart-footer">
        <div class="model-legend">
          ${MODELS.map(
            (model) => `<span class="legend-item"><i class="legend-swatch" style="background:${model.color}"></i>${esc(model.label)}</span>`,
          ).join("")}
        </div>
        <div class="chart-scale-note">Z 轴固定为 0—100。损失类指标使用明确的单调换算，使“越高越好”，原始损失不被隐藏。</div>
      </footer>
    </section>
  `;

  const renderFormulaCard = () => {
    const calibrator = validation().calibrator || {};
    return `
      <section class="model-card formula-card">
        <h3>评价公式</h3>
        <div class="formula-block">
          <span class="formula-kicker">第一层 · 非负凸集成</span>
          <code class="formula-expression">praw = Σₘ wₘ·pₘ　且　wₘ ≥ 0，Σₘ wₘ = 1</code>
          <p class="formula-help">输入是四个数学模型的概率，不是单一标的本身。</p>
        </div>
        <div class="formula-block">
          <span class="formula-kicker">第二层 · 独立 Sigmoid 校准</span>
          <code class="formula-expression">pup = σ(α + β·logit(praw))</code>
          <p class="formula-help">当前 ${esc(marketLabel())}：α=${formatNumber(calibrator.intercept, 6)}，β=${formatNumber(calibrator.coefficient, 6)}。</p>
        </div>
        <div class="formula-block">
          <span class="formula-kicker">第三层 · 线性评价模型</span>
          <code class="formula-expression">D = E模型证据 + Q数据完整性 + A组件一致性 + S信号区分度</code>
          <p class="formula-help">满分 100 = 65 + 15 + 15 + 5；D 是研究诊断刻度，不等于上涨概率。</p>
        </div>
        <div class="formula-block">
          <span class="formula-kicker">模型证据 · 满分 65</span>
          <code class="formula-expression">E = 20·clip(BS/0.10) + 10·clip(RS/0.10) + 20·max(0,1−ECE/0.10) + 15·max(0,1−|Cov−0.80|/0.20)</code>
        </div>
      </section>
    `;
  };

  const weightLogic = (model) => {
    const weight = modelWeight(model.id);
    if (model.id === "prior") {
      return "市场先验是收缩锚：至少保留 10%；当没有挑战模型被保留时可承担全部权重，有挑战模型时按代码约束控制其占比。";
    }
    if (weight > 0) {
      return `该挑战模型已被定权流程保留，当前权重为 ${formatPercent(weight, 2)}。权重来自早段样本外定权，不使用封存验收段重新调权。`;
    }
    return "当前权重为 0。算法只保留在定权段优于先验、且在非负凸优化中获得权重的挑战模型；当前 JSON 不公开逐模型定权段 Brier，因此不能用封存段指标反推唯一归零原因。";
  };

  const renderSelectedDetail = () => {
    const model = selectedModelDefinition();
    const metrics = modelMetric(model.id);
    const status = modelStatus(model);
    const weight = modelWeight(model.id);
    return `
      <section class="model-card detail-card">
        <h3>选中模型评价</h3>
        <div class="selected-summary">
          <div class="selected-identity">
            <strong>${esc(model.label)}</strong>
            <span>${esc(status.label)} · ${esc(model.role)}</span>
          </div>
          <div class="selected-weight">${formatPercent(weight, 1)}<small>当前集成权重</small></div>
        </div>
        <table class="condition-table">
          <thead><tr><th>柱高条件</th><th>高度</th><th>原始量</th></tr></thead>
          <tbody>
            ${CONDITIONS.map(
              (condition) => `
                <tr>
                  <td>${esc(condition.label)}</td>
                  <td>${formatNumber(conditionHeight(model.id, condition.id), 1)}</td>
                  <td>${esc(conditionRaw(model.id, condition.id))}</td>
                </tr>
              `,
            ).join("")}
          </tbody>
        </table>
        <table class="raw-table">
          <thead><tr><th>样本外原始指标</th><th>数值</th><th>判断方向</th></tr></thead>
          <tbody>
            <tr><td>样本数</td><td>${formatInteger(metrics.samples)}</td><td>越多越稳定</td></tr>
            <tr><td>Brier</td><td>${formatNumber(metrics.brier, 6)}</td><td>越低越好</td></tr>
            <tr><td>Brier 相对先验增益</td><td class="${toneClass(metrics.brier_skill)}">${formatSignedPercent(metrics.brier_skill, 3)}</td><td>大于 0 更好</td></tr>
            <tr><td>LogLoss</td><td>${formatNumber(metrics.log_loss, 6)}</td><td>越低越好</td></tr>
            <tr><td>校准误差 ECE</td><td>${formatNumber(metrics.calibration_error, 6)}</td><td>越低越好</td></tr>
          </tbody>
        </table>
        <p class="logic-note"><strong>${model.id === "prior" ? "权重保留逻辑" : weight > 0 ? "当前保留逻辑" : "权重归零说明"}</strong>${esc(weightLogic(model))}</p>
      </section>
    `;
  };

  const renderPipeline = () => {
    const calibrator = validation().calibrator || {};
    const ledger = currentMarket()?.forward_validation || {};
    const arrow = '<div class="pipeline-arrow" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M4 12h14m-5-5 5 5-5 5" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"></path></svg></div>';
    return `
      <section class="model-card pipeline-card">
        <h3>评价模型处理流水线</h3>
        <div class="pipeline">
          <div class="pipeline-step" style="border-top-color:#1769d2">
            <strong>① 四模型输入</strong>
            <span>四个模型分别输出 5 日上涨概率。</span>
            <div class="pipeline-models">${MODELS.map((model) => `<i><b style="background:${model.color}"></b>${esc(model.shortLabel)}</i>`).join("")}</div>
          </div>
          ${arrow}
          <div class="pipeline-step" style="border-top-color:#e87911">
            <strong>② 非负凸定权</strong>
            <span>早段 OOF 数据优化 LogLoss；权重非负且总和为 1，弱挑战者可归零。</span>
          </div>
          ${arrow}
          <div class="pipeline-step" style="border-top-color:#0b8f8b">
            <strong>③ Sigmoid 校准</strong>
            <span>中段独立拟合 α=${formatNumber(calibrator.intercept, 3)}、β=${formatNumber(calibrator.coefficient, 3)}，校准原始组合概率。</span>
          </div>
          ${arrow}
          <div class="pipeline-step" style="border-top-color:#6d48b5">
            <strong>④ 线性诊断评价</strong>
            <span>模型证据、数据完整性、组件一致性和信号区分度线性加总至 100。</span>
          </div>
          ${arrow}
          <div class="pipeline-step" style="border-top-color:var(--accent)">
            <strong>⑤ 前向账本</strong>
            <span>冻结后追加记录：总计 ${formatInteger(ledger.total)}，已结算 ${formatInteger(ledger.resolved)}，待结算 ${formatInteger(ledger.pending)}。</span>
          </div>
        </div>
      </section>
    `;
  };

  const dimensionAverages = () => {
    const buckets = new Map();
    for (const forecast of arrayValue(currentMarket()?.forecasts)) {
      for (const dimension of arrayValue(forecast?.diagnostic_score_breakdown?.dimensions)) {
        if (!buckets.has(dimension.id)) {
          buckets.set(dimension.id, {
            id: dimension.id,
            label: dimension.label,
            maxScore: finite(dimension.max_score) ?? 0,
            scores: [],
          });
        }
        const score = finite(dimension.score);
        if (score !== null) buckets.get(dimension.id).scores.push(score);
      }
    }
    return [...buckets.values()].map((item) => ({
      ...item,
      meanScore: item.scores.length
        ? item.scores.reduce((sum, value) => sum + value, 0) / item.scores.length
        : 0,
    }));
  };

  const renderWeightCard = () => `
    <section class="model-card metric-card">
      <h3>当前权重向量</h3>
      <div class="metric-lead"><strong>${MODELS.filter((model) => modelWeight(model.id) > 0).length} / 4</strong><span>进入当前凸组合的数学模型数</span></div>
      ${MODELS.map((model) => {
        const weight = modelWeight(model.id);
        return `<div class="weight-row"><span>${esc(model.shortLabel)}</span><div class="weight-track"><div class="weight-fill" style="width:${clamp(weight * 100, 0, 100)}%;background:${model.color}"></div></div><strong>${formatPercent(weight, 1)}</strong></div>`;
      }).join("")}
    </section>
  `;

  const renderDiagnosticCard = () => {
    const dimensions = dimensionAverages();
    const total = dimensions.reduce((sum, item) => sum + item.meanScore, 0);
    return `
      <section class="model-card metric-card">
        <h3>评价维度均值</h3>
        <div class="metric-lead"><strong>${formatNumber(total, 1)} / 100</strong><span>当前市场截面的评价模型平均输出，不显示单一标的</span></div>
        ${dimensions.map((dimension) => {
          const completion = dimension.maxScore ? (dimension.meanScore / dimension.maxScore) * 100 : 0;
          return `<div class="score-row"><span>${esc(dimension.label)}</span><div class="score-track"><div class="score-fill" style="width:${clamp(completion, 0, 100)}%"></div></div><strong>${formatNumber(dimension.meanScore, 1)}/${formatNumber(dimension.maxScore, 0)}</strong></div>`;
        }).join("")}
      </section>
    `;
  };

  const renderValidationCard = () => {
    const item = validation();
    return `
      <section class="model-card metric-card">
        <h3>Walk-forward 封存评价</h3>
        <div class="metric-lead"><strong>${esc(item.status || "待验证")}</strong><span>${formatInteger(item.samples)} 样本 · ${formatInteger(item.folds)} 折</span></div>
        <div class="metric-list">
          <div><span>组合 Brier</span><strong>${formatNumber(item.brier, 4)}</strong></div>
          <div><span>Brier 增益</span><strong class="${toneClass(item.brier_skill)}">${formatSignedPercent(item.brier_skill, 2)}</strong></div>
          <div><span>收益 MAE 增益</span><strong class="${toneClass(item.return_skill)}">${formatSignedPercent(item.return_skill, 2)}</strong></div>
          <div><span>组合校准误差</span><strong>${formatNumber(item.calibration_error, 4)}</strong></div>
          <div><span>经验区间覆盖</span><strong>${formatPercent(item.empirical_interval_coverage, 1)}</strong></div>
          <div><span>封存窗口</span><strong>${esc(item.holdout_start || "-")} → ${esc(item.holdout_end || "-")}</strong></div>
        </div>
      </section>
    `;
  };

  const renderConstraintCard = () => `
    <section class="model-card metric-card">
      <h3>定权与评价约束</h3>
      <div class="metric-lead"><strong>严格分段</strong><span>定权、校准、封存评价互不复用</span></div>
      <ul class="constraint-list">
        <li>市场先验在有挑战者时作为 10%—80% 的收缩锚；单个挑战模型上限 75%。</li>
        <li>挑战模型若在定权段 Brier 不优于先验，其权重直接归零。</li>
        <li>四个权重非负且总和为 1；A股与美股分别训练、定权和校准。</li>
        <li>方向 Brier 增益需大于 0、收益 MAE 增益需大于 0、ECE 需不高于 0.06，才通过双指标研究门槛。</li>
        <li>三维柱高只作统一方向的数学换算，原始损失和相对增益始终同步展示。</li>
      </ul>
    </section>
  `;

  const renderFooter = () => `
    <footer class="app-footer">
      <span>仅用于研究，不构成投资建议</span>
      <span>本页可视化对象是数学模型及其评价条件；不以单一标的作为坐标输入。</span>
    </footer>
  `;

  const renderError = () => `
    ${renderHeader()}
    <section class="state-panel model-state">
      <div class="state-content">
        <h2>模型数据读取失败</h2>
        <p>${esc(state.error || "无法读取 forecasts-v1.json。")}</p>
        <div class="state-actions"><button class="state-button" type="button" data-refresh>重新读取</button></div>
      </div>
    </section>
    ${renderFooter()}
  `;

  const render = () => {
    setTheme();
    if (state.loading && !state.data) {
      app.innerHTML = `
        <section class="initial-loading" aria-label="正在读取模型">
          <div class="initial-loading-mark" aria-hidden="true"></div>
          <div><strong>正在构建模型评价透视</strong><span>读取数学模型输入、评价条件、公式与验证指标</span></div>
        </section>
      `;
      return;
    }
    if (state.error && !state.data) {
      app.innerHTML = renderError();
      return;
    }
    if (!currentMarket() || !validation().component_metrics) {
      app.innerHTML = `${renderHeader()}<section class="state-panel model-state"><div class="state-content"><h2>${esc(marketLabel())}暂无模型评价数据</h2><p>市场数据存在，但缺少组件模型的样本外评价指标。</p></div></section>${renderFooter()}`;
      return;
    }
    app.innerHTML = `
      ${renderHeader()}
      ${sourceMode === "demo" ? '<p class="global-notice demo"><strong>合成演示数据</strong><span>模型指标仅用于展示数据结构和交互，不代表任何真实回测结果。</span></p>' : ""}
      ${state.error ? `<p class="global-notice error"><strong>刷新失败</strong><span>${esc(state.error)}。继续显示上一次成功读取的数据。</span></p>` : ""}
      ${renderPageHeading()}
      <div class="model-grid">
        ${renderChartCard()}
        <div class="side-stack">
          ${renderFormulaCard()}
          ${renderSelectedDetail()}
        </div>
      </div>
      ${renderPipeline()}
      <div class="metrics-grid">
        ${renderWeightCard()}
        ${renderDiagnosticCard()}
        ${renderValidationCard()}
        ${renderConstraintCard()}
      </div>
      ${renderFooter()}
    `;
  };

  const validatePayload = (payload) => {
    if (!payload || typeof payload !== "object") throw new Error("预测数据不是有效对象");
    if (!payload.model || !payload.markets) throw new Error("缺少模型或市场字段");
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
      state.data = validatePayload(await response.json());
    } catch (error) {
      state.error = error?.message || String(error);
      if (!refresh) state.data = null;
    } finally {
      state.loading = false;
      state.refreshing = false;
      render();
    }
  };

  const switchMarket = (market, { replace = false } = {}) => {
    state.market = market === "us" ? "us" : "cn";
    state.conditionView = "all";
    state.selectedModel = "prior";
    writeMarketURL(state.market, replace);
    render();
  };

  const selectModel = (modelId) => {
    if (!MODELS.some((model) => model.id === modelId)) return;
    state.selectedModel = modelId;
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

    const conditionButton = event.target.closest?.("[data-condition-view]");
    if (conditionButton) {
      state.conditionView = conditionButton.getAttribute("data-condition-view") || "all";
      render();
      return;
    }

    const modelTarget = event.target.closest?.("[data-model-id]");
    if (modelTarget) selectModel(modelTarget.getAttribute("data-model-id"));
  });

  app.addEventListener("keydown", (event) => {
    if (!["Enter", " "].includes(event.key)) return;
    const modelTarget = event.target.closest?.("[data-model-id]");
    if (!modelTarget) return;
    event.preventDefault();
    selectModel(modelTarget.getAttribute("data-model-id"));
  });

  app.addEventListener("pointermove", (event) => {
    const bar = event.target.closest?.(".bar-group");
    const stage = event.target.closest?.("[data-chart-stage]") || app.querySelector("[data-chart-stage]");
    const tooltip = stage?.querySelector("[data-chart-tooltip]");
    if (!stage || !tooltip) return;
    if (!bar) {
      tooltip.classList.remove("visible");
      return;
    }
    const rect = stage.getBoundingClientRect();
    const x = clamp(event.clientX - rect.left + stage.scrollLeft + 12, 8, stage.scrollWidth - 245);
    const y = clamp(event.clientY - rect.top + 12, 8, stage.clientHeight - 82);
    tooltip.innerHTML = `<strong>${esc(bar.dataset.tooltipTitle)}</strong><span>${esc(bar.dataset.tooltipDetail)}</span>`;
    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${y}px`;
    tooltip.classList.add("visible");
  });

  app.addEventListener("pointerleave", () => {
    app.querySelector("[data-chart-tooltip]")?.classList.remove("visible");
  });

  window.addEventListener("popstate", () => {
    state.market = marketFromURL();
    state.conditionView = "all";
    state.selectedModel = "prior";
    render();
  });

  state.market = marketFromURL();
  writeMarketURL(state.market, true);
  setTheme();
  loadData();
})();
