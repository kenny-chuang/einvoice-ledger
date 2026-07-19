const documentDirectory = new URL('.', document.baseURI)
const appRoot = documentDirectory.pathname.endsWith('/app/')
  ? new URL('../', documentDirectory)
  : documentDirectory

export function appUrl(path: string): string {
  return new URL(path.replace(/^\//, ''), appRoot).toString()
}

export function apiUrl(path: string): string {
  return appUrl(`api/${path.replace(/^\/?api\//, '')}`)
}
