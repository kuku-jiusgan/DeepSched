<template>
  <div>
    <div class="page-header"><h2>精细化运营报表</h2></div>

    <a-spin v-if="loading" size="large" style="display: block; margin: 50px auto" />

    <template v-else>
      <div class="action-bar">
        <a-range-picker v-model:value="dateRange" :placeholder="['开始日期', '结束日期']" allowClear style="width: 240px" @change="onFilterChange" />
        <a-radio-group v-model:value="viewMode" @change="onFilterChange" style="margin-left: 12px">
          <a-radio-button value="project">按项目</a-radio-button>
          <a-radio-button value="user">按人员</a-radio-button>
        </a-radio-group>
        <span style="margin-left: auto; font-size: 12px; color: #94a3b8">
          {{ viewMode === 'project' ? projectStats.length + ' 个项目' : userStats.length + ' 人' }}，总工时 {{ totalHours.toFixed(1) }}h
        </span>
      </div>

      <!-- 按项目视图 -->
      <template v-if="viewMode === 'project'">
        <a-table :dataSource="projectStats" rowKey="projectId" size="small" :pagination="{ pageSize: 20, showSizeChanger: true }" :expandable="{ expandedRowRender }">
          <a-table-column title="项目编号" dataIndex="projectCode" key="code" width="130" />
          <a-table-column title="项目名称" dataIndex="projectName" key="name" ellipsis />
          <a-table-column title="客户" dataIndex="clientName" key="client" width="120" />
          <a-table-column title="负责人" dataIndex="manager" key="manager" width="90" />
          <a-table-column title="任务数" dataIndex="taskCount" key="count" width="80" align="center" />
          <a-table-column title="总工时(h)" dataIndex="totalHours" key="hours" width="110" align="center" sortable>
            <template #default="{ record }">{{ record.totalHours.toFixed(1) }}</template>
          </a-table-column>
          <a-table-column title="人工工时(h)" dataIndex="humanHours" key="human" width="110" align="center">
            <template #default="{ record }">{{ record.humanHours.toFixed(1) }}</template>
          </a-table-column>
          <a-table-column title="仪器工时(h)" dataIndex="instrumentHours" key="inst" width="110" align="center">
            <template #default="{ record }">{{ record.instrumentHours.toFixed(1) }}</template>
          </a-table-column>
          <a-table-column title="操作" key="actions" width="80" align="center">
          <template #default="{ record }">
            <a-button type="link" size="small" @click="openDetail(record)">详情</a-button>
          </template>
        </a-table-column>
        <a-table-column title="工时占比" key="pct" width="180">
            <template #default="{ record }">
              <div style="display: flex; align-items: center; gap: 8px">
                <div style="flex: 1; height: 8px; background: #f1f5f9; border-radius: 4px; overflow: hidden">
                  <div :style="{ width: pct(record.totalHours) + '%', height: '100%', background: '#3b82f6', borderRadius: '4px' }" />
                </div>
                <span style="font-size: 12px; color: #94a3b8; white-space: nowrap">{{ pct(record.totalHours).toFixed(1) }}%</span>
              </div>
            </template>
          </a-table-column>
        </a-table>
      </template>

      <!-- 按人员视图 -->
      <template v-else>
        <a-table :dataSource="userStats" rowKey="userId" size="small" :pagination="{ pageSize: 20, showSizeChanger: true }">
          <a-table-column title="负责人" dataIndex="userName" key="name" />
          <a-table-column title="任务数" dataIndex="taskCount" key="count" width="100" align="center" />
          <a-table-column title="总工时(h)" dataIndex="totalHours" key="hours" width="120" align="center" sortable>
            <template #default="{ record }">{{ record.totalHours.toFixed(1) }}</template>
          </a-table-column>
          <a-table-column title="溶液配制(h)" key="sol" width="110" align="center">
            <template #default>0.0</template>
          </a-table-column>
          <a-table-column title="样品前处理(h)" key="samp" width="110" align="center">
            <template #default>0.0</template>
          </a-table-column>
          <a-table-column title="序列运行(h)" key="run" width="110" align="center">
            <template #default>0.0</template>
          </a-table-column>
          <a-table-column title="出具报告(h)" key="rep" width="110" align="center">
            <template #default>0.0</template>
          </a-table-column>
          <a-table-column title="工时占比" key="pct" width="200">
            <template #default="{ record }">
              <div style="display: flex; align-items: center; gap: 8px">
                <div style="flex: 1; height: 8px; background: #f1f5f9; border-radius: 4px; overflow: hidden">
                  <div :style="{ width: pct(record.totalHours) + '%', height: '100%', background: '#3b82f6', borderRadius: '4px' }" />
                </div>
                <span style="font-size: 12px; color: #94a3b8; white-space: nowrap">{{ pct(record.totalHours).toFixed(1) }}%</span>
              </div>
            </template>
          </a-table-column>
        </a-table>
      </template>
    </template>

  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, h } from 'vue'
import { useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import { getProjects, getUserDirectory, getTaskTypes } from '@/services/api'
import dayjs from 'dayjs'

interface UserStat {
  userId: number
  userName: string
  taskCount: number
  totalHours: number
  typeHours: Record<string, number>
}

interface ProjectStat {
  projectId: number
  projectCode: string
  projectName: string
  clientName: string
  manager: string
  taskCount: number
  totalHours: number
  humanHours: number
  instrumentHours: number
  users: { userName: string; taskCount: number; hours: number; typeHours: Record<string, number> }[]
  taskDetails: { taskId: number; taskName: string; taskType: string; assigneeName: string; hours: number; isHuman: boolean; isInstrument: boolean }[]
}

const loading = ref(true)
const viewMode = ref('project')
const projectStats = ref<ProjectStat[]>([])
const userStats = ref<UserStat[]>([])
const router = useRouter()
const dateRange = ref<any>(null)
const maxProjectHours = ref(0)
const maxUserHours = ref(0)
const taskTypeMap = ref<Record<string, string>>({})

const totalHours = computed(() => {
  if (viewMode.value === 'project') return projectStats.value.reduce((s, p) => s + p.totalHours, 0)
  return userStats.value.reduce((s, u) => s + u.totalHours, 0)
})

function pct(hours: number) {
  const max = viewMode.value === 'project' ? maxProjectHours.value : maxUserHours.value
  return max > 0 ? (hours / max) * 100 : 0
}

function expandedRowRender(record: ProjectStat) {
  const cols = [
    { title: '人员', dataIndex: 'userName', key: 'name' },
    { title: '任务数', dataIndex: 'taskCount', key: 'count', width: 80, align: 'center' as const },
    { title: '工时(h)', dataIndex: 'hours', key: 'hours', width: 100, align: 'center' as const, customRender: ({ text }: any) => text.toFixed(1) },
    { title: '溶液配制(h)', key: 'sol', width: 110, align: 'center' as const, customRender: ({ record: r }: any) => (r.typeHours.solution_prep || 0).toFixed(1) },
    { title: '样品前处理(h)', key: 'samp', width: 110, align: 'center' as const, customRender: ({ record: r }: any) => (r.typeHours.sample_prep || 0).toFixed(1) },
    { title: '序列运行(h)', key: 'run', width: 110, align: 'center' as const, customRender: ({ record: r }: any) => (r.typeHours.instrument_run || 0).toFixed(1) },
    { title: '出具报告(h)', key: 'rep', width: 110, align: 'center' as const, customRender: ({ record: r }: any) => (r.typeHours.report || 0).toFixed(1) },
  ]
  // Use a simple div-based table since we can't use a-table inside expandedRowRender easily
  return h('div', { style: 'padding: 8px 0' }, [
    h('table', { style: 'width: 100%; border-collapse: collapse; font-size: 13px' }, [
      h('thead', {}, [
        h('tr', { style: 'background: #f8fafc' }, cols.map(c =>
          h('th', { style: 'padding: 6px 12px; text-align: ' + (c.align || 'left') + '; border-bottom: 1px solid #e5e7eb; font-weight: 600; color: #475569' }, c.title)
        ))
      ]),
      h('tbody', {}, record.users.map(u =>
        h('tr', { style: 'border-bottom: 1px solid #f1f5f9' }, [
          h('td', { style: 'padding: 6px 12px' }, u.userName),
          h('td', { style: 'padding: 6px 12px; text-align: center' }, String(u.taskCount)),
          h('td', { style: 'padding: 6px 12px; text-align: center' }, u.hours.toFixed(1)),
          h('td', { style: 'padding: 6px 12px; text-align: center' }, '0.0'),
          h('td', { style: 'padding: 6px 12px; text-align: center' }, '0.0'),
          h('td', { style: 'padding: 6px 12px; text-align: center' }, '0.0'),
          h('td', { style: 'padding: 6px 12px; text-align: center' }, '0.0'),
        ])
      ))
    ])
  ])
}

function getTypeName(code: string) { return taskTypeMap.value[code] || code }
function getTypeColor(code: string) {
      const m: Record<string, string> = { FFKF_001: '#8b5cf6', QCFA_001: '#f59e0b', FFYZ_001: '#10b981', SJCL_001: '#3b82f6', ZXBG_001: '#ef4444' }
  return m[code] || '#94a3b8'
}

function openDetail(record: ProjectStat) {
  router.push(`/operations/project-tasks?id=${record.projectId}&name=${encodeURIComponent(record.projectName)}`)
}

function onFilterChange() { buildStats() }

async function buildStats() {
  loading.value = true
  try {
    const [projects, users, types] = await Promise.all([getProjects(), getUserDirectory(), getTaskTypes()])
    const userMap = new Map<number, string>()
    users.forEach(u => { userMap.set(u.id, u.display_name) })
    const typeMap: Record<string, string> = {}
    types.forEach(t => { typeMap[t.code] = t.name })

    const userStatsMap = new Map<number, UserStat>()
    const projectStatsMap = new Map<number, ProjectStat>()

    for (const p of projects) {
      const tasks = p.tasks || []

      // Date filter
      if (dateRange.value?.[0] && dateRange.value?.[1]) {
        const taskDate = p.start_date ? dayjs(p.start_date) : null
        if (!taskDate || taskDate.isBefore(dateRange.value[0]) || taskDate.isAfter(dateRange.value[1])) continue
      }

      if (!projectStatsMap.has(p.id)) {
        projectStatsMap.set(p.id, {
          projectId: p.id,
          projectCode: p.code,
          projectName: p.name,
          clientName: p.client_name || '-',
          manager: p.manager_name || '-',
          taskCount: 0,
          totalHours: 0,
          humanHours: 0,
          instrumentHours: 0,
          users: [],
          taskDetails: []
        })
      }
      const pStat = projectStatsMap.get(p.id)!
      const projUserMap = new Map<number, { userName: string; taskCount: number; hours: number; typeHours: Record<string, number> }>()

      for (const t of tasks) {
        const hours = t.est_duration_hours || 0
        if (hours <= 0) continue

        const isHuman = t.requires_instrument === false || (taskTypeMap.value[t.task_type] || '').includes('human')
        const isInstrument = t.requires_instrument === true || (taskTypeMap.value[t.task_type] || '').includes('instrument')

        pStat.taskDetails.push({
          taskId: t.id, taskName: t.name, taskType: t.task_type,
          assigneeName: t.assignee_id ? (userMap.get(t.assignee_id) || t.assignee_name || '-') : '-',
          hours, isHuman, isInstrument
        })

        pStat.taskCount++
        pStat.totalHours += hours
        if (isHuman && !isInstrument) pStat.humanHours += hours
        else if (isInstrument && !isHuman) pStat.instrumentHours += hours
        else { pStat.humanHours += hours / 2; pStat.instrumentHours += hours / 2 }

        // Per-user stats within project
        if (t.assignee_id) {
          if (!projUserMap.has(t.assignee_id)) {
            projUserMap.set(t.assignee_id, {
              userName: userMap.get(t.assignee_id) || t.assignee_name || '未知',
              taskCount: 0, hours: 0, typeHours: {}
            })
          }
          const pu = projUserMap.get(t.assignee_id)!
          pu.taskCount++
          pu.hours += hours
          pu.typeHours[t.task_type] = (pu.typeHours[t.task_type] || 0) + hours

          // Global user stats
          if (!userStatsMap.has(t.assignee_id)) {
            userStatsMap.set(t.assignee_id, {
              userId: t.assignee_id,
              userName: userMap.get(t.assignee_id) || t.assignee_name || '未知',
              taskCount: 0, totalHours: 0, typeHours: {}
            })
          }
          const uStat = userStatsMap.get(t.assignee_id)!
          uStat.taskCount++
          uStat.totalHours += hours
          uStat.typeHours[t.task_type] = (uStat.typeHours[t.task_type] || 0) + hours
        }
      }

      pStat.users = Array.from(projUserMap.values()).sort((a, b) => b.hours - a.hours)
    }

    projectStats.value = Array.from(projectStatsMap.values()).sort((a, b) => b.totalHours - a.totalHours)
    userStats.value = Array.from(userStatsMap.values()).sort((a, b) => b.totalHours - a.totalHours)
    maxProjectHours.value = projectStats.value.length > 0 ? projectStats.value[0].totalHours : 0
    maxUserHours.value = userStats.value.length > 0 ? userStats.value[0].totalHours : 0
  } catch { message.error('加载数据失败') }
  finally { loading.value = false }
}

onMounted(() => { buildStats() })
</script>
