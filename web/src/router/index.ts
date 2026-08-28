import { createRouter, createWebHistory } from 'vue-router'
import AppLayout from '@/components/AppLayout.vue'
import { canViewPage, clearPermissions, firstViewablePage, loadMyPermissions } from '@/services/permissions'
import { isMobileViewport } from '@/composables/useMobileViewport'

function defaultAuthenticatedPath() {
  return isMobileViewport() ? '/tasks/workspace' : '/operations/cockpit'
}

const routes = [
  {
    path: '/login',
    component: () => import('@/pages/Login.vue'),
    meta: { guest: true }
  },
  {
    path: '/',
    component: AppLayout,
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: defaultAuthenticatedPath },
      { path: 'dashboard', component: () => import('@/pages/Dashboard.vue') },
      { path: 'operations/cockpit', component: () => import('@/pages/operations/LabOperationsCockpit.vue') },
      { path: 'operations/lab-dashboard', redirect: '/operations/cockpit' },
      { path: 'operations/reports', component: () => import('@/pages/operations/DetailedReports.vue') },
      { path: 'operations/project-tasks', component: () => import('@/pages/operations/ProjectTaskDetail.vue') },
      { path: 'operations/lab-status', component: () => import('@/pages/operations/LabStatusScreen.vue') },
      { path: 'kanban/instrument-gantt', component: () => import('@/pages/InstrumentGantt.vue') },
      { path: 'kanban/project-gantt', component: () => import('@/pages/kanban/ProjectGantt.vue') },
      { path: 'kanban/project-progress', component: () => import('@/pages/kanban/ProjectProgress.vue') },
      { path: 'kanban/human-gantt', component: () => import('@/pages/kanban/HumanGantt.vue') },
      { path: 'tasks/workspace', component: () => import('@/pages/tasks/PersonalWorkspace.vue') },
      { path: 'tasks/agenda', component: () => import('@/pages/tasks/MyAgenda.vue') },
      { path: 'tasks/faults', component: () => import('@/pages/tasks/InstrumentFaults.vue') },
      { path: 'projects/ledger', component: () => import('@/pages/ProjectBoard.vue') },
      { path: 'projects/detection-tasks', component: () => import('@/pages/projects/DetectionTaskManagement.vue') },
      { path: 'projects/plan-breakdown', component: () => import('@/pages/projects/PlanBreakdown.vue') },
      { path: 'projects/process-dag', component: () => import('@/pages/ProjectDAG.vue') },
      { path: 'projects/resource-ledger', component: () => import('@/pages/projects/ResourceLedger.vue') },
      { path: 'schedule/rules', component: () => import('@/pages/schedule/ScheduleRules.vue') },
      { path: 'schedule/engine', component: () => import('@/pages/ScheduleManager.vue'), meta: { requiresAdmin: true } },
      { path: 'schedule/insert-order', component: () => import('@/pages/schedule/InsertOrder.vue') },
      { path: 'system/alerts', component: () => import('@/pages/system/AlertPush.vue') },
      { path: 'system/audit-logs', component: () => import('@/pages/system/AuditLogManagement.vue') },
      { path: 'system/external-sync', component: () => import('@/pages/system/ExternalSync.vue') },
      { path: 'system/users', component: () => import('@/pages/system/UserManagement.vue'), meta: { requiresAdmin: true } },
      { path: 'system/roles', component: () => import('@/pages/system/RoleManagement.vue') },
      { path: 'system/basic', component: () => import('@/pages/system/SystemBasic.vue') },
      { path: 'system/calendar', component: () => import('@/pages/system/WorkCalendar.vue') },
    ]
  }
]

const router = createRouter({ history: createWebHistory(), routes })
const IDLE_TIMEOUT_MS = 3 * 60 * 60 * 1000

function clearSession() {
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  localStorage.removeItem('lastActivityAt')
  clearPermissions()
}

router.beforeEach(async (to, _from, next) => {
  const token = localStorage.getItem('token')
  const lastActivityAt = Number(localStorage.getItem('lastActivityAt') || Date.now())
  const isIdleExpired = token && Date.now() - lastActivityAt >= IDLE_TIMEOUT_MS
  if (isIdleExpired) {
    clearSession()
    next('/login?expired=1')
    return
  }
  if (to.meta.requiresAuth && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    try {
      await loadMyPermissions(true)
      next(defaultAuthenticatedPath())
    } catch {
      clearSession()
      next('/login')
    }
  } else {
    if (token && isMobileViewport() && to.path === '/operations/cockpit') {
      next('/tasks/workspace')
      return
    }
    try {
      if (token) await loadMyPermissions(true)
      if (!to.meta.guest && token && !canViewPage(to.path)) {
        next(firstViewablePage())
      } else {
        next()
      }
    } catch {
      clearSession()
      next('/login')
    }
  }
})

export default router
