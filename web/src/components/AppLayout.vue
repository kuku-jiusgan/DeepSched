<template>
  <a-layout style="min-height: 100vh; background: #f7f8fa">
    <a-layout-sider
      v-if="!isMobile"
      width="220"
      :collapsed-width="64"
      :collapsed="isSiderCollapsed"
      class="app-sider"
    >
      <div class="brand-panel" :class="{ 'brand-panel-collapsed': isSiderCollapsed }" aria-label="山东大学淄博生物医药研究院 资源智能调度协同平台">
        <div class="brand-panel-head">
          <div class="brand-mark" aria-hidden="true" />
          <a-tooltip
            :title="isSiderCollapsed ? '展开菜单' : '收回菜单'"
            placement="right"
            :open="isSiderTooltipOpen"
            :trigger="[]"
          >
            <a-button
              type="text"
              class="sider-toggle"
              :aria-label="isSiderCollapsed ? '展开菜单' : '收回菜单'"
              @mouseenter="showSiderTooltip"
              @mouseleave="hideSiderTooltip"
              @focus="showSiderTooltip"
              @blur="hideSiderTooltip"
              @click="toggleSider"
            >
              <template #icon>
                <MenuUnfoldOutlined v-if="isSiderCollapsed" />
                <MenuFoldOutlined v-else />
              </template>
            </a-button>
          </a-tooltip>
        </div>
        <div v-if="!isSiderCollapsed" class="brand-copy">
          <div class="brand-institute">山东大学淄博生物医药研究院</div>
          <div class="brand-platform">资源智能调度协同平台</div>
        </div>
      </div>
      <a-menu
        theme="dark"
        mode="inline"
        :inline-collapsed="isSiderCollapsed"
        :selected-keys="[route.path]"
        :default-open-keys="openKeys"
        :items="menuItems"
        class="app-menu"
        @click="navigate"
      />

      <div class="sider-footer" :class="{ 'sider-footer-collapsed': isSiderCollapsed }">
        <a-button type="text" block class="logout-button" aria-label="退出登录" @click="handleLogout">
          <template #icon><LogoutOutlined /></template>
          <span v-if="!isSiderCollapsed">退出登录</span>
        </a-button>
      </div>
    </a-layout-sider>
    <a-drawer
      v-else
      v-model:open="mobileMenuOpen"
      placement="left"
      :width="280"
      class="mobile-navigation-drawer"
      title="资源智能调度协同平台"
    >
      <a-spin v-if="mobileMenuLoading" class="mobile-menu-loading" />
      <nav v-else-if="mobileVisibleMenuItems.length" class="mobile-nav-list" aria-label="主菜单">
        <template v-for="item in mobileVisibleMenuItems" :key="item.key">
          <div v-if="item.children?.length" class="mobile-nav-group">{{ item.mobileLabel ?? item.label }}</div>
          <button
            v-for="entry in (item.children?.length ? item.children : [item])"
            :key="entry.key"
            type="button"
            class="mobile-nav-entry"
            :class="{ 'mobile-nav-entry-selected': route.path === entry.key }"
            @click="navigate({ key: entry.key })"
          >
            <component :is="entry.icon" />
            <span>{{ entry.label }}</span>
          </button>
        </template>
      </nav>
      <a-empty v-else description="暂无可访问菜单" :image="Empty.PRESENTED_IMAGE_SIMPLE" class="mobile-menu-empty" />
      <template #footer>
        <a-button type="text" block class="logout-button" @click="handleLogout">
          <template #icon><LogoutOutlined /></template>退出登录
        </a-button>
      </template>
    </a-drawer>
    <a-layout style="background: #f7f8fa">
      <a-layout-content
        class="app-content"
        :class="{ 'app-content-cockpit': route.path === '/operations/cockpit' }"
      >
        <div v-if="route.path !== '/operations/cockpit' || isMobile" class="page-top-actions">
          <a-button v-if="isMobile" class="mobile-menu-button" aria-label="打开菜单" :loading="mobileMenuLoading" @click="openMobileMenu">
            <template #icon><MenuOutlined /></template>
          </a-button>
          <a-dropdown trigger="click">
            <a-tooltip :title="currentUserLabel">
              <a-tag color="blue" class="current-user" :aria-label="`${currentUserLabel} 的账户菜单`">
                <span class="current-user-initial">{{ currentUserInitial }}</span>
                <span>{{ currentUserLabel }}</span>
              </a-tag>
            </a-tooltip>
            <template #overlay>
              <a-menu>
                <a-menu-item key="password" @click="openPasswordModal">修改密码</a-menu-item>
              </a-menu>
            </template>
          </a-dropdown>
        <a-popover v-model:open="notificationOpen" trigger="click" placement="bottomRight" overlayClassName="notification-popover">
          <template #content>
            <div class="notification-panel">
              <div class="notification-panel-head">
                <span>站内通知</span>
                <a-button
                  type="link"
                  size="small"
                  :loading="markingAllNotificationsRead"
                  :disabled="notifications.length === 0"
                  @click="markAllNotificationsRead"
                >
                  全部已读
                </a-button>
              </div>
              <a-empty v-if="notifications.length === 0" :image="Empty.PRESENTED_IMAGE_SIMPLE" description="暂无未读通知" />
              <div v-else class="notification-list">
                <div
                  v-for="item in notifications"
                  :key="item.id"
                  class="notification-item"
                  @click="readNotification(item)"
                >
                  <div class="notification-item-head">
                    <a-tag :color="notificationTypeColor(item.n_type)">{{ notificationTypeLabel(item.n_type) }}</a-tag>
                    <span>{{ formatTime(item.created_at) }}</span>
                  </div>
                  <div class="notification-title">{{ item.title || '系统通知' }}</div>
                  <div class="notification-content">{{ item.content || '-' }}</div>
                  <div v-if="item.is_confirmed === false" class="notification-actions">
                    <a-button size="small" type="primary" @click.stop="confirmItem(item)">确认</a-button>
                  </div>
                </div>
              </div>
            </div>
          </template>
          <a-badge :count="unreadCount" :overflow-count="99" size="small">
            <a-button class="notification-button" shape="circle">
              <template #icon><BellOutlined /></template>
            </a-button>
          </a-badge>
        </a-popover>
        </div>
        <router-view />
      </a-layout-content>
    </a-layout>
    <a-modal
      v-model:open="passwordModalOpen"
      title="修改密码"
      ok-text="确认修改"
      cancel-text="取消"
      :confirm-loading="passwordSubmitting"
      @ok="handleChangePassword"
      @cancel="closePasswordModal"
    >
      <a-form layout="vertical">
        <a-form-item label="原密码" required>
          <a-input-password v-model:value="passwordForm.oldPassword" autocomplete="current-password" />
        </a-form-item>
        <a-form-item label="新密码" required :help="newPasswordHelp">
          <a-input-password v-model:value="passwordForm.newPassword" placeholder="至少8位，包含字母和数字" autocomplete="new-password" />
        </a-form-item>
        <a-form-item label="确认新密码" required>
          <a-input-password v-model:value="passwordForm.confirmPassword" autocomplete="new-password" />
        </a-form-item>
      </a-form>
    </a-modal>
  </a-layout>
