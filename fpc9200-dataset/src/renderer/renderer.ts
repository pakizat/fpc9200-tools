/**
 * FPC 9200 Dataset Tool - Renderer Process
 */

export {};

declare global {
  interface Window {
    electronAPI: {
      enrollTemplate: () => Promise<{ success: boolean; output?: string; error?: string }>;
      captureSamples: (type: string) => Promise<{ success: boolean; output?: string; error?: string }>;
      runMatching: () => Promise<{ success: boolean; output?: string; error?: string }>;
      listSamples: () => Promise<{ genuine: any[]; impostor: any[] }>;
      listReports: () => Promise<any[]>;
      getReport: (id: string) => Promise<any>;
      deleteSample: (type: string, id: string) => Promise<{ success: boolean }>;
    };
  }
}

// ============ 状态 ============
const state: {
  currentReport: any | null;
  genuineSamples: any[];
  impostorSamples: any[];
  reports: any[];
  templateExists: boolean;
} = {
  currentReport: null,
  genuineSamples: [],
  impostorSamples: [],
  reports: [],
  templateExists: false,
};

// ============ 导航 ============
document.querySelectorAll('.nav-item').forEach(item => {
  item.addEventListener('click', () => {
    const page = (item as HTMLElement).dataset.page;
    if (!page) return;

    // 更新导航
    document.querySelectorAll('.nav-item').forEach(i => i.classList.remove('active'));
    item.classList.add('active');

    // 切换页面
    document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
    document.getElementById(`page-${page}`)?.classList.add('active');

    // 刷新数据
    if (page === 'samples') loadSamples();
    if (page === 'reports') loadReports();
    if (page === 'dashboard') updateDashboard();
  });
});

// ============ 仪表盘 ============
function updateDashboard() {
  document.getElementById('genuine-count')!.textContent = String(state.genuineSamples.length);
  document.getElementById('impostor-count')!.textContent = String(state.impostorSamples.length);
  document.getElementById('report-count')!.textContent = String(state.reports.length);

  const templateExists = state.templateExists ?? false;
  const el = document.getElementById('stat-template')!;
  el.textContent = templateExists ? '✓' : '—';
  el.style.color = templateExists ? 'var(--success)' : 'var(--text-secondary)';
}

// ============ 录入模板 ============
async function startEnroll() {
  const btn = document.getElementById('btn-enroll') as HTMLButtonElement;
  const status = document.getElementById('enroll-status')!;
  const log = document.getElementById('enroll-log')!;

  btn.disabled = true;
  status.innerHTML = '<div class="status status-warning"><span class="status-dot"></span>正在录入，请按提示放置手指 7 次...</div>';
  log.style.display = 'block';
  log.textContent = '';

  try {
    const result = await window.electronAPI.enrollTemplate();
    log.textContent = result.output || '';

    if (result.success) {
      status.innerHTML = '<div class="status status-success"><span class="status-dot"></span>✓ 模板录入成功!</div>';
    } else {
      status.innerHTML = `<div class="status status-error"><span class="status-dot"></span>✗ 录入失败: ${result.error}</div>`;
    }
  } catch (e: any) {
    status.innerHTML = `<div class="status status-error"><span class="status-dot"></span>✗ 错误: ${e.message}</div>`;
  }

  btn.disabled = false;
  updateDashboard();
}

// ============ 录制样本 ============
async function startCapture(type: string) {
  const status = document.getElementById('capture-status')!;
  const log = document.getElementById('capture-log')!;

  const typeName = type === 'genuine' ? '正确手指' : '错误手指';
  status.innerHTML = `<div class="status status-warning"><span class="status-dot"></span>正在录制 ${typeName} 样本，请按提示放置手指...</div>`;
  log.style.display = 'block';
  log.textContent = '';

  try {
    const result = await window.electronAPI.captureSamples(type);
    log.textContent = result.output || '';

    if (result.success) {
      status.innerHTML = `<div class="status status-success"><span class="status-dot"></span>✓ 录制完成</div>`;
    } else {
      status.innerHTML = `<div class="status status-error"><span class="status-dot"></span>✗ 录制失败: ${result.error}</div>`;
    }
  } catch (e: any) {
    status.innerHTML = `<div class="status status-error"><span class="status-dot"></span>✗ 错误: ${e.message}</div>`;
  }

  loadSamples();
  updateDashboard();
}

// ============ 匹配计算 ============
async function startMatching() {
  const status = document.getElementById('match-status')!;
  const log = document.getElementById('match-log')!;

  status.innerHTML = '<div class="status status-warning"><span class="status-dot"></span>正在执行匹配计算，请勿操作...</div>';
  log.style.display = 'block';
  log.textContent = '';

  try {
    const result = await window.electronAPI.runMatching();
    log.textContent = result.output || '';

    if (result.success) {
      status.innerHTML = '<div class="status status-success"><span class="status-dot"></span>✓ 匹配计算完成!</div>';
      loadReports();
    } else {
      status.innerHTML = `<div class="status status-error"><span class="status-dot"></span>✗ 匹配失败: ${result.error}</div>`;
    }
  } catch (e: any) {
    status.innerHTML = `<div class="status status-error"><span class="status-dot"></span>✗ 错误: ${e.message}</div>`;
  }

  updateDashboard();
}

// ============ 加载样本 ============
async function loadSamples() {
  try {
    const data = await window.electronAPI.listSamples();
    state.genuineSamples = data.genuine || [];
    state.impostorSamples = data.impostor || [];

    renderSampleList('genuine', state.genuineSamples);
    renderSampleList('impostor', state.impostorSamples);

    document.getElementById('genuine-count')!.textContent = String(state.genuineSamples.length);
    document.getElementById('impostor-count')!.textContent = String(state.impostorSamples.length);
  } catch (e) {
    console.error('Failed to load samples:', e);
  }
}

