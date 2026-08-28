<template>
  <div>
    <div class="page-header"><h2>仪器基础信息</h2></div>
    <div class="action-bar">
      <a-button v-operation="'create'" type="primary" @click="openCreate"><PlusOutlined /> 添加仪器</a-button>
      <a-select placeholder="按分组筛选" allowClear style="width: 180px" v-model:value="groupFilter" :options="groupOptions" />
      <span style="margin-left: auto; font-size: 12px; color: #94a3b8; align-self: center">共 {{ filtered.length }} 台仪器</span>
    </div>
    <a-spin v-if="loading" size="large" style="display: block; margin: 80px auto" />
    <a-table v-else :dataSource="filtered" :columns="columns" rowKey="id" size="middle"
      :pagination="{ pageSize: 20, showSizeChanger: true }">
      <template #bodyCell="{ column, record }">
        <template v-if="column.key === 'code'">
          <span style="font-family: monospace; font-weight: 600; color: #2563eb; font-size: 12px">{{ record.code }}</span>
        </template>
        <template v-else-if="column.key === 'group'">
          <a-tag :color="record.instrument_group === 'GTI_Group' ? '#7c3aed' : record.instrument_group === 'Quality_Group' ? '#0891b2' : '#94a3b8'">{{ groupLabels[record.instrument_group] || record.instrument_group }}</a-tag>
        </template>
        <template v-else-if="column.key === 'spec'">{{ [record.brand, record.model].filter(Boolean).join(' / ') || '-' }}</template>
        <template v-else-if="column.key === 'location'">{{ record.location || '-' }}</template>
        <template v-else-if="column.key === 'availability'">
          <a-tag :color="availabilityColors[record.availability_status] || '#94a3b8'">
            {{ availabilityLabels[record.availability_status] || record.availability_status || '未设置' }}
          </a-tag>
        </template>
        <template v-else-if="column.key === 'status'">
          <a-tag :color="statusColors[record.status] || '#94a3b8'">{{ statusLabels[record.status] || record.status }}</a-tag>
        </template>
        <template v-else-if="column.key === 'working_hours'">
          <span style="font-variant-numeric: tabular-nums">{{ formatWorkingHours(record) }}</span>
        </template>
        <template v-else-if="column.key === 'caps'">
          <a-space :size="4" wrap>
            <a-tag v-for="(c, i) in (record.capabilities || []).slice(0, 3)" :key="i" style="font-size: 11px; margin: 0">{{ c.tag_name }}:{{ c.tag_value }}</a-tag>
            <a-tooltip v-if="(record.capabilities || []).length > 3" :title="formatCapabilities(record.capabilities || [])">
              <a-tag style="font-size: 11px; margin: 0">+{{ record.capabilities.length - 3 }}</a-tag>
            </a-tooltip>
            <span v-if="!record.capabilities || record.capabilities.length === 0" style="color: #cbd5e1">未配置</span>
          </a-space>
        </template>
        <template v-else-if="column.key === 'actions'">
          <a-space :size="4">
            <a-button v-operation="'edit'" type="link" size="small" @click="openEdit(record)"><EditOutlined /> 编辑</a-button>
            <span v-operation="'delete'">
              <a-popconfirm title="确定删除该仪器？" @confirm="handleDelete(record.id)" okText="确定" cancelText="取消">
                <a-button type="link" size="small" danger><DeleteOutlined /> 删除</a-button>
              </a-popconfirm>
            </span>
          </a-space>
        </template>
      </template>
    </a-table>

    <a-modal :title="editingId ? '编辑仪器' : '添加仪器'" v-model:open="modalOpen" @ok="handleSubmit" width="640" :confirmLoading="submitting"
      :okText="editingId ? '保存' : '添加'" destroyOnClose>
      <a-form layout="vertical" style="margin-top: 16px">
        <a-space style="width: 100%" :size="16">
          <a-form-item label="仪器唯一编码" required style="width: 200px"><a-input v-model:value="form.code" placeholder="如：LCMS_001" /></a-form-item>
          <a-form-item label="标准仪器名称" required style="flex: 1"><a-input v-model:value="form.name" placeholder="如：液相色谱-质谱联用仪" /></a-form-item>
        </a-space>
        <a-space style="width: 100%" :size="16">
          <a-form-item label="所属物理分组" required style="width: 200px"><a-select v-model:value="form.instrument_group" :options="groupOptions" /></a-form-item>
          <a-form-item label="品牌" style="width: 180px"><a-input v-model:value="form.brand" placeholder="如：Agilent" /></a-form-item>
          <a-form-item label="型号" style="width: 180px"><a-input v-model:value="form.model" placeholder="如：1290-6470" /></a-form-item>
        </a-space>
        <a-space style="width: 100%" :size="16">
          <a-form-item label="物理位置" style="width: 160px"><a-input v-model:value="form.location" placeholder="如：A201" /></a-form-item>
          <a-form-item label="可用状态" required style="width: 140px"><a-select v-model:value="form.availability_status" :options="availabilityOptions" /></a-form-item>
        </a-space>
        <a-space style="width: 100%" :size="16">
          <a-form-item label="切换基准耗时(h)" style="width: 160px"><a-input-number v-model:value="form.switchover_base_hours" :min="0" :max="24" :step="0.5" style="width: 100%" /></a-form-item>
          <a-form-item label="有效工作时段" required style="flex: 1">
            <a-space :size="8">
              <a-select v-model:value="form.effective_work_start" :options="workStartOptions" style="width: 100px" />
              <span style="color: #64748b">至</span>
              <a-select v-model:value="form.effective_work_end" :options="workEndOptions" style="width: 100px" />
            </a-space>
          </a-form-item>
        </a-space>
        <div style="margin-bottom: 8px; font-size: 13px; font-weight: 600; color: #334155">
          仪器能力标签集 <span style="font-weight: 400; font-size: 11px; color: #94a3b8; margin-left: 8px">（算法匹配依据）</span>
        </div>
        <a-space v-for="(cap, idx) in form.capabilities" :key="idx" style="display: flex; margin-bottom: 8px" align="baseline">
          <a-select v-model:value="cap.tag_name" placeholder="能力标签" style="width: 140px" :options="capTagOptions" />
          <a-select v-model:value="cap.tag_value" placeholder="标签值" style="width: 160px" :options="capValOpts[cap.tag_name] || []" />
          <a-button type="text" danger @click="form.capabilities.splice(idx, 1)"><DeleteOutlined /></a-button>
        </a-space>
        <a-button type="dashed" block @click="form.capabilities.push({ tag_name: '', tag_value: '' })"><PlusOutlined /> 添加能力标签</a-button>
      </a-form>
    </a-modal>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, reactive } from 'vue'
