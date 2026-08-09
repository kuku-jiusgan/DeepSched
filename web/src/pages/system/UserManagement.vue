<template>
  <div>
    <div class="page-header">
      <h2>用户管理</h2>
      <p>具备操作权限的角色可维护普通账号；系统管理员账号仅能由系统管理员维护。</p>
    </div>
    <div class="action-bar">
      <a-button v-operation="'create'" type="primary" @click="openCreate"><PlusOutlined /> 添加用户</a-button>
      <span style="margin-left: auto; font-size: 12px; color: #94a3b8; align-self: center">共 {{ users.length }} 个用户</span>
    </div>
    <a-spin v-if="loading" size="large" style="display: block; margin: 80px auto" />
    <a-table v-else :dataSource="users" :columns="columns" rowKey="id" size="middle" :pagination="{ pageSize: 20, showSizeChanger: true }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'role'">
          <a-tag v-for="role in userRoles(record)" :key="role" :color="roleColors[role] || '#94a3b8'">{{ role }}</a-tag>
        </template>
        <template v-else-if="column.key === 'is_active'">
          <a-tag :color="record.is_active ? '#16a34a' : '#dc2626'">{{ record.is_active ? '启用' : '停用' }}</a-tag>
        </template>
        <template v-else-if="column.key === 'created_at'">
          {{ record.created_at ? dayjs(record.created_at).format('YYYY-MM-DD HH:mm') : '-' }}
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space :size="4">
            <a-button v-operation="'edit'" type="link" size="small" @click="openEdit(record)"><EditOutlined /> 编辑</a-button>
            <a-button v-operation="'password'" type="link" size="small" @click="openChangePwd(record)"><KeyOutlined /> 密码</a-button>
            <span v-operation="'delete'">
              <a-popconfirm title="确定删除该用户？" @confirm="handleDelete(record.id)" okText="确定" cancelText="取消" :disabled="record.id === currentUserId">
                <a-button type="link" size="small" danger :disabled="record.id === currentUserId"><DeleteOutlined /> 删除</a-button>
              </a-popconfirm>
            </span>
          </a-space>
        </template>
      </template>
    </a-table>

    <!-- 添加/编辑用户 -->
    <a-modal
      :title="editingId ? '编辑用户' : '添加用户'"
      v-model:open="modalOpen"
      wrap-class-name="user-form-modal"
      centered
      width="520px"
      :okText="editingId ? '保存' : '添加'"
      destroyOnClose
      @ok="handleSubmit"
    >
      <a-form layout="vertical" class="user-form">
        <a-form-item label="用户名" required><a-input v-model:value="form.username" placeholder="登录账号" :disabled="!!editingId" /></a-form-item>
        <a-form-item label="显示名称" required><a-input v-model:value="form.display_name" placeholder="如：张三" /></a-form-item>
        <a-form-item v-if="!editingId" label="登录密码" required :help="passwordHelp"><a-input-password v-model:value="form.password" placeholder="至少8位，包含字母和数字" /></a-form-item>
        <a-form-item label="角色" required><a-select v-model:value="form.roles" mode="multiple" :options="roleOptions" placeholder="可选择多个角色" /></a-form-item>
        <a-form-item label="邮箱"><a-input v-model:value="form.email" placeholder="如：zhangsan@example.com" /></a-form-item>
        <a-form-item label="手机号"><a-input v-model:value="form.phone" placeholder="如：13800138000" /></a-form-item>
          <a-form-item label="企业微信"><a-input v-model:value="form.wecom_id" placeholder="企业微信号" /></a-form-item>
        <a-form-item label="状态"><a-switch v-model:checked="form.is_active" checked-children="启用" un-checked-children="停用" :disabled="editingId === currentUserId" /></a-form-item>
      </a-form>
    </a-modal>

    <!-- 修改密码 -->
    <a-modal title="修改密码" v-model:open="pwdOpen" @ok="handleChangePwd" width="400" okText="确认修改" destroyOnClose>
      <a-form layout="vertical" style="margin-top: 16px">
        <a-form-item label="用户">{{ pwdTarget?.display_name }}（{{ pwdTarget?.username }}）</a-form-item>
        <a-form-item label="新密码" required>
          <a-input-password v-model:value="newPassword" placeholder="至少8位，包含字母和数字" autocomplete="new-password" />
        </a-form-item>
        <a-form-item label="确认新密码" required>
          <a-input-password v-model:value="confirmPassword" placeholder="再次输入新密码" autocomplete="new-password" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, reactive, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { PlusOutlined, EditOutlined, DeleteOutlined, KeyOutlined } from '@ant-design/icons-vue'
import { getUsers, createUser, updateUser, resetUserPassword, deleteUser, type User, type UserPayload } from '@/services/api'
import dayjs from 'dayjs'

const users = ref<User[]>([])
const loading = ref(true)
const modalOpen = ref(false)
const editingId = ref<number | null>(null)
const form = reactive({ username: '', display_name: '', password: '', roles: ['技术员'] as string[], email: '', phone: '', wecom_id: '', is_active: true })

const pwdOpen = ref(false)
const pwdTarget = ref<User | null>(null)
const newPassword = ref('')
const confirmPassword = ref('')