</template>

<script setup lang="ts">
import { computed, h, onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { isMobileViewport, useMobileViewport } from '@/composables/useMobileViewport'
import { useRouter, useRoute } from 'vue-router'
import { Empty, message } from 'ant-design-vue'
import dayjs from 'dayjs'
import { canViewPage, clearPermissions, loadMyPermissions, permissionState } from '@/services/permissions'
import {
  FundOutlined, AppstoreOutlined, CheckSquareOutlined,
  ProjectOutlined, ScheduleOutlined, SettingOutlined,
  FileTextOutlined, DesktopOutlined,
  BarChartOutlined, UserOutlined, MessageOutlined,
  WarningOutlined, DatabaseOutlined, PartitionOutlined,
  ApartmentOutlined, TableOutlined, ToolOutlined,
  ThunderboltOutlined, SwapOutlined, DollarOutlined,
  BellOutlined, TeamOutlined, CalendarOutlined, LogoutOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, MenuOutlined, HomeOutlined,
  SafetyCertificateOutlined,
} from '@ant-design/icons-vue'
import {
  changeMyPassword,
  confirmNotification,
  getNotifications,
  keepSessionAlive,
  logout,
  markAllNotificationsRead as markAllNotificationsReadRequest,
  markNotificationRead,
  type NotificationRecord,
} from '@/services/api'

const router = useRouter()
const route = useRoute()
const mobileSiderMedia = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
  ? window.matchMedia('(max-width: 768px)')
  : null
const { isMobile } = useMobileViewport()
const mobileMenuOpen = ref(false)
const mobileMenuLoading = ref(false)
const mobileMenuKey = computed(() => menuItems.value.map(item => item.key).join('|'))
const isSiderCollapsed = ref(
  isMobileViewport() || localStorage.getItem('siderCollapsed') === 'true',
)
const isSiderTooltipOpen = ref(false)
const canShowSiderTooltip = ref(true)
const notificationOpen = ref(false)
const notifications = ref<NotificationRecord[]>([])
const markingAllNotificationsRead = ref(false)
const passwordModalOpen = ref(false)
const passwordSubmitting = ref(false)
const passwordForm = reactive({
  oldPassword: '',
  newPassword: '',
  confirmPassword: '',
})
let notificationTimer: number | undefined
let initialNotificationTimer: number | undefined
let notificationFailureCount = 0
let hasNotifiedNotificationFailure = false
let sessionKeepAliveTimer: number | undefined
let idleLogoutTimer: number | undefined
let lastActivityWriteAt = 0
const IDLE_TIMEOUT_MS = 3 * 60 * 60 * 1000
const ACTIVITY_THROTTLE_MS = 1000
const SESSION_KEEP_ALIVE_MS = 5 * 60 * 1000
const ACTIVITY_EVENTS = ['click', 'keydown', 'mousemove', 'scroll', 'touchstart'] as const

const iconMap: Record<string, any> = {
  FundOutlined, AppstoreOutlined, CheckSquareOutlined,
  ProjectOutlined, ScheduleOutlined, SettingOutlined,
  FileTextOutlined, DesktopOutlined,
  BarChartOutlined, UserOutlined, MessageOutlined,
  WarningOutlined, DatabaseOutlined, PartitionOutlined,
  ApartmentOutlined, TableOutlined, ToolOutlined,
  ThunderboltOutlined, SwapOutlined, DollarOutlined,
  BellOutlined, TeamOutlined, CalendarOutlined, LogoutOutlined,
  HomeOutlined,
  SafetyCertificateOutlined,
}

function icon(name: string) {
  return () => h(iconMap[name])
}

const baseMenuItems = [
  { key: '/operations/cockpit', icon: icon('HomeOutlined'), label: '首页', mobile: false },
  { key: '/operations', icon: icon('FundOutlined'), label: '运营数据中台', hidden: true, children: [
    { key: '/operations/lab-status', icon: icon('DesktopOutlined'), label: '实验室状态大屏' },
  ]},
  { key: '/kanban', icon: icon('AppstoreOutlined'), label: '资源看板', mobile: false, children: [
    { key: '/kanban/instrument-gantt', icon: icon('BarChartOutlined'), label: '仪器甘特图' },
  ]},
  { key: '/tasks', icon: icon('CheckSquareOutlined'), label: '任务管理', mobile: true, mobileLabel: '工作', children: [
    { key: '/tasks/workspace', icon: icon('UserOutlined'), label: '个人工作台', mobile: true },
    { key: '/tasks/agenda', icon: icon('CalendarOutlined'), label: '我的安排', mobile: true },
    { key: '/tasks/faults', icon: icon('ToolOutlined'), label: '故障提报', mobile: true },
  ]},
  { key: '/projects', icon: icon('ProjectOutlined'), label: '项目管理', mobile: true, mobileLabel: '管理查看', children: [
    { key: '/projects/detection-tasks', icon: icon('CheckSquareOutlined'), label: '检测任务管理', mobile: true },
    { key: '/projects/ledger', icon: icon('DatabaseOutlined'), label: '项目台账管理', mobile: true },
    { key: '/projects/plan-breakdown', icon: icon('PartitionOutlined'), label: '项目计划拆解' },
    { key: '/projects/process-dag', icon: icon('ApartmentOutlined'), label: '标准工序依赖配置' },
    { key: '/projects/resource-ledger', icon: icon('TableOutlined'), label: '仪器基础信息' },
  ]},
  { key: '/reports', icon: icon('BarChartOutlined'), label: '报表中心', mobile: true, mobileLabel: '管理查看', children: [
    { key: '/operations/reports', icon: icon('FileTextOutlined'), label: '项目工时统计报表', mobile: true },
  ]},
  { key: '/schedule', icon: icon('ScheduleOutlined'), label: '排程管理', children: [
    { key: '/schedule/rules', icon: icon('ToolOutlined'), label: '排程规则配置' },
    { key: '/schedule/engine', icon: icon('ThunderboltOutlined'), label: '自动排程引擎', adminOnly: true },
    { key: '/schedule/insert-order', icon: icon('DollarOutlined'), label: '插单代价计算' },
  ]},
  { key: '/system', icon: icon('SettingOutlined'), label: '系统管理', children: [
    { key: '/system/alerts', icon: icon('BellOutlined'), label: '智能预警推送' },
    { key: '/system/audit-logs', icon: icon('FileTextOutlined'), label: '操作日志' },
    { key: '/system/users', icon: icon('TeamOutlined'), label: '用户管理' },
    { key: '/system/roles', icon: icon('SafetyCertificateOutlined'), label: '角色管理' },
    { key: '/system/basic', icon: icon('SettingOutlined'), label: '标准任务类型' },
    { key: '/system/calendar', icon: icon('CalendarOutlined'), label: '工作日历管理' },
  ]},
]

const menuItems = computed(() => {
  permissionState.permissions
  return filterMenuItems(baseMenuItems)
})

const mobileVisibleMenuItems = computed(() => (
  baseMenuItems
    .filter(item => item.mobile)
    .map(item => ({ ...item, children: item.children?.filter(child => (child as { mobile?: boolean }).mobile) }))
    .filter(item => item.children?.length || !item.children)
))

function filterMenuItems(items: typeof baseMenuItems) {
  return items
    .filter(item => !item.hidden)
    .map(item => ({
      ...item,
      children: item.children?.filter(child => canViewPage(child.key)),
    }))
    .filter(item => item.children ? item.children.length > 0 : canViewPage(item.key))
}

const openKeys = computed(() => {
  for (const item of menuItems.value) {
    if (item.children?.some((c: any) => c.key === route.path)) {
      return [item.key]
    }
  }
  return []
})

const unreadCount = computed(() => notifications.value.filter(item => !item.is_read).length)
const currentUserLabel = computed(() => {
  const user = getStoredUser()
  return user?.display_name || user?.username || '未登录'
})
const currentUserInitial = computed(() => {
  const label = currentUserLabel.value.trim()
  if (!label) return '我'
  const chineseName = label.replace(/\s+/g, '')
  return /^[\u4e00-\u9fff]/.test(chineseName) ? chineseName.slice(0, 1) : label.slice(0, 1).toUpperCase()
})
const newPasswordHelp = computed(() => (
  passwordForm.newPassword && !isStrongPassword(passwordForm.newPassword)
    ? '密码至少8位，且必须包含字母和数字'
    : undefined
))

function clearSession() {
  if (notificationTimer) window.clearInterval(notificationTimer)
  if (sessionKeepAliveTimer) window.clearInterval(sessionKeepAliveTimer)
  if (idleLogoutTimer) window.clearTimeout(idleLogoutTimer)
  localStorage.removeItem('token')
  localStorage.removeItem('user')
  localStorage.removeItem('lastActivityAt')
  clearPermissions()
}

async function handleLogout() {
  try {
    await logout()
  } catch {
    // Session may already be expired; local cleanup still needs to run.
  }
  clearSession()
  router.replace('/login')
}

function openPasswordModal() {
  resetPasswordForm()
  passwordModalOpen.value = true
}

function closePasswordModal() {
  if (passwordSubmitting.value) return
  passwordModalOpen.value = false
  resetPasswordForm()
}

function resetPasswordForm() {
  passwordForm.oldPassword = ''
  passwordForm.newPassword = ''
  passwordForm.confirmPassword = ''
}

async function handleChangePassword() {
  if (!passwordForm.oldPassword) {
    message.error('请输入原密码')
    return
  }
  if (!passwordForm.newPassword) {
    message.error('请输入新密码')
    return
  }
  if (!isStrongPassword(passwordForm.newPassword)) {
    message.error('密码至少8位，且必须包含字母和数字')
    return
  }
  if (passwordForm.newPassword !== passwordForm.confirmPassword) {
    message.error('两次输入的新密码不一致')
    return
  }
  passwordSubmitting.value = true
  try {
    await changeMyPassword({
      old_password: passwordForm.oldPassword,
      new_password: passwordForm.newPassword,
    })
    message.success('密码修改成功，请重新登录')
    clearSession()
    router.replace('/login')
  } catch (error: unknown) {
    message.error(getErrorDetail(error, '密码修改失败'))
  } finally {
    passwordSubmitting.value = false
  }
}

function isStrongPassword(value: string) {
  return value.length >= 8 && /[A-Za-z]/.test(value) && /\d/.test(value)
}

function getErrorDetail(error: unknown, fallback: string) {
  const maybeError = error as { response?: { data?: { detail?: string } } }
  return maybeError.response?.data?.detail || fallback
}

function navigate({ key }: { key: string }) {
  router.push(key)
  mobileMenuOpen.value = false
}

function toggleSider() {
  isSiderTooltipOpen.value = false
  canShowSiderTooltip.value = false
  isSiderCollapsed.value = !isSiderCollapsed.value
  localStorage.setItem('siderCollapsed', String(isSiderCollapsed.value))
}

function syncSiderWithViewport(event: MediaQueryListEvent) {
  isSiderCollapsed.value = event.matches
    ? true
    : localStorage.getItem('siderCollapsed') === 'true'
}

function showSiderTooltip() {
  if (canShowSiderTooltip.value) isSiderTooltipOpen.value = true
}

function hideSiderTooltip() {
  isSiderTooltipOpen.value = false
  canShowSiderTooltip.value = true
}

async function fetchNotifications() {
  const user = getStoredUser()
  if (!user?.username) return
  try {
    notifications.value = await getNotifications({
      user_name: user.username,
      channel: 'site',
      unread_only: true,
    })
    notificationFailureCount = 0
    hasNotifiedNotificationFailure = false
  } catch {
    notificationFailureCount += 1
    if (notificationFailureCount >= 3 && !hasNotifiedNotificationFailure) {
      hasNotifiedNotificationFailure = true
      message.warning('站内通知暂时无法更新，系统将自动重试')
    }
  }
}

function markActivity() {
  const now = Date.now()
  if (now - lastActivityWriteAt < ACTIVITY_THROTTLE_MS) return
  lastActivityWriteAt = now
  localStorage.setItem('lastActivityAt', String(now))
  resetIdleTimer()
}

async function keepActiveSessionAlive() {
  const lastActivityAt = Number(localStorage.getItem('lastActivityAt') || 0)
  if (!lastActivityAt || Date.now() - lastActivityAt >= IDLE_TIMEOUT_MS) return
  try {
    await keepSessionAlive()
  } catch {
    // The response interceptor handles expired sessions.
  }
}

function resetIdleTimer() {
  if (idleLogoutTimer) window.clearTimeout(idleLogoutTimer)
  idleLogoutTimer = window.setTimeout(handleIdleTimeout, IDLE_TIMEOUT_MS)
}

async function handleIdleTimeout() {
  try {
    await logout()
  } catch {
    // Expired tokens are expected here; keep the UX deterministic.
  }
  clearSession()
  message.warning('长时间未操作，已自动退出')
  router.replace('/login?expired=1')
}

function checkIdleOnVisibilityChange() {
  if (document.visibilityState !== 'visible') return
  const lastActivityAt = Number(localStorage.getItem('lastActivityAt') || Date.now())
  if (Date.now() - lastActivityAt >= IDLE_TIMEOUT_MS) {
    handleIdleTimeout()
  } else {
    resetIdleTimer()
  }
}

async function readNotification(item: NotificationRecord) {
  if (item.is_confirmed === false) return
  await markNotificationRead(item.id)
  notifications.value = notifications.value.filter(n => n.id !== item.id)
}

async function markAllNotificationsRead() {
  if (notifications.value.length === 0 || markingAllNotificationsRead.value) return
  markingAllNotificationsRead.value = true
  try {
    const result = await markAllNotificationsReadRequest()
    notifications.value = []
    message.success(result.updated_count > 0 ? '全部通知已标记为已读' : '暂无未读通知')
  } catch {
    message.error('全部标记已读失败，请稍后重试')
  } finally {
    markingAllNotificationsRead.value = false
  }
}

async function confirmItem(item: NotificationRecord) {
  await confirmNotification(item.id)
  await markNotificationRead(item.id)
  notifications.value = notifications.value.filter(n => n.id !== item.id)
  message.success('已确认')
}

function getStoredUser(): { username: string; display_name?: string; role?: string } | null {
  const raw = localStorage.getItem('user')
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw) as { username?: unknown; display_name?: unknown; role?: unknown }
    if (typeof parsed.username !== 'string') return null
    return {
      username: parsed.username,
      display_name: typeof parsed.display_name === 'string' ? parsed.display_name : undefined,
      role: typeof parsed.role === 'string' ? parsed.role : undefined,
    }
  } catch {
    return null
  }
}

