/**
 * FPC 9200 Dataset Tool - Electron Main Process
 */

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const { exec } = require('child_process');
const { promisify } = require('util');

const execAsync = promisify(exec);

// 项目根目录（从 dist/main/ 向上 2 级）
const PROJECT_DIR = path.join(__dirname, '..', '..');
const DATA_DIR = path.join(PROJECT_DIR, 'data', 'fpc9200-dataset');
const SCRIPT = path.join(PROJECT_DIR, 'fpc9200-dataset.py');

let mainWindow: any = null;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, '..', 'renderer', 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
    title: 'FPC 9200 Dataset Tool',
  });

  mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

// ============ IPC Handlers ============

ipcMain.handle('enroll-template', async () => {
  try {
    const { stdout, stderr } = await execAsync(`python3 ${SCRIPT} --enroll-template`, { timeout: 300000 });
    return { success: true, output: stdout + stderr };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
});

ipcMain.handle('capture-samples', async (_event: any, sampleType: string) => {
  try {
    const { stdout, stderr } = await execAsync(`python3 ${SCRIPT} --capture ${sampleType}`, { timeout: 600000 });
    return { success: true, output: stdout + stderr };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
});

ipcMain.handle('run-matching', async () => {
  try {
    const { stdout, stderr } = await execAsync(`python3 ${SCRIPT} --match`, { timeout: 600000 });
    return { success: true, output: stdout + stderr };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
});

ipcMain.handle('list-samples', async () => {
  try {
    const genuineDir = path.join(DATA_DIR, 'genuine');
    const impostorDir = path.join(DATA_DIR, 'impostor');
    const genuine: any[] = [];
    const impostor: any[] = [];

    if (fs.existsSync(genuineDir)) {
      for (const f of fs.readdirSync(genuineDir)) {
        if (f.endsWith('.json')) {
          genuine.push(JSON.parse(fs.readFileSync(path.join(genuineDir, f), 'utf-8')));
        }
      }
    }
    if (fs.existsSync(impostorDir)) {
      for (const f of fs.readdirSync(impostorDir)) {
        if (f.endsWith('.json')) {
          impostor.push(JSON.parse(fs.readFileSync(path.join(impostorDir, f), 'utf-8')));
        }
      }
    }
    return { genuine, impostor };
  } catch (e: any) {
    return { genuine: [], impostor: [], error: e.message };
  }
});

ipcMain.handle('list-reports', async () => {
  try {
    const reportDir = path.join(DATA_DIR, 'reports');
    if (!fs.existsSync(reportDir)) return [];
    const reports: any[] = [];
    for (const f of fs.readdirSync(reportDir)) {
      if (f.endsWith('.json')) {
        reports.push(JSON.parse(fs.readFileSync(path.join(reportDir, f), 'utf-8')));
      }
    }
    return reports.sort((a, b) => b.id.localeCompare(a.id));
  } catch { return []; }
});

ipcMain.handle('get-report', async (_event: any, reportId: string) => {
  try {
    const file = path.join(DATA_DIR, 'reports', `report_${reportId}.json`);
    return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf-8')) : null;
  } catch { return null; }
});

ipcMain.handle('delete-sample', async (_event: any, sampleType: string, sampleId: string) => {
  try {
    const dir = path.join(DATA_DIR, sampleType);
    fs.unlinkSync(path.join(dir, `${sampleId}.bin`));
    fs.unlinkSync(path.join(dir, `${sampleId}.json`));
    return { success: true };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
});