const roleOptions = [
  { label: '系统管理员', value: '系统管理员' },
  { label: '项目管理员', value: '项目管理员' },
  { label: '分析所所长', value: '分析所所长' },
  { label: '技术组长', value: '技术组长' },
  { label: '技术员', value: '技术员' },
]
const roleColors: Record<string, string> = {
  '系统管理员': '#7c3aed',
  '项目管理员': '#2563eb',
  '分析所所长': '#0891b2',
  '技术组长': '#0f766e',
  '技术员': '#15803d',
}

function userRoles(user: User) { return user.roles?.length ? user.roles : [user.role] }

const columns = [
  { title: '用户名', dataIndex: 'username', key: 'username', width: 120 },
  { title: '显示名称', dataIndex: 'display_name', key: 'display_name', width: 120 },
  { title: '角色', dataIndex: 'role', key: 'role', width: 110 },
  { title: '邮箱', dataIndex: 'email', key: 'email', width: 180, ellipsis: true },
  { title: '手机号', dataIndex: 'phone', key: 'phone', width: 130 },
  { title: '企业微信', dataIndex: 'wecom_id', key: 'wecom_id', width: 130, ellipsis: true },
  { title: '状态', dataIndex: 'is_active', key: 'is_active', width: 70 },
  { title: '创建时间', key: 'created_at', width: 140 },
  { title: '操作', key: 'actions', width: 200 },
]

async function fetchData() {
  loading.value = true
  try { users.value = await getUsers() } catch { message.error('加载用户列表失败') } finally { loading.value = false }
}

function openCreate() {
  editingId.value = null
  Object.assign(form, { username: '', display_name: '', password: '', roles: ['技术员'], email: '', phone: '', wecom_id: '', is_active: true })
  modalOpen.value = true
}

function openEdit(r: User) {
  editingId.value = r.id
  Object.assign(form, {
    username: r.username,
    display_name: r.display_name,
    password: '',
    roles: userRoles(r),
    email: r.email || '',
    phone: r.phone || '',
    wecom_id: r.wecom_id || '',
    is_active: r.is_active,
  })
  modalOpen.value = true
}

const currentUser = getCurrentUser()
const currentUserId = currentUser?.id ?? null
const passwordHelp = computed(() => form.password && !isStrongPassword(form.password) ? '密码至少8位，且必须包含字母和数字' : undefined)

async function handleSubmit() {
  if (!form.username || !form.display_name) { message.error('请填写用户名和显示名称'); return }
  if (!form.roles.length) { message.error('请至少选择一个角色'); return }
  if (!editingId.value && !form.password) { message.error('请设置登录密码'); return }
  if (form.password && !isStrongPassword(form.password)) { message.error('密码至少8位，且必须包含字母和数字'); return }
  try {
    const data: UserPayload = {
      username: form.username,
      display_name: form.display_name,
      role: form.roles[0],
      roles: form.roles,
      email: form.email,
      phone: form.phone,
      wecom_id: form.wecom_id,
      is_active: form.is_active,
    }
    if (form.password) data.password = form.password
    editingId.value ? await updateUser(editingId.value, data) : await createUser(data)
    message.success(editingId.value ? '更新成功' : '添加成功')
    modalOpen.value = false
    await fetchData()
  } catch (error: unknown) {
    message.error(getErrorDetail(error, '操作失败'))
  }
}

function openChangePwd(r: User) {
  pwdTarget.value = r
  newPassword.value = ''
  confirmPassword.value = ''
  pwdOpen.value = true
}

async function handleChangePwd() {
  if (!newPassword.value) { message.error('请输入新密码'); return }
  if (newPassword.value !== confirmPassword.value) { message.error('两次输入的密码不一致'); return }
  if (!isStrongPassword(newPassword.value)) { message.error('密码至少8位，且必须包含字母和数字'); return }
  try {
    await resetUserPassword(pwdTarget.value!.id, newPassword.value)
    message.success('密码修改成功')
    pwdOpen.value = false
  } catch (error: unknown) {
    message.error(getErrorDetail(error, '修改失败'))
  }
}

async function handleDelete(id: number) {
  if (id === currentUserId) { message.error('不能删除当前登录账号'); return }
  try { await deleteUser(id); message.success('已删除'); await fetchData() } catch (error: unknown) { message.error(getErrorDetail(error, '删除失败')) }
}

function isStrongPassword(value: string) {
  return value.length >= 8 && /[A-Za-z]/.test(value) && /\d/.test(value)
}

function getCurrentUser(): { id?: number; role?: string } | null {
  const raw = localStorage.getItem('user')
  if (!raw) return null
  try {
    return JSON.parse(raw) as { id?: number; role?: string }
  } catch {
    return null
  }
}

function getErrorDetail(error: unknown, fallback: string) {
  const response = (error as { response?: { data?: { detail?: string } } }).response
  return response?.data?.detail || fallback
}

onMounted(fetchData)
</script>

<style scoped>
.user-form {
  margin-top: 8px;
}

.user-form :deep(.ant-form-item) {
  margin-bottom: 14px;
}

:global(.user-form-modal .ant-modal) {
  max-width: calc(100vw - 32px);
  padding-bottom: 0;
}

:global(.user-form-modal .ant-modal-body) {
  max-height: calc(100vh - 190px);
  overflow-y: auto;
  padding-right: 8px;
}

@media (max-height: 720px) {
  :global(.user-form-modal .ant-modal-body) {
    max-height: calc(100vh - 150px);
  }
}
</style>