function formatTime(value: string) {
  return dayjs(value).format('MM-DD HH:mm')
}

function notificationTypeLabel(type: string) {
  const labels: Record<string, string> = {
    instrument_fault_reschedule: '故障重排',
    instrument_fault_schedule_conflict: '故障冲突',
    schedule_changed: '排程变更',
    task_start_delay: '开始提醒',
    task_end_delay: '结束延期',
    task_schedule_advanced: '排程提前',
    task_schedule_delayed: '排程推迟',
    hours_exceeded: '工时超标',
    approval_pending: '方案待提交',
    approval_due: '签批提醒',
    approval_schedule_result: '签批排程',
  }
  return labels[type] || '系统通知'
}

function notificationTypeColor(type: string) {
  const colors: Record<string, string> = {
    instrument_fault_schedule_conflict: 'red',
    instrument_fault_reschedule: 'orange',
    schedule_changed: 'blue',
    task_start_delay: 'gold',
    task_end_delay: 'volcano',
    task_schedule_advanced: 'green',
    task_schedule_delayed: 'orange',
    hours_exceeded: 'purple',
    approval_pending: 'default',
    approval_due: 'orange',
    approval_schedule_result: 'green',
  }
  return colors[type] || 'default'
}

onMounted(() => {
  void ensureLayoutPermissions()
  mobileSiderMedia?.addEventListener('change', syncSiderWithViewport)
  markActivity()
  ACTIVITY_EVENTS.forEach(eventName => window.addEventListener(eventName, markActivity, { passive: true }))
  document.addEventListener('visibilitychange', checkIdleOnVisibilityChange)
  initialNotificationTimer = window.setTimeout(fetchNotifications, 600)
  notificationTimer = window.setInterval(fetchNotifications, 15000)
  sessionKeepAliveTimer = window.setInterval(keepActiveSessionAlive, SESSION_KEEP_ALIVE_MS)
})

