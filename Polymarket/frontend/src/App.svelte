<script>
  import { onMount } from 'svelte';
  import { fetchJSON, keepalive } from './api.js';

  let uptime = '—';
  let status = null;
  let balance = null;
  let error = '';
  let lastUpdated = '';

  onMount(() => {
    load();
    const id = setInterval(load, 30000);
    keepalive();
    return () => clearInterval(id);
  });

  async function load() {
    error = '';
    try {
      const [u, s, b] = await Promise.all([
        fetchJSON('/uptime'),
        fetchJSON('/status'),
        fetchJSON('/balance')
      ]);
      uptime = u.uptime;
      status = s;
      balance = b;
      lastUpdated = new Date().toLocaleString();
    } catch (e) {
      error = e.message || 'Connection failed';
    }
  }

  function pnlClass(v) {
    if (v == null) return '';
    return v >= 0 ? 'text-success-500' : 'text-error-500';
  }

  function formatPnl(v) {
    if (v == null) return '—';
    return (v >= 0 ? '+' : '') + v.toFixed(2);
  }
</script>

<div class="container mx-auto max-w-6xl p-6">
  <h1 class="text-4xl font-bold mb-1">Quant</h1>
  <p class="text-surface-500 dark:text-surface-400 mb-8">Copy Trading Dashboard</p>

  {#if error}
    <div class="bg-error-500/10 border border-error-500 text-error-500 rounded-xl p-4 mb-6">{error}</div>
  {/if}

  <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4 mb-8">
    <div class="card p-4">
      <p class="text-sm uppercase tracking-wider text-surface-500 dark:text-surface-400 mb-1">Uptime</p>
      <p class="text-3xl font-bold">{uptime}</p>
    </div>
    <div class="card p-4">
      <p class="text-sm uppercase tracking-wider text-surface-500 dark:text-surface-400 mb-1">Live Trading</p>
      <p class="text-3xl font-bold">
        {#if status?.live_trading}
          <span class="text-success-500">Live</span>
        {:else}
          <span class="text-warning-500">Dry Run</span>
        {/if}
      </p>
    </div>
    <div class="card p-4">
      <p class="text-sm uppercase tracking-wider text-surface-500 dark:text-surface-400 mb-1">Tracked Wallets</p>
      <p class="text-3xl font-bold">{status?.tracked_wallets ?? '—'}</p>
    </div>
    <div class="card p-4">
      <p class="text-sm uppercase tracking-wider text-surface-500 dark:text-surface-400 mb-1">Trades Copied</p>
      <p class="text-3xl font-bold">{status?.trades_copied ?? '—'}</p>
    </div>
    <div class="card p-4">
      <p class="text-sm uppercase tracking-wider text-surface-500 dark:text-surface-400 mb-1">Total P&amp;L</p>
      <p class="text-3xl font-bold {pnlClass(balance?.total_realized_pnl)}">{formatPnl(balance?.total_realized_pnl)}</p>
    </div>
    <div class="card p-4">
      <p class="text-sm uppercase tracking-wider text-surface-500 dark:text-surface-400 mb-1">Win Rate</p>
      <p class="text-3xl font-bold">{balance?.win_rate || '—'}</p>
    </div>
    <div class="card p-4">
      <p class="text-sm uppercase tracking-wider text-surface-500 dark:text-surface-400 mb-1">Position Value</p>
      <p class="text-3xl font-bold">{balance?.position_value?.toFixed(2) ?? '—'}</p>
    </div>
    <div class="card p-4">
      <p class="text-sm uppercase tracking-wider text-surface-500 dark:text-surface-400 mb-1">Total Trades</p>
      <p class="text-3xl font-bold">{balance?.total_trades ?? '—'}</p>
    </div>
  </div>

  <h2 class="text-2xl font-bold mb-4">Open Positions</h2>
  {#if balance?.open_positions?.length}
    <div class="overflow-x-auto mb-8">
      <table class="table w-full">
        <thead>
          <tr>
            <th>Market</th>
            <th>Outcome</th>
            <th>Size</th>
            <th>Avg Price</th>
            <th>Cur Price</th>
            <th>Value</th>
          </tr>
        </thead>
        <tbody>
          {#each balance.open_positions as pos}
            <tr>
              <td>{pos.title || '—'}</td>
              <td>{pos.outcome || '—'}</td>
              <td>{parseFloat(pos.size).toFixed(2)}</td>
              <td>{parseFloat(pos.avgPrice).toFixed(4)}</td>
              <td>{parseFloat(pos.curPrice).toFixed(4)}</td>
              <td>{(parseFloat(pos.size) * parseFloat(pos.curPrice)).toFixed(2)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>
  {:else}
    <p class="text-surface-500 dark:text-surface-400 mb-8">No open positions</p>
  {/if}

  <h2 class="text-2xl font-bold mb-4">Scan Status</h2>
  <div class="overflow-x-auto mb-4">
    <table class="table w-full">
      <thead>
        <tr><th>Metric</th><th>Value</th></tr>
      </thead>
      <tbody>
        <tr><td>Wallet</td><td>{status?.tracked_wallets ?? 0} tracked</td></tr>
        <tr><td>Scan Count</td><td>{status?.scan_count ?? 0}</td></tr>
        <tr><td>Store Wallets</td><td>{status?.store_wallets ?? 0}</td></tr>
        <tr><td>Store Trades</td><td>{status?.store_trades ?? 0}</td></tr>
        <tr><td>Copy Amount</td><td>${status?.copy_amount_usd ?? 10}</td></tr>
        <tr><td>Scan Interval</td><td>{status?.scan_interval ?? '—'}</td></tr>
        <tr><td>Last Scan</td><td>{status?.last_scan ?? '—'}</td></tr>
      </tbody>
    </table>
  </div>

  <p class="text-sm text-surface-500 dark:text-surface-400 text-right">Last updated: {lastUpdated}</p>
</div>
