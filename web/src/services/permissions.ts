import { reactive } from 'vue'
import http from './http'

export interface PagePermission {
  page_key: string
  page_name: string
  group_name: string
  can_view: boolean
  can_operate: boolean
  actions: PageActionPermission[]
}

export interface PageActionPermission {
  action_key: string
  action_name: string
  allowed: boolean
}

interface PermissionState {
  role: string
  roles: string[]
  permissions: PagePermission[]
  isLoaded: boolean
}

export const permissionState = reactive<PermissionState>({ role: '', roles: [], permissions: [], isLoaded: false })
let loadingPromise: Promise<void> | null = null
localStorage.removeItem('permissionCache')

export async function loadMyPermissions(force = false) {
  if (permissionState.isLoaded && !force) return
  if (loadingPromise) return loadingPromise
  loadingPromise = http.get<{ role: string; roles: string[]; permissions: PagePermission[] }>('/role-permissions/me')
    .then(response => {
      permissionState.role = response.data.role
      permissionState.roles = response.data.roles
      permissionState.permissions = response.data.permissions
      permissionState.isLoaded = true
    })
    .finally(() => { loadingPromise = null })
  return loadingPromise
}

export function canViewPage(path: string) {
  if (permissionState.roles.includes('系统管理员')) return true
  return permissionState.permissions.find(item => item.page_key === normalizedPath(path))?.can_view ?? false
}

export function canOperatePage(path: string) {
  if (permissionState.roles.includes('系统管理员')) return true
  return permissionState.permissions.find(item => item.page_key === normalizedPath(path))?.actions.some(action => action.allowed) ?? false
}

export function canOperateAction(path: string, actionKey: string) {
  if (permissionState.roles.includes('系统管理员')) return true
  return permissionState.permissions
    .find(item => item.page_key === normalizedPath(path))
    ?.actions.find(action => action.action_key === actionKey)?.allowed ?? false
}

export function firstViewablePage() {
  return permissionState.permissions.find(item => item.can_view)?.page_key || '/operations/cockpit'
}

export function clearPermissions() {
  permissionState.role = ''
  permissionState.roles = []
  permissionState.permissions = []
  permissionState.isLoaded = false
  localStorage.removeItem('permissionCache')
}

function normalizedPath(path: string) {
  if (path === '/operations/project-tasks') return '/operations/reports'
  return path
}
