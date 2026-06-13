/**
 * Preload Script - 安全桥接 Main 和 Renderer
 */

import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('electronAPI', {
  enrollTemplate: () => ipcRenderer.invoke('enroll-template'),
  captureSamples: (type: string) => ipcRenderer.invoke('capture-samples', type),
  runMatching: () => ipcRenderer.invoke('run-matching'),
  listSamples: () => ipcRenderer.invoke('list-samples'),
  listReports: () => ipcRenderer.invoke('list-reports'),
  getReport: (id: string) => ipcRenderer.invoke('get-report', id),
  deleteSample: (type: string, id: string) => ipcRenderer.invoke('delete-sample', type, id),
});