function renderSampleList(type: string, samples: any[]) {
  const container = document.getElementById(`${type}-list`)!;
  const countEl = document.getElementById(`${type}-count`)!;
  if (countEl) countEl.textContent = String(samples.length);

  if (samples.length === 0) {
    container.innerHTML = '<div class="empty-state"><div class="icon">📭</div><p>暂无样本</p></div>';
    return;
  }

  container.innerHTML = samples.map(s => {
    const q = s.quality || {};
    const qualityPct = Math.min(100, Math.round((q.stddev || 0) / 40 * 100));
    return `
      <div class="sample-card" onclick="showSampleDetail('${type}', '${s.id}')">
        <div class="id">${s.id}</div>
        <div class="meta">stddev=${q.stddev || '?'} contrast=${q.contrast || '?'}</div>
        <div class="quality-bar"><div class="quality-fill" style="width:${qualityPct}%"></div></div>
      </div>
    `;
  }).join('');
}

function showSampleDetail(type: string, id: string) {
  // TODO: 显示样本详情对话框
  console.log('Show sample detail:', type, id);
}

// ============ 加载报告 ============
async function loadReports() {
  try {
    state.reports = await window.electronAPI.listReports();
    renderReports();
  } catch (e) {
    console.error('Failed to load reports:', e);
  }
}

function renderReports() {
  const container = document.getElementById('reports-container')!;

  if (state.reports.length === 0) {
    container.innerHTML = '<div class="empty-state"><div class="icon">📈</div><p>暂无匹配报告，请先执行「匹配计算」</p></div>';
    return;
  }

  container.innerHTML = state.reports.map(r => {
    const s = r.stats || {};
    const genRate = ((s.genuine_rate || 0) * 100).toFixed(1);
    const impFAR = ((s.impostor_far || 0) * 100).toFixed(1);

    return `
      <div class="card" onclick="showReportDetail('${r.id}')">
        <div class="card-title">📊 报告 ${r.id}</div>
        <div class="stats-grid">
          <div class="stat-card">
            <div class="value">${s.genuine_count || 0}</div>
            <div class="label">正确样本</div>
          </div>
          <div class="stat-card">
            <div class="value" style="color:var(--success)">${genRate}%</div>
            <div class="label">匹配率</div>
          </div>
          <div class="stat-card">
            <div class="value">${s.impostor_count || 0}</div>
            <div class="label">错误样本</div>
          </div>
          <div class="stat-card">
            <div class="value" style="color:var(--danger)">${impFAR}%</div>
            <div class="label">误匹配率 (FAR)</div>
          </div>
        </div>
      </div>
    `;
  }).join('');
}

function showReportDetail(reportId: string) {
  window.electronAPI.getReport(reportId).then(report => {
    if (!report) return;
    state.currentReport = report;

    // 显示详细报告对话框
    const s = report.stats || {};
    const content = `
      <div style="padding: 20px;">
        <h3>报告详情: ${report.id}</h3>

        <h4 style="margin-top:20px">统计摘要</h4>
        <div class="stats-grid">
          <div class="stat-card"><div class="value">${s.genuine_count || 0}</div><div class="label">正确样本</div></div>
          <div class="stat-card"><div class="value" style="color:var(--success)">${((s.genuine_rate||0)*100).toFixed(1)}%</div><div class="label">匹配率</div></div>
          <div class="stat-card"><div class="value">${s.impostor_count || 0}</div><div class="label">错误样本</div></div>
          <div class="stat-card"><div class="value" style="color:var(--danger)">${((s.impostor_far||0)*100).toFixed(1)}%</div><div class="label">FAR</div></div>
        </div>

        <h4 style="margin-top:20px">正确样本分数明细</h4>
        <table class="score-table">
          <tr><th>样本ID</th><th>Score</th><th>Raw</th><th>Center</th><th>结果</th></tr>
          ${(report.genuine_results||[]).map((r:any) => `
            <tr>
              <td>${r.sample_id}</td>
              <td>${r.score}</td>
              <td>${r.raw_score}</td>
              <td>${r.center_score}</td>
              <td><span class="score-badge ${r.matched?'score-match':'score-nomatch'}">${r.matched?'✓':'✗'}</span></td>
            </tr>
          `).join('')}
        </table>

        <h4 style="margin-top:20px">错误样本分数明细</h4>
        <table class="score-table">
          <tr><th>样本ID</th><th>Score</th><th>Raw</th><th>Center</th><th>结果</th></tr>
          ${(report.impostor_results||[]).map((r:any) => `
            <tr>
              <td>${r.sample_id}</td>
              <td>${r.score}</td>
              <td>${r.raw_score}</td>
              <td>${r.center_score}</td>
              <td><span class="score-badge ${r.matched?'score-nomatch':'score-match'}">${r.matched?'✓(误)':'✓'}</span></td>
            </tr>
          `).join('')}
        </table>
      </div>
    `;

    // 创建模态框
    const modal = document.createElement('div');
    modal.style.cssText = 'position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,0.8);z-index:1000;display:flex;align-items:center;justify-content:center;';
    modal.innerHTML = `<div style="background:var(--bg-secondary);border-radius:12px;max-width:800px;max-height:80vh;overflow-y:auto;">${content}</div>`;
    modal.onclick = () => modal.remove();
    document.body.appendChild(modal);
  });
}

// ============ 初始化 ============
window.addEventListener('DOMContentLoaded', () => {
  loadSamples();
  loadReports();
  updateDashboard();
});

// 暴露给 HTML
(window as any).startEnroll = startEnroll;
(window as any).startCapture = startCapture;
(window as any).startMatching = startMatching;
