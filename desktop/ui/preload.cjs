const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvis', {
  call: (method, params) => ipcRenderer.invoke('jarvis:call', method, params ?? {}),
  ui: (action, params) => ipcRenderer.invoke('jarvis:ui', action, params ?? {}),
  signal: (action, params) => ipcRenderer.send('jarvis:signal', action, params ?? {}),
  on: (callback) => {
    const handler = (_event, name, data) => callback(name, data);
    ipcRenderer.on('jarvis:event', handler);
    return () => ipcRenderer.off('jarvis:event', handler);
  },
});
