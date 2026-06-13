/**
 * FPC 9200 Dataset Tool - Electron Main Process
 */

const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const { execFile, spawn } = require('child_process');
const { promisify } = require('util');

const execFileAsync = promisify(execFile);

// 项目根目录（从 dist/main/ 向上 2 级）
const PROJECT_DIR = path.join(__dirname, '..', '..');
const REPO_DIR = path.join(PROJECT_DIR, '..', '..');
const DATA_DIR = process.env.FPC9200_DATA_DIR || path.join(REPO_DIR, 'data', 'fpc9200-dataset');
const SCRIPT = path.join(PROJECT_DIR, 'fpc9200-dataset.py');
const SCRIPT_ENV = { ...process.env, FPC9200_DATA_DIR: DATA_DIR };

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

async function runScript(args: string[], timeout: number) {
  return execFileAsync('python3', [SCRIPT, ...args], {
    cwd: PROJECT_DIR,
    env: SCRIPT_ENV,
    timeout,
    maxBuffer: 1024 * 1024 * 20,
  });
}

ipcMain.handle('enroll-template', async () => {
  try {
    const { stdout, stderr } = await runScript(['--enroll-template'], 300000);
    return { success: true, output: stdout + stderr };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
});

ipcMain.handle('capture-samples', async (_event: any, sampleType: string) => {
  try {
    if (!['genuine', 'impostor'].includes(sampleType)) {
      return { success: false, error: 'invalid sample type' };
    }
    const { stdout, stderr } = await runScript(['--capture-once', sampleType], 600000);
    return { success: true, output: stdout + stderr };
  } catch (e: any) {
    return { success: false, error: e.message };
  }
});

ipcMain.handle('run-matching', async () => {
  return new Promise((resolve) => {
    const child = spawn('python3', ['-u', SCRIPT, '--match'], {
      cwd: PROJECT_DIR,
      env: { ...SCRIPT_ENV, PYTHONUNBUFFERED: '1' },
    });
    let output = '';

    const append = (data: Buffer) => {
      const text = data.toString();
      output += text;
      if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.webContents.send('matching-output', text);
      }
    };

    child.stdout.on('data', append);
    child.stderr.on('data', append);
    child.on('error', (e: Error) => {
      resolve({ success: false, output, error: e.message });
    });
    child.on('close', (code: number) => {
      resolve({
        success: code === 0,
        output,
        error: code === 0 ? undefined : `matching exited with code ${code}`,
      });
    });
  });
});

ipcMain.handle('get-status', async () => {
  const countJson = (dir: string) => {
    if (!fs.existsSync(dir)) return 0;
    return fs.readdirSync(dir).filter((f: string) => f.endsWith('.json')).length;
  };

  return {
    dataDir: DATA_DIR,
    templateExists: fs.existsSync(path.join(DATA_DIR, 'template.f9rm')),
    genuineCount: countJson(path.join(DATA_DIR, 'genuine')),
    impostorCount: countJson(path.join(DATA_DIR, 'impostor')),
    reportCount: countJson(path.join(DATA_DIR, 'reports')),
  };
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