import { message } from 'ant-design-vue'
import { PlusOutlined, EditOutlined, DeleteOutlined } from '@ant-design/icons-vue'
import { getInstruments, createInstrument, updateInstrument, deleteInstrument, getScheduleRules, type InstrumentPayload } from '@/services/api'
import type { CapabilityReq, Instrument } from '@/types'

type AvailabilityStatus = 'available' | 'unavailable'

interface CapabilityForm {
  tag_name: string
  tag_value: string
}

interface InstrumentForm {
  code: string
  name: string
  instrument_group: string
  brand: string
  model: string
  location: string
  availability_status: AvailabilityStatus
  switchover_base_hours: number
  effective_work_start: string
  effective_work_end: string
  capabilities: CapabilityForm[]
}

const instruments = ref<Instrument[]>([])
const loading = ref(true)
const submitting = ref(false)
const modalOpen = ref(false)
const editingId = ref<number | null>(null)
const groupFilter = ref<string>()
const form = reactive<InstrumentForm>({
  code: '',
  name: '',
  instrument_group: 'GTI_Group',
  brand: '',
  model: '',
  location: '',
  availability_status: 'available',
  switchover_base_hours: 0.5,
  effective_work_start: '08:30',
  effective_work_end: '20:00',
  capabilities: [],
})

