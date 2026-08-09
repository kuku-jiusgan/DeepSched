<template>
  <div>
    <div class="page-header"><h2>标准任务类型</h2></div>

    <a-card title="标准任务类型" style="margin-top: 16px">
      <template #extra>
        <a-button v-operation="'create'" type="primary" size="small" @click="openCreate"><PlusOutlined /> 新增类型</a-button>
      </template>
      <a-table :dataSource="taskTypes" :columns="columns" rowKey="id" size="small"
        :pagination="false">
        <template #bodyCell="{ column, record }">
          <template v-if="column.key === 'resource_type'">
            <a-tag :color="record.resource_type === 'instrument' ? 'blue' : record.resource_type === 'human' ? 'green' : 'purple'">
              {{ resourceLabels[record.resource_type] || record.resource_type }}
            </a-tag>
          </template>
          <template v-else-if="column.key === 'is_active'">
            <a-switch v-operation="'toggle'" :checked="record.is_active" size="small" @change="(v: boolean) => handleToggle(record, v)" />
          </template>
          <template v-else-if="column.key === 'actions'">
            <a-space :size="0">
              <a-button v-operation="'edit'" type="link" size="small" @click="openEdit(record)"><EditOutlined /></a-button>
              <span v-operation="'delete'">
                <a-popconfirm title="确定删除此类型？" @confirm="handleDelete(record.id)">
                  <a-button type="link" size="small" danger><DeleteOutlined /></a-button>
                </a-popconfirm>
              </span>
            </a-space>
          </template>
        </template>
      </a-table>
    </a-card>

    <a-modal :title="editing ? '编辑任务类型' : '新增任务类型'" v-model:open="modalOpen" @ok="handleSubmit" width="480">
      <a-form layout="vertical">
        <a-form-item label="类型名称" required>
          <a-input v-model:value="form.name" placeholder="如：溶液配制" />
        </a-form-item>
        <a-form-item label="类型编码" required>
          <a-input v-model:value="form.code" placeholder="如：FFKF_001" :disabled="!!editing" />
        </a-form-item>
        <a-form-item label="资源依赖" required>
          <a-select v-model:value="form.resource_type" :options="resourceOptions" />
        </a-form-item>
        <a-form-item label="描述">
          <a-textarea v-model:value="form.description" placeholder="任务类型说明" :rows="2" />
        </a-form-item>
        <a-form-item label="前置任务">
          <a-select v-model:value="form.predecessor_type_ids" mode="multiple" placeholder="选择需先完成的任务类型" :options="predecessorOptions" style="width: 100%" />
        </a-form-item>
        <a-form-item label="排序">
          <a-input-number v-model:value="form.sort_order" :min="0" style="width: 100px" />
        </a-form-item>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, reactive, computed, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { isAxiosError } from 'axios'
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons-vue'
import { getTaskTypes, createTaskType, updateTaskType, toggleTaskType, deleteTaskType, type TaskTypeConfig } from '@/services/api'

const taskTypes = ref<TaskTypeConfig[]>([])
const modalOpen = ref(false)
const editing = ref<TaskTypeConfig | null>(null)
const form = reactive({ name: '', code: '', resource_type: 'both', description: '', sort_order: 0, predecessor_type_ids: [] as number[] })

const resourceLabels: Record<string, string> = { instrument: '仪器依赖', human: '人工依赖', both: '仪器+人工' }
const resourceOptions = [
  { label: '仪器依赖', value: 'instrument' },
  { label: '人工依赖', value: 'human' },
  { label: '仪器+人工', value: 'both' },
]

const columns = [
  { title: '名称', dataIndex: 'name', key: 'name' },
  { title: '编码', dataIndex: 'code', key: 'code' },
  { title: '资源依赖', key: 'resource_type', width: 120 },
  { title: '描述', dataIndex: 'description', key: 'desc', ellipsis: true },
  { title: '启用', key: 'is_active', width: 70 },
  { title: '排序', dataIndex: 'sort_order', key: 'sort', width: 60 },
  { title: '操作', key: 'actions', width: 100 },
]

const predecessorOptions = computed(() => 
  taskTypes.value
    .filter(t => t.id !== editing.value?.id)
    .map(t => ({ label: t.name, value: t.id }))
)

async function loadData() {
  try { taskTypes.value = await getTaskTypes() } catch { message.error('加载失败') }
}

function openCreate() {
  editing.value = null
  Object.assign(form, { name: '', code: '', resource_type: 'both', description: '', sort_order: 0, predecessor_type_ids: [] })
  modalOpen.value = true
}

function openEdit(record: TaskTypeConfig) {
  editing.value = record
  Object.assign(form, { name: record.name, code: record.code, resource_type: record.resource_type, description: record.description || '', sort_order: record.sort_order, predecessor_type_ids: record.predecessor_type_ids || [] })
  modalOpen.value = true
}

async function handleSubmit() {
  if (!form.name || !form.code) { message.error('请填写名称和编码'); return }
  try {
    if (editing.value) {
      await updateTaskType(editing.value.id, { ...form })
      message.success('更新成功')
    } else {
      await createTaskType({ ...form })
      message.success('创建成功')
    }
    modalOpen.value = false
    loadData()
  } catch (error: unknown) { message.error(errorDetail(error, '操作失败')) }
}

async function handleToggle(record: TaskTypeConfig, v: boolean) {
  try {
    await toggleTaskType(record.id, v)
    record.is_active = v
  } catch { message.error('操作失败') }
}

async function handleDelete(id: number) {
  try { await deleteTaskType(id); message.success('已删除'); loadData() } catch { message.error('删除失败') }
}

onMounted(loadData)

function errorDetail(error: unknown, fallback: string) {
  if (isAxiosError<{ detail?: string }>(error)) return error.response?.data?.detail || fallback
  return fallback
}
</script>
