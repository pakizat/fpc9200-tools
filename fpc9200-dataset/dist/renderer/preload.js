"use strict";
/**
 * Preload Script - 安全桥接 Main 和 Renderer
 */
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('electronAPI', {
    enrollTemplate: () => ipcRenderer.invoke('enroll-template'),
    captureSamples: (type) => ipcRenderer.invoke('capture-samples', type),
    runMatching: () => ipcRenderer.invoke('run-matching'),
    listSamples: () => ipcRenderer.invoke('list-samples'),
    listReports: () => ipcRenderer.invoke('list-reports'),
    getReport: (id) => ipcRenderer.invoke('get-report', id),
    deleteSample: (type, id) => ipcRenderer.invoke('delete-sample', type, id),
});
