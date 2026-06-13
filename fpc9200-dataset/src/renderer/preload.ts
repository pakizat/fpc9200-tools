/**
 * Preload Script - 安全桥接 Main 和 Renderer
 */

const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  enrollTemplate: () => ipcRenderer.invoke('enroll-template'),
  captureSamples: (type: string) => ipcRenderer.invoke('capture-samples', type),
  runMatching: () => ipcRenderer.invoke('run-matching'),
  onMatchingOutput: (callback: (chunk: string) => void) => {
    const listener = (_event: any, chunk: string) => callback(chunk);
    ipcRenderer.on('matching-output', listener);
    return () => ipcRenderer.removeListener('matching-output', listener);
  },
  getStatus: () => ipcRenderer.invoke('get-status'),
  listSamples: () => ipcRenderer.invoke('list-samples'),
  listReports: () => ipcRenderer.invoke('list-reports'),
  getReport: (id: string) => ipcRenderer.invoke('get-report', id),
  deleteSample: (type: string, id: string) => ipcRenderer.invoke('delete-sample', type, id),
});