async function ensureLayoutPermissions() {
  if (!localStorage.getItem('token') || permissionState.isLoaded) return
  try {
    await loadMyPermissions()
  } catch {
    // The router handles session expiration; keep the layout mounted while it redirects.
  }
}

async function openMobileMenu() {
  if (mobileMenuLoading.value) return
  mobileMenuLoading.value = true
  try {
    if (localStorage.getItem('token')) await loadMyPermissions(true)
    mobileMenuOpen.value = true
  } catch (error: unknown) {
    message.error(getErrorDetail(error, '菜单加载失败，请稍后重试'))
  } finally {
    mobileMenuLoading.value = false
  }
}

onBeforeUnmount(() => {
  mobileSiderMedia?.removeEventListener('change', syncSiderWithViewport)
  if (notificationTimer) window.clearInterval(notificationTimer)
  if (initialNotificationTimer) window.clearTimeout(initialNotificationTimer)
  if (sessionKeepAliveTimer) window.clearInterval(sessionKeepAliveTimer)
  if (idleLogoutTimer) window.clearTimeout(idleLogoutTimer)
  ACTIVITY_EVENTS.forEach(eventName => window.removeEventListener(eventName, markActivity))
  document.removeEventListener('visibilitychange', checkIdleOnVisibilityChange)
})
</script>

<style scoped src="./AppLayout.css"></style>
