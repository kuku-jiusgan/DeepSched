import { createApp } from 'vue'
import {
  Alert, Badge, Button, Card, Checkbox, Col, Collapse, ConfigProvider, DatePicker,
  Descriptions, Divider, Drawer, Dropdown, Empty, Form, Input, InputNumber,
  Layout, Menu, Modal, Popconfirm, Popover, Radio, Row, Segmented, Select,
  Skeleton, Space, Spin, Switch, Table, Tabs, Tag, TimePicker, Tooltip,
} from 'ant-design-vue'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import 'ant-design-vue/dist/reset.css'
import App from './App.vue'
import router from './router'
import './index.css'
import { canOperateAction, canOperatePage, permissionState } from './services/permissions'

dayjs.locale('zh-cn')

const app = createApp(App)
const antComponents = [
  Alert, Badge, Button, Card, Checkbox, Col, Collapse, ConfigProvider, DatePicker,
  Descriptions, Divider, Drawer, Dropdown, Empty, Form, Input, InputNumber,
  Layout, Menu, Modal, Popconfirm, Popover, Radio, Row, Segmented, Select,
  Skeleton, Space, Spin, Switch, Table, Tabs, Tag, TimePicker, Tooltip,
]
antComponents.forEach(component => app.use(component))
app.use(router)
interface OperationBinding {
  page: string
  action: string
}

function isOperationBinding(value: unknown): value is OperationBinding {
  return typeof value === 'object' && value !== null
    && 'page' in value && typeof value.page === 'string'
    && 'action' in value && typeof value.action === 'string'
}

function operationAllowed(value: unknown) {
  if (isOperationBinding(value)) return canOperateAction(value.page, value.action)
  if (typeof value === 'string') return canOperateAction(router.currentRoute.value.path, value)
  return canOperatePage(router.currentRoute.value.path)
}

function applyOperationVisibility(element: HTMLElement, binding: { value: unknown; modifiers: Partial<Record<string, boolean>> }) {
  const allowed = operationAllowed(binding.value)
  const showReadonly = binding.modifiers.readonly
    && permissionState.roles.includes('项目管理员')
    && !allowed
  element.style.display = allowed || showReadonly ? '' : 'none'
  if (showReadonly) {
    element.dataset.operationReadonly = 'true'
    element.setAttribute('aria-disabled', 'true')
    if ('disabled' in element) (element as HTMLButtonElement).disabled = true
  } else if (element.dataset.operationReadonly) {
    delete element.dataset.operationReadonly
    element.removeAttribute('aria-disabled')
    if ('disabled' in element) (element as HTMLButtonElement).disabled = false
  }
}

app.directive('operation', {
  mounted(element, binding) {
    applyOperationVisibility(element, binding)
  },
  updated(element, binding) {
    applyOperationVisibility(element, binding)
  },
})
app.mount('#app')
