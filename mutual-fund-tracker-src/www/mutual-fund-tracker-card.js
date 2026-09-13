/* Mutual Fund Tracker — Home Assistant custom Lovelace card.
 * The card is intentionally self-contained so it can be served from /local/.
 */
(() => {
  const CARD_TYPE = 'mutual-fund-tracker-card';
  const FUND_COUNT_SUFFIX = '_fund_count';
  const REFRESH_SUFFIX = '_refresh_nav';
  const CARD_LOGO_URL = '/api/mutual_fund_tracker/mutual-fund-tracker-logo.png';

  const esc = (value) => String(value ?? '')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;')
    .replace(/>/g, '&gt;').replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');

  const numberValue = (value) => {
    if (value === null || value === undefined || value === '' || value === 'unknown' || value === 'unavailable') return null;
    const n = Number(String(value).replace(/,/g, ''));
    return Number.isFinite(n) ? n : null;
  };

  const inr = (value, decimals = 0) => {
    const n = numberValue(value);
    if (n === null) return '—';
    return `₹${new Intl.NumberFormat('en-IN', {
      minimumFractionDigits: decimals,
      maximumFractionDigits: decimals,
    }).format(n)}`;
  };

  const pct = (value) => {
    const n = numberValue(value);
    if (n === null) return '—';
    return `${n.toFixed(2)}%`;
  };

  const signedInr = (value) => {
    const n = numberValue(value);
    if (n === null) return '—';
    const text = inr(Math.abs(n));
    return n > 0 ? `+${text}` : n < 0 ? `-${text}` : text;
  };

  const signedClass = (value) => {
    const n = numberValue(value);
    return n === null ? 'neutral' : n > 0 ? 'positive' : n < 0 ? 'negative' : 'neutral';
  };

  const formatDate = (value) => {
    if (!value || value === 'unknown' || value === 'unavailable') return '—';
    const d = new Date(`${value}T00:00:00`);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleDateString(undefined, { day: '2-digit', month: 'short', year: 'numeric' });
  };

  const formatDateTime = (value) => {
    if (!value) return '—';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value);
    return d.toLocaleString(undefined, {
      day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit',
    });
  };

  const baseEntity = (fundCountEntity) => {
    if (!fundCountEntity || !fundCountEntity.endsWith(FUND_COUNT_SUFFIX)) return null;
    return fundCountEntity.slice(0, -FUND_COUNT_SUFFIX.length);
  };

  // HA object IDs are generated from the entity's display name, so the
  // logical field names do not always equal their entity-id suffixes.
  // Prefer the current production suffixes, with legacy aliases only as a
  // compatibility fallback. This also prevents accidentally selecting stale
  // orphaned legacy entities when the current entity exists.
  const RELATED_SUFFIX_ALIASES = {
    value: ['total_value', 'value'],
    invested: ['total_invested', 'invested'],
    profit: ['total_profit', 'profit'],
    profit_pct: ['total_profit_pct', 'total_profit_2', 'profit_pct'],
    day_change: ['daily_change', 'day_change'],
    month_change: ['monthly_change', 'month_change'],
    year_change: ['yearly_change', 'year_change'],
    xirr: ['portfolio_xirr', 'xirr'],
    nav_date: ['latest_nav_date', 'nav_date'],
  };

  const relatedSuffixes = (suffix) => RELATED_SUFFIX_ALIASES[suffix] || [suffix];

  const relatedEntity = (hass, fundCountEntity, suffix, domain = 'sensor') => {
    const suffixes = relatedSuffixes(suffix);
    const investorId = entityAttr(hass, fundCountEntity, 'investor_id');
    if (investorId !== undefined && investorId !== null) {
      for (const candidateSuffix of suffixes) {
        const match = Object.entries(hass?.states || {}).find(([id, state]) =>
          id.startsWith(`${domain}.`) &&
          id.endsWith(`_${candidateSuffix}`) &&
          String(state?.attributes?.investor_id) === String(investorId)
        );
        if (match) return match[0];
      }
    }
    const base = baseEntity(fundCountEntity);
    if (!base) return null;
    const baseObjectId = base.includes('.') ? base.slice(base.indexOf('.') + 1) : base;
    const states = hass?.states || {};
    for (const candidateSuffix of suffixes) {
      const candidate = `${domain}.${baseObjectId}_${candidateSuffix}`;
      if (states[candidate]) return candidate;
    }
    // Preserve the old deterministic fallback even when the entity is not
    // currently present in the HA state registry.
    return `${domain}.${baseObjectId}_${suffixes[0]}`;
  };

  const entityState = (hass, entityId) => (entityId && hass?.states?.[entityId]) || null;
  const entityValue = (hass, entityId) => entityState(hass, entityId)?.state;
  const entityAttr = (hass, entityId, attr) => entityState(hass, entityId)?.attributes?.[attr];

  const investorNameFromEntity = (hass, entityId) => {
    const friendly = entityAttr(hass, entityId, 'friendly_name') || entityId;
    return String(friendly).replace(/\s*Fund Count\s*$/i, '').replace(/^Mutual Funds\s*[–-]\s*/i, '').trim();
  };

  const discoverInvestorEntities = (hass) => Object.keys(hass?.states || {})
    .filter((id) => id.startsWith('sensor.') && id.endsWith(FUND_COUNT_SUFFIX))
    .filter((id) => !/(^|\.)[^.]*_all_fund_count$/.test(id) && !id.includes('_all_fund_count'))
    .sort((a, b) => investorNameFromEntity(hass, a).localeCompare(investorNameFromEntity(hass, b)));


  const SORT_OPTIONS = [
    ['value', 'Total Value'],
    ['invested', 'Invested'],
    ['profit', 'Profit'],
    ['profit_pct', 'Profit %'],
    ['xirr', 'XIRR'],
    ['day_change', 'Day Change'],
    ['day_pct', 'Day %'],
    ['month_change', 'Month Change'],
    ['month_pct', 'Month %'],
    ['sip_day', 'SIP Date'],
    ['fund_name', 'Fund Name'],
  ];

  const sortFunds = (funds, sortBy, direction) => {
    const dir = direction === 'asc' ? 1 : -1;
    return [...funds].sort((a, b) => {
      if (sortBy === 'fund_name') {
        return dir * String(a?.fund_name || '').localeCompare(String(b?.fund_name || ''), undefined, { sensitivity: 'base' });
      }
      const av = numberValue(a?.[sortBy]);
      const bv = numberValue(b?.[sortBy]);
      if (av === null && bv === null) return String(a?.fund_name || '').localeCompare(String(b?.fund_name || ''), undefined, { sensitivity: 'base' });
      if (av === null) return 1;
      if (bv === null) return -1;
      if (av === bv) return String(a?.fund_name || '').localeCompare(String(b?.fund_name || ''), undefined, { sensitivity: 'base' });
      return (av - bv) * dir;
    });
  };

  class MutualFundTrackerCardEditor extends HTMLElement {
    set hass(value) {
      this._hass = value;
      this._scheduleRender();
    }

    _scheduleRender() {
      if (this._renderTimer !== null) return;
      this._renderTimer = window.setTimeout(() => {
        this._renderTimer = null;
        this._render();
      }, 2000);
    }

    setConfig(config) {
      this._config = { ...config };
      this._render();
    }

    _selected() {
      const configured = Array.isArray(this._config?.investors) ? this._config.investors : [];
      return [...new Set(configured.filter(Boolean))];
    }

    _emit(investors) {
      this._config = { ...this._config, investors };
      this.dispatchEvent(new CustomEvent('config-changed', {
        detail: { config: this._config }, bubbles: true, composed: true,
      }));
      this._render();
    }

    _render() {
      if (!this._hass) return;
      const options = discoverInvestorEntities(this._hass);
      const selected = this._selected().filter((id) => options.includes(id));
      if (!this._config || !Array.isArray(this._config.investors)) {
        this._config = { ...(this._config || {}), investors: selected };
      }

      this.innerHTML = `
        <div class="mft-editor">
          <div class="mft-editor-title">Mutual Fund Tracker</div>
          <div class="mft-editor-subtitle">Select the investors that should be available in the card's investor dropdown.</div>
          <div class="mft-editor-list">
            ${options.length ? options.map((entityId) => `
              <label class="mft-editor-check">
                <input type="checkbox" data-investor="${esc(entityId)}" ${selected.includes(entityId) ? 'checked' : ''}>
                <span>${esc(investorNameFromEntity(this._hass, entityId))}</span>
              </label>
            `).join('') : '<div class="mft-editor-empty">No Mutual Fund Tracker investors found.</div>'}
          </div>
          <div class="mft-editor-selection">${selected.length} investor${selected.length === 1 ? '' : 's'} selected</div>
          <div class="mft-editor-sort">
            <label>Default fund sort
              <select class="mft-editor-sort-select">
                ${SORT_OPTIONS.map(([value, label]) => `<option value="${value}" ${(this._config?.sort_by || 'value') === value ? 'selected' : ''}>${label}</option>`).join('')}
              </select>
            </label>
            <button type="button" class="mft-editor-sort-direction" title="Toggle default sort direction">${(this._config?.sort_direction || 'desc') === 'asc' ? '↑ Ascending' : '↓ Descending'}</button>
          </div>
          <div class="mft-editor-help">The card itself will show one selected investor at a time using its Investor dropdown.</div>
        </div>
        <style>
          .mft-editor{font-family:var(--paper-font-body1_-_font-family,Arial,sans-serif);padding:8px 0;color:var(--primary-text-color);}
          .mft-editor-title{font-size:18px;font-weight:700;margin-bottom:4px;}
          .mft-editor-subtitle,.mft-editor-help{font-size:13px;color:var(--secondary-text-color);line-height:1.45;}
          .mft-editor-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;margin:14px 0 6px;}
          .mft-editor-check{display:flex;align-items:center;gap:9px;min-height:42px;padding:8px 10px;border:1px solid var(--divider-color);border-radius:9px;background:var(--card-background-color,#fff);cursor:pointer;}
          .mft-editor-check input{width:18px;height:18px;margin:0;accent-color:var(--primary-color);flex:0 0 auto;}
          .mft-editor-check span{font-size:13px;line-height:1.25;}
          .mft-editor-selection{font-size:12px;color:var(--secondary-text-color);margin:4px 0 12px;}
          .mft-editor-sort{display:grid;grid-template-columns:minmax(220px,1fr) auto;gap:8px;align-items:end;margin:12px 0;}
          .mft-editor-sort label{display:flex;flex-direction:column;gap:5px;font-size:12px;font-weight:700;color:var(--secondary-text-color);}
          .mft-editor-sort-select,.mft-editor-sort-direction{min-height:40px;padding:7px 10px;border:1px solid var(--divider-color);border-radius:8px;background:var(--card-background-color,#fff);color:var(--primary-text-color);font:inherit;font-weight:600;}
          .mft-editor-sort-direction{cursor:pointer;align-self:end;}
          .mft-editor-empty{padding:12px;border:1px dashed var(--divider-color);border-radius:10px;color:var(--secondary-text-color);grid-column:1/-1;}
          @media(max-width:650px){.mft-editor-list{grid-template-columns:1fr;}.mft-editor-sort{grid-template-columns:1fr;}.mft-editor-sort-direction{width:100%;}}
        </style>
      `;

      this.querySelectorAll('.mft-editor-check input').forEach((checkbox) => {
        checkbox.addEventListener('change', () => {
          const next = new Set(selected);
          const id = checkbox.dataset.investor;
          if (checkbox.checked) next.add(id);
          else next.delete(id);
          this._emit([...next].filter((id) => options.includes(id)));
        });
      });
      this.querySelector('.mft-editor-sort-select')?.addEventListener('change', (event) => {
        this._config = { ...this._config, sort_by: event.target.value };
        this.dispatchEvent(new CustomEvent('config-changed', {
          detail: { config: this._config }, bubbles: true, composed: true,
        }));
      });
      this.querySelector('.mft-editor-sort-direction')?.addEventListener('click', () => {
        const direction = this._config?.sort_direction === 'asc' ? 'desc' : 'asc';
        this._config = { ...this._config, sort_direction: direction };
        this.dispatchEvent(new CustomEvent('config-changed', {
          detail: { config: this._config }, bubbles: true, composed: true,
        }));
        this._render();
      });
    }
  }

  if (!customElements.get('mutual-fund-tracker-card-editor')) customElements.define('mutual-fund-tracker-card-editor', MutualFundTrackerCardEditor);

  class MutualFundTrackerCard extends HTMLElement {
    constructor() {
      super();
      this._config = { investors: [], sort_by: 'value', sort_direction: 'desc' };
      this._activeInvestorId = null;
      this._refreshingUntil = 0;
      this._renderTimer = null;
      this._hasRendered = false;
      this._hasMarketState = false;
      this._lastMarketState = null;
    }

    setConfig(config) {
      if (!config || !Array.isArray(config.investors)) throw new Error('Please select at least one investor in the visual editor.');
      const investors = [...new Set(config.investors.filter(Boolean))];
      this._config = { ...config, investors, sort_by: config.sort_by || 'value', sort_direction: config.sort_direction || 'desc' };
      if (!investors.includes(this._activeInvestorId)) this._activeInvestorId = investors[0] || null;
      this._render();
    }

    _findNseTradingDayEntityId(hass) {
      const states = hass?.states || {};
      const candidates = Object.keys(states).filter((id) => id.startsWith('binary_sensor.'));
      return candidates.find((id) => {
        const attrs = states[id]?.attributes || {};
        return Object.prototype.hasOwnProperty.call(attrs, 'nse_open') &&
          !Object.prototype.hasOwnProperty.call(attrs, 'market_open');
      }) || null;
    }

    _findNseTomorrowEntityId(hass) {
      const states = hass?.states || {};
      const candidates = Object.keys(states).filter((id) => id.startsWith('binary_sensor.'));
      return candidates.find((id) => {
        const attrs = states[id]?.attributes || {};
        return Object.prototype.hasOwnProperty.call(attrs, 'tomorrow_nse_open') &&
          !Object.prototype.hasOwnProperty.call(attrs, 'nse_open') &&
          !Object.prototype.hasOwnProperty.call(attrs, 'market_open');
      }) || null;
    }

    _findNseMarketEntityId(hass) {
      const states = hass?.states || {};
      const candidates = Object.keys(states).filter((id) => id.startsWith('binary_sensor.'));
      return candidates.find((id) => {
        const attrs = states[id]?.attributes || {};
        return Object.prototype.hasOwnProperty.call(attrs, 'market_open');
      }) || null;
    }

    set hass(value) {
      const tradingEntityId = this._findNseTradingDayEntityId(value);
      const tomorrowEntityId = this._findNseTomorrowEntityId(value);
      const marketEntityId = this._findNseMarketEntityId(value);
      const tradingState = tradingEntityId ? value?.states?.[tradingEntityId] : null;
      const tomorrowState = tomorrowEntityId ? value?.states?.[tomorrowEntityId] : null;
      const marketState = marketEntityId ? value?.states?.[marketEntityId] : null;
      const marketSignature = JSON.stringify([
        tradingEntityId,
        tradingState?.state ?? null,
        tradingState?.attributes?.nse_open ?? null,
        tomorrowEntityId,
        tomorrowState?.state ?? null,
        tomorrowState?.attributes?.tomorrow_nse_open ?? null,
        tradingState?.attributes?.tomorrow_date ?? null,
        tradingState?.attributes?.tomorrow_nse_open ?? null,
        tradingState?.attributes?.tomorrow_description ?? null,
        marketEntityId,
        marketState?.state ?? null,
        marketState?.attributes?.market_open ?? null,
        marketState?.attributes?.status ?? null,
        marketState?.attributes?.nifty?.value ?? null,
        marketState?.attributes?.nifty?.change ?? null,
        marketState?.attributes?.nifty?.percent_change ?? null,
        marketState?.attributes?.nifty?.timestamp ?? null,
      ]);
      const configured = (this._config?.investors || []).filter(Boolean);
      const investorSignature = configured.map((id) => {
        const base = entityState(value, id);
        if (!base) return [id, null];
        // Do not depend on investor_id attributes here. Those attributes are
        // intentionally not duplicated across every HA entity anymore.
        // relatedEntity() already resolves by the entity-id relationship when
        // investor_id is absent.
        const related = (suffix, domain = 'sensor') => {
          const relatedId = relatedEntity(value, id, suffix, domain);
          const relatedState = relatedId ? value?.states?.[relatedId] : null;
          return relatedState ? [relatedId, relatedState.state, relatedState.last_updated] : [null];
        };
        return [id, base.state, base.last_updated, ...related('value'), ...related('invested'), ...related('profit'), ...related('day_change'), ...related('month_change'), ...related('year_change'), ...related('xirr'), ...related('nav_date'), ...related('fund_details'), ...related('next_expected_sip_date'), ...related('sip_executed_today', 'binary_sensor'), ...related('delayed_nav_update', 'binary_sensor')];
      });
      const investorStateChanged = this._hasMarketState && this._lastInvestorState !== JSON.stringify(investorSignature);
      const marketStateChanged = this._hasMarketState && this._lastMarketState !== marketSignature;
      this._hass = value;
      this._lastMarketState = marketSignature;
      this._lastInvestorState = JSON.stringify(investorSignature);

      if (!this._hasRendered) {
        this._hasRendered = true;
        this._hasMarketState = true;
        this._render();
        return;
      }
      if (marketStateChanged || investorStateChanged) {
        if (this._renderTimer !== null) {
          window.clearTimeout(this._renderTimer);
          this._renderTimer = null;
        }
        this._render();
        return;
      }
      this._hasMarketState = true;
      this._scheduleRender();
    }

    _scheduleRender() {
      if (this._renderTimer !== null) return;
      this._renderTimer = window.setTimeout(() => {
        this._renderTimer = null;
        this._render();
      }, 30000);
    }

    disconnectedCallback() {
      if (this._renderTimer !== null) {
        window.clearTimeout(this._renderTimer);
        this._renderTimer = null;
      }
    }

    getCardSize() {
      return 12;
    }

    static getConfigElement() {
      return document.createElement('mutual-fund-tracker-card-editor');
    }

    static getStubConfig() {
      return { investors: [], sort_by: 'value', sort_direction: 'desc' };
    }

    async _refreshNav() {
      if (!this._hass) return;
      const button = Object.keys(this._hass.states || {}).find((id) => id.startsWith('button.') && id.endsWith(REFRESH_SUFFIX));
      if (!button) return;
      this._refreshingUntil = Date.now() + 5000;
      this._render();
      try {
        await this._hass.callService('button', 'press', { entity_id: button });
      } finally {
        window.setTimeout(() => {
          this._refreshingUntil = 0;
          this._render();
        }, 5000);
      }
    }

    _investorData(entityId) {
      const fundCountId = entityId;
      const valueId = relatedEntity(this._hass, fundCountId, 'value');
      const investedId = relatedEntity(this._hass, fundCountId, 'invested');
      const profitId = relatedEntity(this._hass, fundCountId, 'profit');
      const profitPctId = relatedEntity(this._hass, fundCountId, 'profit_pct');
      const dayId = relatedEntity(this._hass, fundCountId, 'day_change');
      const monthId = relatedEntity(this._hass, fundCountId, 'month_change');
      const yearId = relatedEntity(this._hass, fundCountId, 'year_change');
      const xirrId = relatedEntity(this._hass, fundCountId, 'xirr');
      const navDateId = relatedEntity(this._hass, fundCountId, 'nav_date');
      const nextSipId = relatedEntity(this._hass, fundCountId, 'next_expected_sip_date');
      const fundDetailsId = relatedEntity(this._hass, fundCountId, 'fund_details');
      const sipExecutedId = relatedEntity(this._hass, fundCountId, 'sip_executed_today', 'binary_sensor');
      const sipDelayedId = relatedEntity(this._hass, fundCountId, 'delayed_nav_update', 'binary_sensor');
      const fundState = entityState(this._hass, fundDetailsId);
      const nextSipState = entityState(this._hass, nextSipId);
      const sipState = entityState(this._hass, sipExecutedId);
      const sipExecutedAttributes = sipState?.attributes || {};
      return {
        id: entityId,
        name: investorNameFromEntity(this._hass, entityId),
        fundCount: entityValue(this._hass, fundCountId),
        value: entityValue(this._hass, valueId),
        invested: entityValue(this._hass, investedId),
        profit: entityValue(this._hass, profitId),
        profitPct: entityValue(this._hass, profitPctId),
        day: entityValue(this._hass, dayId),
        month: entityValue(this._hass, monthId),
        year: entityValue(this._hass, yearId),
        xirr: entityValue(this._hass, xirrId),
        navDate: entityValue(this._hass, navDateId),
        nextSipDate: entityValue(this._hass, nextSipId),
        nextSipDetails: Array.isArray(nextSipState?.attributes?.details) ? nextSipState.attributes.details : [],
        funds: Array.isArray(fundState?.attributes?.funds) ? fundState.attributes.funds : [],
        sipExecuted: Number(sipExecutedAttributes.count || 0) > 0,
        sipExecutedCount: Number(sipExecutedAttributes.count || 0),
        sipExecutedTotalAmount: Number(sipExecutedAttributes.total_amount || 0),
        sipExecutedDetails: Array.isArray(sipExecutedAttributes.details) ? sipExecutedAttributes.details : [],
        sipDelayedDetails: Array.isArray(entityState(this._hass, sipDelayedId)?.attributes?.details) ? entityState(this._hass, sipDelayedId).attributes.details : [],
      };
    }

    _render() {
      if (!this._hass) return;
      const configuredInvestors = (this._config?.investors || []).filter(Boolean);
      const investors = configuredInvestors.map((id) => this._investorData(id)).filter(Boolean);
      if (!this._activeInvestorId || !investors.some((i) => i.id === this._activeInvestorId)) {
        this._activeInvestorId = investors[0]?.id || null;
      }
      const activeInvestor = investors.find((i) => i.id === this._activeInvestorId) || investors[0] || null;
      const refreshButton = Object.keys(this._hass.states || {}).find((id) => id.startsWith('button.') && id.endsWith(REFRESH_SUFFIX));
      const tradingEntityId = this._findNseTradingDayEntityId(this._hass);
      const tomorrowEntityId = this._findNseTomorrowEntityId(this._hass);
      const marketEntityId = this._findNseMarketEntityId(this._hass);
      const tradingState = tradingEntityId ? entityState(this._hass, tradingEntityId) : null;
      const tomorrowState = tomorrowEntityId ? entityState(this._hass, tomorrowEntityId) : null;
      const marketState = marketEntityId ? entityState(this._hass, marketEntityId) : null;
      const tradingAttrs = tradingState?.attributes || {};
      const tomorrowAttrs = tomorrowState?.attributes || {};
      const marketAttrs = marketState?.attributes || {};
      const nseOpen = Boolean(tradingAttrs.nse_open ?? (tradingState?.state === 'on'));
      const tomorrowOpen = Boolean(tomorrowAttrs.tomorrow_nse_open ?? (tomorrowState?.state === 'on'));
      const marketOpen = Boolean(marketAttrs.market_open ?? (marketState?.state === 'on'));
      const tomorrowHoliday = tradingAttrs.tomorrow_nse_open === false &&
        tradingAttrs.tomorrow_type !== 'WEEKEND' &&
        Boolean(tradingAttrs.tomorrow_description);
      const nseReason = !nseOpen
        ? (tradingAttrs.description || tradingAttrs.type || 'Holiday')
        : tomorrowHoliday
          ? `Tomorrow ${tradingAttrs.tomorrow_description} Holiday`
          : 'Functional';
      const tomorrowReason = tomorrowOpen ? 'Open' : (tomorrowAttrs.tomorrow_description || 'Closed');
      const nifty = marketAttrs.nifty || {};
      const niftyText = nifty.value == null
        ? 'NIFTY 50 unavailable'
        : `NIFTY 50 ${Number(nifty.value).toLocaleString('en-IN', {maximumFractionDigits: 2})}${nifty.change == null ? '' : `(${nifty.change >= 0 ? '+' : ''}${Number(nifty.change).toFixed(2)})`}`;
      const niftyClass = nifty.value == null ? 'is-neutral' : (Number(nifty.change) > 0 ? 'is-positive' : Number(nifty.change) < 0 ? 'is-negative' : 'is-neutral');
      const niftyHtml = nifty.value != null
        ? `<span class="mft-nifty-label">Nifty50</span> <span class="mft-nifty-value ${niftyClass}">${esc(Number(nifty.value).toLocaleString('en-IN',{maximumFractionDigits:2}))}(${nifty.change >= 0 ? '+' : ''}${esc(Number(nifty.change).toFixed(2))})</span>`
        : `<span class="mft-nifty-label">Nifty50</span>`;
      const marketReason = marketOpen
        ? `Open · ${niftyText}`
        : marketAttrs.status === 'PRE_OPEN'
          ? 'Open @ 9:00am'
          : marketAttrs.status === 'HOLIDAY' || marketAttrs.status === 'WEEKEND'
            ? (marketAttrs.reason || marketAttrs.status)
            : 'Closed';
      const marketReasonHtml = marketOpen && nifty.value != null
        ? `Open · ${niftyHtml}`
        : (marketAttrs.status === 'CLOSED' && nifty.value != null)
          ? `Closed · ${niftyHtml}`
          : esc(marketReason);
      const nseTitle = nseOpen ? 'NSE' : 'NSE';
      const refreshing = Date.now() < this._refreshingUntil;
      const sortBy = this._config?.sort_by || 'value';
      const sortDirection = this._config?.sort_direction === 'asc' ? 'asc' : 'desc';

      this.innerHTML = `
        <ha-card class="mft-card">
          <div class="mft-header">
            <div class="mft-title-group">
              <div class="mft-brand"><img class="mft-logo" src="${CARD_LOGO_URL}" alt="Mutual Fund Tracker logo"><div><div class="mft-kicker">HOME ASSISTANT</div><div class="mft-title">Mutual Fund Tracker</div></div></div>
            </div>
            <div class="mft-header-actions">
              ${investors.length ? `<label class="mft-investor-control"><span>Investor</span><select class="mft-investor-select">
                ${investors.map((investor) => `<option value="${esc(investor.id)}" ${investor.id === activeInvestor?.id ? 'selected' : ''}>${esc(investor.name)}</option>`).join('')}
              </select></label>` : ''}
              <label class="mft-sort-control"><span>Sort</span><select class="mft-sort-select">
                ${SORT_OPTIONS.map(([value, label]) => `<option value="${value}" ${value === sortBy ? 'selected' : ''}>${label}</option>`).join('')}
              </select></label>
              <button class="mft-sort-direction" title="Toggle sort direction">${sortDirection === 'asc' ? '↑' : '↓'}</button>
              <button class="mft-refresh" ${refreshButton ? '' : 'disabled'} ${refreshing ? 'disabled' : ''}>
                <span class="mft-refresh-icon">↻</span>${refreshing ? 'Refreshing…' : 'Refresh NAV'}
              </button>
            </div>
          </div>
          <div class="mft-market-strip">
            <div class="mft-market-item">
              <div class="mft-status-switch ${nseOpen ? 'is-open' : 'is-closed'}" role="switch" aria-checked="${nseOpen}" title="${esc(nseReason)}">
                <span class="mft-status-track"><span class="mft-status-knob"></span></span>
              </div>
              <span class="mft-status-title">NSE Today</span>
              <div class="mft-reason-viewport"><div class="mft-reason">${esc(nseReason)}</div></div>
            </div>
            <div class="mft-market-item">
              <div class="mft-status-switch ${tomorrowOpen ? 'is-open' : 'is-closed'}" role="switch" aria-checked="${tomorrowOpen}" title="${esc(tomorrowReason)}">
                <span class="mft-status-track"><span class="mft-status-knob"></span></span>
              </div>
              <span class="mft-status-title">NSE Tomorrow</span>
              <div class="mft-reason-viewport"><div class="mft-reason">${esc(tomorrowReason)}</div></div>
            </div>
            <div class="mft-market-item">
              <div class="mft-status-switch ${marketOpen ? 'is-open' : 'is-closed'}" role="switch" aria-checked="${marketOpen}" title="${esc(marketReason)}">
                <span class="mft-status-track"><span class="mft-status-knob"></span></span>
              </div>
              <span class="mft-status-title">NSE Trading</span>
              <div class="mft-reason-viewport"><div class="mft-reason">${marketReasonHtml}</div></div>
            </div>
          </div>
          ${activeInvestor ? this._renderInvestor(activeInvestor, sortBy, sortDirection) : `
            <div class="mft-empty">Open the card editor and choose at least one investor.</div>
          `}
        </ha-card>
        <style>${this._styles()}</style>
      `;
      this.querySelector('.mft-investor-select')?.addEventListener('change', (event) => {
        this._activeInvestorId = event.target.value;
        this._render();
      });
      this.querySelector('.mft-refresh')?.addEventListener('click', () => this._refreshNav());
      this.querySelector('.mft-sort-select')?.addEventListener('change', (event) => {
        this._config.sort_by = event.target.value;
        this._render();
      });
      this.querySelector('.mft-sort-direction')?.addEventListener('click', () => {
        this._config.sort_direction = sortDirection === 'asc' ? 'desc' : 'asc';
        this._render();
      });
      window.requestAnimationFrame(() => this._updateReasonScroll());
    }

    _updateReasonScroll() {
      this.querySelectorAll('.mft-reason-viewport').forEach((viewport) => {
        const reason = viewport.querySelector('.mft-reason');
        if (!reason) return;
        reason.classList.remove('is-scrollable');
        reason.style.removeProperty('--mft-scroll-distance');
        const overflow = reason.scrollWidth - viewport.clientWidth;
        if (overflow > 4) {
          reason.style.setProperty('--mft-scroll-distance', `${-overflow}px`);
          reason.classList.add('is-scrollable');
        }
      });
    }

    _renderInvestor(investor, sortBy = 'value', sortDirection = 'desc') {
      const nextSipFunds = Array.isArray(investor.nextSipFunds) ? investor.nextSipFunds.filter(Boolean) : [];
      let visibleNextSipFunds = nextSipFunds;
      if (!visibleNextSipFunds.length && investor.nextSipDate) {
        const d = new Date(`${String(investor.nextSipDate)}T00:00:00`);
        if (!Number.isNaN(d.getTime())) {
          const lastDay = new Date(d.getFullYear(), d.getMonth() + 1, 0).getDate();
          visibleNextSipFunds = investor.funds.filter((fund) => {
            if (!fund?.sip_enabled || fund?.sip_day == null) return false;
            return Math.min(Number(fund.sip_day), lastDay) === d.getDate();
          }).map((fund) => fund.fund_name).filter(Boolean);
        }
      }
      const nextSipDetails = Array.isArray(investor.nextSipDetails) ? investor.nextSipDetails : [];
      const nextSipCount = nextSipDetails.length;
      const nextSipTotal = nextSipDetails.reduce((sum, d) => sum + (numberValue(d?.amount) || 0), 0);
      const sipText = investor.nextSipDate
        ? `${formatDate(investor.nextSipDate)}${nextSipCount ? ` · ${nextSipCount} fund${nextSipCount === 1 ? '' : 's'}` : ''}${nextSipTotal ? ` · ${inr(nextSipTotal)}` : ''}`
        : 'No active SIP scheduled';
      const sipFundNames = nextSipDetails.length
        ? nextSipDetails.map((d) => `${esc(d?.fund_name || '')}${d?.amount != null ? ` (${inr(d.amount)})` : ''}`).join(', ')
        : 'No fund scheduled';
      const sipExecutedDetails = Array.isArray(investor.sipExecutedDetails) ? investor.sipExecutedDetails : [];
      const sipExecutedCount = Number(investor.sipExecutedCount || 0);
      const sipExecutedTotal = Number(investor.sipExecutedTotalAmount || 0);
      const sipExecuted = investor.sipExecuted
        ? `SIP executed today · ${sipExecutedCount} fund${sipExecutedCount === 1 ? '' : 's'} · ${inr(sipExecutedTotal)}`
        : 'No SIP executed today';
      const sipExecutedFunds = sipExecutedDetails.map((d) => d?.fund_name).filter(Boolean);
      const sipDelayedDetails = Array.isArray(investor.sipDelayedDetails) ? investor.sipDelayedDetails : [];
      const sipDelayedCount = sipDelayedDetails.length;
      const sipDelayedTotal = sipDelayedDetails.reduce((sum, d) => sum + (numberValue(d?.amount) || numberValue(d?.sip_amount) || 0), 0);
      const sipDelayed = sipDelayedCount ? 'ON' : 'OFF';
      const sipExecutedDetail = sipExecutedFunds.length
        ? `${sipExecutedFunds.length === 1 ? 'Fund' : 'Funds'}: ${sipExecutedFunds.map((name) => esc(name)).join(', ')}`
        : '';
      const sipDelayedFunds = sipDelayedDetails.map((d) => d?.fund_name).filter(Boolean);
      const displayedFunds = Array.isArray(investor.funds) ? investor.funds : [];
      const todayChange = displayedFunds.length
        ? displayedFunds.reduce((sum, fund) => {
            const n = numberValue(fund?.day_change);
            return sum + (n === null ? 0 : n);
          }, 0)
        : numberValue(investor.day);
      const rows = sortFunds(displayedFunds, sortBy, sortDirection).map((fund) => {
        const dayClass = signedClass(fund.day_change);
        const monthClass = signedClass(fund.month_change);
        const profitClass = signedClass(fund.profit);
        return `<tr>
          <td class="fund-name"><div>${esc(fund.fund_name)}</div><small>${esc(fund.nav_date || '')}</small></td>
          <td>${fund.sip_enabled ? inr(fund.sip_amount) : '—'}</td>
          <td>${fund.sip_enabled && fund.sip_day ? esc(fund.sip_day) : '—'}</td>
          <td class="${dayClass}">${signedInr(fund.day_change)}</td>
          <td class="${dayClass}">${pct(fund.day_pct)}</td>
          <td class="${monthClass}">${signedInr(fund.month_change)}</td>
          <td class="${monthClass}">${pct(fund.month_pct)}</td>
          <td>${inr(fund.value)}</td>
          <td>${inr(fund.invested)}</td>
          <td class="${profitClass}">${signedInr(fund.profit)}</td>
          <td class="${profitClass}">${pct(fund.profit_pct)}</td>
          <td>${pct(fund.xirr)}</td>
        </tr>`;
      }).join('');

      return `
        <section class="mft-investor">
          <div class="mft-investor-head">
            <div>
              <div class="mft-investor-name">${esc(investor.name)}</div>
              <div class="mft-investor-sub">${esc(investor.fundCount || investor.funds.length || 0)} funds · NAV ${esc(formatDate(investor.navDate))}</div>
            </div>
          </div>
          <div class="mft-metrics">
            ${this._metric('Total Value', inr(investor.value))}
            ${this._metric('Invested', inr(investor.invested))}
            ${this._metric('Profit', `${signedInr(investor.profit)} <span class="mft-metric-sub">(${esc(pct(investor.profitPct))})</span>`, signedClass(investor.profit))}
            ${this._metric('Portfolio XIRR', pct(investor.xirr))}
            ${this._metric('Day Change', signedInr(todayChange), signedClass(todayChange))}
            ${this._metric('Month Change', signedInr(investor.month), signedClass(investor.month))}
          </div>
          <div class="mft-sip-row">
            <div class="mft-info-box">
              <span>Next SIP</span><strong>${esc(sipText)}</strong><small class="mft-info-detail"><b>Fund:</b> ${sipFundNames}</small>
            </div>
            <div class="mft-info-box ${investor.sipExecuted ? 'done' : ''}">
              <span>Today</span><strong>${esc(sipExecuted)}</strong>${sipExecutedDetail ? `<div class="mft-info-detail">${sipExecutedDetail}</div>` : ''}
            </div>
          </div>
          ${sipDelayedCount ? `<div class="mft-delayed-row">
            <div class="mft-info-box delayed">
              <span>Delayed NAV Update</span><strong>ON</strong>
              <div class="mft-info-detail">${sipDelayedCount} fund${sipDelayedCount === 1 ? '' : 's'} · ${inr(sipDelayedTotal)}${sipDelayedFunds.length ? ` · Funds: ${sipDelayedFunds.map(name => esc(name)).join(', ')}` : ''}</div>
            </div>
          </div>` : ''}
          <div class="mft-table-wrap">
            <table class="mft-table">
              <thead><tr>
                <th class="fund-col">Fund</th><th>SIP</th><th>SIP Date</th><th>Day</th><th>Day %</th><th>Month</th><th>Month %</th>
                <th>Value</th><th>Invested</th><th>Profit</th><th>Profit %</th><th>XIRR</th>
              </tr></thead>
              <tbody>${rows || '<tr><td colspan="12" class="mft-no-funds">No funds available.</td></tr>'}</tbody>
            </table>
          </div>
        </section>
      `;
    }

    _metric(label, value, cls = '') {
      return `<div class="mft-metric"><span>${esc(label)}</span><strong class="${cls}">${value}</strong></div>`;
    }

    _styles() {
      return `
        :host{display:block}
        .mft-card{overflow:hidden;background:var(--ha-card-background,var(--card-background-color,#1f1f1f));color:var(--primary-text-color,#fff);border:1px solid var(--ha-card-border-color,var(--divider-color,#333));border-radius:16px;font-family:var(--paper-font-body1_-_font-family,Roboto,Arial,sans-serif)}
        .mft-header{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:20px 22px;border-bottom:1px solid var(--divider-color)}
        .mft-brand{display:flex;align-items:center;gap:10px}.mft-logo{width:34px;height:34px;border-radius:8px;object-fit:contain;flex:0 0 34px}
        .mft-kicker{font-size:10px;font-weight:800;letter-spacing:.16em;color:var(--secondary-text-color);margin-bottom:3px}
        .mft-title{font-size:24px;font-weight:800;line-height:1.1}
        .mft-header-actions{display:flex;align-items:end;gap:9px;flex-wrap:wrap;justify-content:flex-end}
        .mft-market-strip{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;padding:7px 22px 9px;border-bottom:1px solid var(--divider-color);background:var(--secondary-background-color)}
        .mft-market-item{min-width:0;display:grid;grid-template-columns:52px 90px minmax(0,1fr);align-items:center;gap:8px;padding:3px 10px}
        .mft-market-item+.mft-market-item{border-left:1px solid var(--divider-color)}
        .mft-status-switch{display:flex;align-items:center;justify-content:flex-start;min-width:52px;user-select:none}
        .mft-status-track{position:relative;display:inline-flex;width:46px;height:24px;border-radius:999px;background:#b5b5b5;box-shadow:inset 0 0 0 1px rgba(0,0,0,.08)}
        .mft-status-knob{position:absolute;top:3px;left:3px;width:18px;height:18px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.35);transition:left .18s ease}
        .mft-status-switch.is-open .mft-status-track{background:#0dcc5a}
        .mft-status-switch.is-open .mft-status-knob{left:25px}
        .mft-status-title{font-size:13px;font-weight:900;color:var(--primary-text-color);white-space:nowrap}
        .mft-reason-viewport{min-width:0;overflow:hidden;white-space:nowrap}
        .mft-reason{display:inline-block;font-size:12px;font-weight:700;color:var(--primary-text-color);white-space:nowrap;min-width:100%}
        .mft-reason.is-scrollable{padding-right:35px;animation:mftReasonScroll 12s ease-in-out infinite alternate}.mft-nifty-label{color:var(--primary-text-color)}.mft-nifty-value.is-positive{color:#16a34a}.mft-nifty-value.is-negative{color:#dc2626}.mft-nifty-value.is-neutral{color:var(--primary-text-color)}
        @keyframes mftReasonScroll{0%,18%{transform:translateX(0)}82%,100%{transform:translateX(var(--mft-scroll-distance))}}
        .mft-investor-control,.mft-sort-control{display:flex;flex-direction:column;gap:3px;font-size:9px;text-transform:uppercase;letter-spacing:.05em;color:var(--secondary-text-color);font-weight:800}
        .mft-investor-select,.mft-sort-select{min-height:39px;min-width:170px;border:1px solid var(--divider-color);border-radius:9px;background:var(--secondary-background-color);color:var(--primary-text-color);padding:7px 9px;font:inherit;text-transform:none;letter-spacing:normal;font-size:12px}
        .mft-sort-select{min-width:128px}
        .mft-sort-direction{min-height:39px;min-width:39px;border:1px solid var(--divider-color);border-radius:9px;background:var(--secondary-background-color);color:var(--primary-text-color);font-size:18px;font-weight:800;cursor:pointer}
        .mft-sort-direction:hover{background:var(--card-background-color)}
        .mft-refresh{border:1px solid var(--primary-color);background:var(--primary-color);color:#fff;border-radius:9px;padding:10px 14px;font-weight:700;cursor:pointer;white-space:nowrap;min-height:39px}
        .mft-refresh:disabled{opacity:.5;cursor:default}
        .mft-refresh-icon{display:inline-block;margin-right:6px;font-size:15px}
        .mft-investor{padding:18px 22px 22px}
        .mft-investor + .mft-investor{border-top:8px solid var(--secondary-background-color,var(--ha-card-border-radius,transparent))}
        .mft-investor-head{display:flex;justify-content:space-between;align-items:end;gap:10px;margin-bottom:12px}
        .mft-investor-name{font-size:18px;font-weight:800}
        .mft-investor-sub{font-size:12px;color:var(--secondary-text-color);margin-top:3px}
        .mft-metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:9px}
        .mft-metric{border:1px solid var(--divider-color);border-radius:10px;padding:11px 12px;background:var(--card-background-color)}
        .mft-metric span{display:block;font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--secondary-text-color);font-weight:700}
        .mft-metric strong{display:block;font-size:17px;margin-top:5px;font-weight:800;white-space:nowrap}
        .mft-metric-sub{font-size:11px!important;margin-left:5px;color:var(--secondary-text-color)}
        .positive{color:var(--success-color,#0a8f2c)!important}.negative{color:var(--error-color,#ea3943)!important}.neutral{color:var(--primary-text-color)!important}
        .mft-sip-row{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:9px;margin:10px 0}.mft-delayed-row{display:block;margin:0 0 10px}
        .mft-info-box{border:1px solid var(--divider-color);border-radius:10px;padding:10px 12px;background:var(--secondary-background-color)}
        .mft-info-box span{display:block;font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:var(--secondary-text-color);font-weight:700}
        .mft-info-box strong{display:block;font-size:13px;margin-top:4px}.mft-info-detail{display:block;font-size:11px;line-height:1.35;color:var(--primary-text-color);margin-top:4px;white-space:normal}
        .mft-info-box.done{border-color:var(--success-color,#0a8f2c)}
        .mft-info-box.delayed{border-color:var(--warning-color,#f59e0b)}
        .mft-table-wrap{border:1px solid var(--divider-color);border-radius:10px;overflow:auto}
        .mft-table{width:100%;border-collapse:collapse;min-width:920px;font-size:12px}
        .mft-table th,.mft-table td{padding:8px 8px;border-bottom:1px solid var(--divider-color);text-align:center;vertical-align:middle;white-space:nowrap}
        .mft-table thead th{font-size:10px;text-transform:uppercase;letter-spacing:.04em;color:var(--secondary-text-color);background:var(--secondary-background-color);font-weight:800;position:sticky;top:0}
        .mft-table tbody tr:last-child td{border-bottom:0}
        .mft-table tbody tr:hover{background:var(--secondary-background-color)}
        .mft-table .fund-col,.mft-table .fund-name{text-align:left}
        .fund-name{min-width:270px;max-width:360px;white-space:normal!important}
        .fund-name div{font-weight:700;line-height:1.25}.fund-name small{display:block;color:var(--secondary-text-color);font-size:9px;margin-top:2px}
        .mft-no-funds{text-align:center!important;color:var(--secondary-text-color);padding:18px!important}
        .mft-empty{padding:28px 22px;color:var(--secondary-text-color);font-size:14px}
        @media(max-width:900px){.mft-header{align-items:flex-start;flex-direction:column}.mft-header-actions{width:100%;justify-content:stretch}.mft-sort-control{flex:1}.mft-sort-select{width:100%}.mft-refresh{flex:1}.mft-market-strip{grid-template-columns:1fr}.mft-market-item{grid-template-columns:52px 72px minmax(0,1fr);padding-left:0;padding-right:0}.mft-market-item+.mft-market-item{border-left:0;border-top:1px solid var(--divider-color);padding-top:8px;margin-top:4px}.mft-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.mft-sip-row{grid-template-columns:1fr}.mft-investor{padding-left:16px;padding-right:16px}}
        @media(max-width:520px){.mft-title{font-size:21px}.mft-metrics{grid-template-columns:1fr 1fr}.mft-metric strong{font-size:15px}}
      `;
    }
  }

  if (!customElements.get(CARD_TYPE)) customElements.define(CARD_TYPE, MutualFundTrackerCard);
  window.customCards = window.customCards || [];
  if (!window.customCards.some((entry) => entry.type === CARD_TYPE)) {
    window.customCards.push({
      type: CARD_TYPE,
      name: 'Mutual Fund Tracker',
      description: 'Professional multi-investor mutual fund portfolio card with SIP and NAV information.',
      preview: true,
    });
  }
})();