const groupOptions = [{ label: '基因毒组 (GTI)', value: 'GTI_Group' }, { label: '质量组 (Quality)', value: 'Quality_Group' }]
const availabilityOptions = [{ label: '可用', value: 'available' }, { label: '不可用', value: 'unavailable' }]
const timeOptions = Array.from({ length: 49 }, (_, index) => {
  const minutes = index * 30
  const value = `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
  return { label: value, value }
})
const workStartOptions = timeOptions.slice(0, -1)
const workEndOptions = timeOptions.slice(1)
const capTagOptions = [{ label: '离子源', value: '离子源' }, { label: '质量分析器', value: '质量分析器' }, { label: '方法类型', value: '方法类型' }, { label: '灵敏度等级', value: '灵敏度等级' }]
const capValOpts: Record<string, { label: string; value: string }[]> = {
  '离子源': [{ label: 'ESI', value: 'ESI' }, { label: 'APCI', value: 'APCI' }],
  '质量分析器': [{ label: 'QqQ', value: 'QqQ' }, { label: 'Q-TOF', value: 'Q-TOF' }],
  '方法类型': [{ label: '基因毒杂质', value: '基因毒杂质' }, { label: '有关物质', value: '有关物质' }, { label: '含量测定', value: '含量测定' }],
  '灵敏度等级': [{ label: '痕量', value: '痕量' }, { label: '常量', value: '常量' }],
}

const availabilityLabels: Record<string, string> = { available: '可用', unavailable: '不可用' }
const availabilityColors: Record<string, string> = { available: '#16a34a', unavailable: '#64748b' }
const statusLabels: Record<string, string> = { idle: '闲置', running: '运行中', maintenance: '维护中', fault: '故障停机', active: '活跃' }
const statusColors: Record<string, string> = { idle: '#16a34a', running: '#2563eb', maintenance: '#ea580c', fault: '#dc2626', active: '#16a34a' }
const groupLabels: Record<string, string> = { GTI_Group: '基因毒组', Quality_Group: '质量组' }

const filtered = computed(() => groupFilter.value ? instruments.value.filter(i => i.instrument_group === groupFilter.value) : instruments.value)
const columns = [
  { title: '仪器编码', dataIndex: 'code', key: 'code', width: 100 },
  { title: '仪器名称', dataIndex: 'name', key: 'name', width: 140, ellipsis: true },
  { title: '所属分组', dataIndex: 'instrument_group', key: 'group', width: 90 },
  { title: '品牌/型号', key: 'spec', width: 120, responsive: ['lg'] },
  { title: '位置', dataIndex: 'location', key: 'location', width: 60 },
  { title: '可用状态', dataIndex: 'availability_status', key: 'availability', width: 80 },
  { title: '运行状态', dataIndex: 'status', key: 'status', width: 80 },
  { title: '有效工作时段', key: 'working_hours', width: 115 },
  { title: '能力标签', dataIndex: 'capabilities', key: 'caps', width: 140, responsive: ['xl'] },
  { title: '操作', key: 'actions', width: 120 },
]

async function fetchData() { loading.value = true; try { instruments.value = await getInstruments({ include_unavailable: true }) } catch { message.error('加载失败') } finally { loading.value = false } }
async function resetForm() {
  const rules = await getScheduleRules()
  const params = rules.find(rule => rule.code === 'working_hours')?.params
  Object.assign(form, {
    code: '',
    name: '',
    instrument_group: 'GTI_Group',
    brand: '',
    model: '',
    location: '',
    availability_status: 'available',
      switchover_base_hours: 0.5,
    effective_work_start: normalizeClock(params?.day_start, '08:30'),
    effective_work_end: normalizeClock(params?.day_end, '20:00'),
    capabilities: [],
  })
}
async function openCreate() {
  editingId.value = null
  try { await resetForm(); modalOpen.value = true }
  catch { message.error('读取默认有效工作时段失败，暂不能添加仪器') }
}
function openEdit(r: Instrument) {
  editingId.value = r.id
  Object.assign(form, {
    code: r.code,
    name: r.name,
    instrument_group: r.instrument_group,
    brand: r.brand || '',
    model: r.model || '',
    location: r.location || '',
    availability_status: r.availability_status || 'available',
    switchover_base_hours: r.switchover_base_hours,
    effective_work_start: normalizeClock(r.effective_work_start, ''),
    effective_work_end: normalizeClock(r.effective_work_end, ''),
    capabilities: (r.capabilities || []).map(c => ({ tag_name: c.tag_name, tag_value: c.tag_value })),
  })
  modalOpen.value = true
}
function buildPayload(): InstrumentPayload {
  return {
    code: form.code,
    name: form.name,
    instrument_group: form.instrument_group,
    brand: form.brand || undefined,
    model: form.model || undefined,
    location: form.location || undefined,
    availability_status: form.availability_status,
    switchover_base_hours: form.switchover_base_hours,
    effective_work_start: form.effective_work_start,
    effective_work_end: form.effective_work_end,
    capabilities: form.capabilities,
  }
}
function formatCapabilities(capabilities: CapabilityReq[]) {
  return capabilities.map(c => c.tag_name + ':' + c.tag_value).join(', ')
}
async function handleSubmit() {
  if (!form.code || !form.name) { message.error('请填写编码和名称'); return }
  if (form.effective_work_start >= form.effective_work_end) { message.error('有效工作时段开始时间必须早于结束时间'); return }
  submitting.value = true
  try {
    const payload = buildPayload()
    editingId.value ? await updateInstrument(editingId.value, payload) : await createInstrument(payload)
    message.success(editingId.value ? '更新成功' : '添加成功')
    modalOpen.value = false
    await fetchData()
  } catch (error: unknown) {
    const response = (error as { response?: { status?: number; data?: { detail?: string } } }).response
    if (response?.status === 409) message.error(response.data?.detail || '编码重复')
    else message.error(response?.data?.detail || '保存失败')
  } finally { submitting.value = false }
}
function normalizeClock(value: unknown, fallback: string) {
  return typeof value === 'string' && /^\d{2}:\d{2}/.test(value) ? value.slice(0, 5) : fallback
}
function formatWorkingHours(instrument: Instrument) {
  return `${normalizeClock(instrument.effective_work_start, '-')} - ${normalizeClock(instrument.effective_work_end, '-')}`
}
async function handleDelete(id:number){try{await deleteInstrument(id);message.success('已删除');fetchData()}catch{message.error('删除失败')}}
onMounted(fetchData)
</script>


