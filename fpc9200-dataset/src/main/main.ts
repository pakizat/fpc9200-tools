/**
 * FPC 9200 Dataset Tool - Electron Main Process
 */

import { app, BrowserWindow, ipcMain, dialog } from 'electron';
import { exec } from 'child_process';
import { promisify } from 'util';
import * as path from 'path';
import * as fs from 'fs';

const execAsync = promisify(exec);

const DATA_DIR = path.join(__dirname, '..', '..', 'data', 'fpc9200-dataset');
const SCRIPT = path.join(__dirname, 'fpc9200-dataset.py');

let mainWindow: BrowserWindow | null = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'src', 'renderer', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title: 'FPC 9200 Dataset Tool',
  });

  mainWindow.loadFile(path.join(__dirname, 'src', 'renderer', 'index.html'));

  // Open DevTools in development
  // mainWindow.webContents.openDevTools();
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

// ============ IPC Handlers ============

// 录入模板
ipcMain.handle('enroll-template', async () => {
  try {
    const { stdout, stderr } = await execAsync(`python3 ${SCRIPT} --enroll-template`, { timeout: 300000 });
    return { success: true, output: stdout + stderr };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
});

// 录制样本
ipcMain.handle('capture-samples', async (event, sampleType: string) => {
  try {
    // 使用 PTY 进行交互式会话
    const { stdout, stderr } = await execAsync(
      `python3 ${SCRIPT} --capture ${sampleType}`,
      { timeout: 600000 }
    );
    return { success: true, output: stdout + stderr };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
});

// 匹配计算
ipcMain.handle('run-matching', async () => {
  try {
    const { stdout, stderr } = await execAsync(`python3 ${SCRIPT} --match`, { timeout: 600000 });
    return { success: true, output: stdout + stderr };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
});

// 列出样本
ipcMain.handle('list-samples', async () => {
  try {
    const genuineDir = path.join(DATA_DIR, 'genuine');
    const impostorDir = path.join(DATA_DIR, 'impostor');

    const genuine: any[] = [];
    const impostor: any[] = [];

    if (fs.existsSync(genuineDir)) {
      for (const f of fs.readdirSync(genuineDir)) {
        if (f.endsWith('.json')) {
          const meta = JSON.parse(fs.readFileSync(path.join(genuineDir, f), 'utf-8'));
          genuine.push(meta);
        }
      }
    }

    if (fs.existsSync(impostorDir)) {
      for (const f of fs.readdirSync(impostorDir)) {
        if (f.endsWith('.json')) {
          const meta = JSON.parse(fs.readFileSync(path.join(impostorDir, f), 'utf-8'));
          impostor.push(meta);
        }
      }
    }

    return { genuine, impostor };
  } catch (e: any) {
    return { genuine: [], impostor: [], error: e.message };
  }
});

// 列出报告
ipcMain.handle('list-reports', async () => {
  try {
    const reportDir = path.join(DATA_DIR, 'reports');
    if (!fs.existsSync(reportDir)) return [];

    const reports: any[] = [];
    for (const f of fs.readdirSync(reportDir)) {
      if (f.endsWith('.json')) {
        const report = JSON.parse(fs.readFileSync(path.join(reportDir, f), 'utf-8'));
        reports.push(report);
      }
    }
    return reports.sort((a, b) => b.id.localeCompare(a.id));
  } catch (e: any) {
    return [];
  }
});

// 获取报告详情
ipcMain.handle('get-report', async (event, reportId: string) => {
  try {
    const reportFile = path.join(DATA_DIR, 'reports', `report_${reportId}.json`);
    if (!fs.existsSync(reportFile)) return null;
    return JSON.parse(fs.readFileSync(reportFile, 'utf-8'));
  } catch {
    return null;
  }
});

// 删除样本
ipcMain.handle('delete-sample', async (event, sampleType: string, sampleId: string) => {
  try {
    const dir = path.join(DATA_DIR, sampleType);
    fs.unlinkSync(path.join(dir, `${sampleId}.bin`));
    fs.unlinkSync(path.join(dir, `${sampleId}.json`));
    return { success: true };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
});
