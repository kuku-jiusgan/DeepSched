<template>
  <div>
    <div class="page-header">
      <h2>排程规则配置</h2>
    </div>

    <a-spin v-if="loading" size="large" style="display: block; margin: 80px auto" />

    <template v-else>
      <!-- 决策变量 -->
      <a-card title="决策变量" size="small" style="margin-bottom: 16px">
        <template #extra><a-tag color="blue">{{ decisionVars.length }} 项</a-tag></template>
        <div v-for="rule in decisionVars" :key="rule.id" class="rule-row">
          <div class="rule-info">
            <div class="rule-name">{{ rule.name }}</div>
            <div class="rule-desc">{{ rule.description }}</div>
          </div>
          <div v-operation="'edit'" class="rule-params">
            <template v-if="rule.code === 'time_granularity'">
              <span style="margin-right: 8px; color: #64748b; font-size: 13px">当前值：</span>
              <a-input-number
                :value="getNumberParam(rule, 'value', 30)"
                :min="15"
                :max="120"
                :step="15"
                style="width: 100px"
                @change="(v: number | null) => saveRuleParam(rule, 'value', v ?? 30)"
              />
              <span style="margin: 0 8px; color: #64748b; font-size: 13px">分钟</span>
            </template>
            <template v-else-if="rule.code === 'planning_horizon'">
              <span style="margin-right: 8px; color: #64748b; font-size: 13px">当前值：</span>
              <a-input-number
                :value="getNumberParam(rule, 'value', 90)"
                :min="7"
                :max="180"
                :step="1"
                style="width: 100px"
                @change="(v: number | null) => saveRuleParam(rule, 'value', v ?? 90)"
              />
              <span style="margin: 0 8px; color: #64748b; font-size: 13px">天</span>
            </template>
          </div>
        </div>
      </a-card>

      <!-- 约束条件 -->
      <a-card title="约束条件" size="small" style="margin-bottom: 16px">
        <template #extra>
          <a-space>
            <a-tag :color="constraintEnabledCount === constraints.length ? 'green' : 'orange'">
              {{ constraintEnabledCount }}/{{ constraints.length }} 已启用
            </a-tag>
          </a-space>
        </template>
        <div v-for="(rule, index) in constraints" :key="rule.id" class="rule-row" :class="{ 'rule-disabled': !rule.is_enabled }">
          <div class="constraint-order">{{ index + 1 }}</div>
          <div class="rule-info">
            <div class="rule-name">
              <span :style="{ color: rule.is_enabled ? '#1e293b' : '#94a3b8' }">{{ rule.name }}</span>
              <a-tag v-if="rule.params?.strict" color="red" style="margin-left: 6px; font-size: 10px">硬约束</a-tag>
              <a-tag v-else color="blue" style="margin-left: 6px; font-size: 10px">软约束</a-tag>
              <a-tag v-if="isRuleLocked(rule)" color="default" style="margin-left: 6px; font-size: 10px">求解器必选</a-tag>
            </div>
            <div class="rule-desc" :style="{ color: rule.is_enabled ? '#64748b' : '#cbd5e1' }">{{ rule.description }}</div>
          </div>
          <div v-operation="'edit'" class="rule-params">
            <template v-if="rule.code === 'working_hours'">
              <span style="font-size: 12px; color: #94a3b8">白天</span>
              <a-select
                :value="getTimeParam(rule, 'day_start', DEFAULT_WORK_START)"
                :options="workStartOptions"
                size="small"
                style="width: 86px"
                :disabled="!rule.is_enabled"
                @change="(v: string) => saveWorkingTimeParam(rule, 'day_start', v)"
              />
              <span style="font-size: 12px; color: #94a3b8">-</span>
              <a-select
                :value="getTimeParam(rule, 'day_end', DEFAULT_WORK_END)"
                :options="workEndOptions"
                size="small"
                style="width: 86px"
                :disabled="!rule.is_enabled"
                @change="(v: string) => saveWorkingTimeParam(rule, 'day_end', v)"
              />
              <span class="param-divider"></span>
              <span style="font-size: 12px; color: #64748b">包含周末</span>
              <a-switch
                :checked="getBooleanParam(rule, 'include_weekends', false)"
                size="small"
                :disabled="!rule.is_enabled"
                @change="(checked: boolean) => saveRuleParam(rule, 'include_weekends', checked)"
              />
              <span style="font-size: 12px; color: #64748b">包含节假日</span>
              <a-switch
                :checked="getBooleanParam(rule, 'include_holidays', false)"
                size="small"
                :disabled="!rule.is_enabled"
                @change="(checked: boolean) => saveRuleParam(rule, 'include_holidays', checked)"
              />
            </template>
            <template v-else-if="rule.code === 'cross_project_setup'">
              <span style="font-size: 12px; color: #94a3b8">切换间隔</span>
              <a-input-number :value="getNumberParam(rule, 'setup_hours', 2)" :min="0" :max="24" :step="0.5" size="small" style="width: 70px" :disabled="!rule.is_enabled" @change="(v: number | null) => saveRuleParam(rule, 'setup_hours', v ?? 2)" />
              <span style="font-size: 12px; color: #94a3b8">小时</span>
            </template>
            <template v-else-if="rule.code === 'freezing'">
              <span style="font-size: 12px; color: #94a3b8">冻结期</span>
              <a-input-number :value="getNumberParam(rule, 'freeze_days', 3)" :min="0" :max="30" size="small" style="width: 70px" :disabled="!rule.is_enabled" @change="(v: number | null) => saveRuleParam(rule, 'freeze_days', v ?? 3)" />
              <span style="font-size: 12px; color: #94a3b8">天（按自然日，1天=今天24点）</span>
            </template>
            <template v-else-if="rule.code === 'sibling_task_cohesion'">
              <span style="font-size: 12px; color: #94a3b8">靠拢权重</span>
              <a-input-number
                :value="getNumberParam(rule, 'weight', 1)"
                :min="0"
                :max="10"
                :step="0.1"
                size="small"
                style="width: 80px"
                :disabled="!rule.is_enabled"
                @change="(v: number | null) => saveRuleParam(rule, 'weight', v ?? 1)"
              />
            </template>
          </div>
          <div v-operation="'toggle'" class="rule-toggle">
            <a-switch v-model:checked="rule.is_enabled" size="small" :disabled="isRuleLocked(rule)" @change="toggleRule(rule)" />
          </div>
        </div>
      </a-card>

      <!-- 目标函数 -->
      <a-card title="目标函数" size="small" style="margin-bottom: 16px">
        <template #extra><a-tag color="purple">{{ objectives.length }} 项目标</a-tag></template>
        <div v-for="rule in objectives" :key="rule.id" class="rule-row" :class="{ 'rule-disabled': !rule.is_enabled }">
          <div class="rule-info">
            <div class="rule-name">
              <span :style="{ color: rule.is_enabled ? '#1e293b' : '#94a3b8' }">{{ rule.name }}</span>
              <a-tag v-if="rule.code === 'weighted_tardiness'" color="red" style="margin-left: 6px; font-size: 10px">主目标</a-tag>
              <a-tag v-else color="default" style="margin-left: 6px; font-size: 10px">次目标</a-tag>
            </div>
            <div class="rule-desc" :style="{ color: rule.is_enabled ? '#64748b' : '#cbd5e1' }">{{ rule.description }}</div>
          </div>
          <div v-operation="'edit'" class="rule-params">
            <span style="font-size: 12px; color: #94a3b8; margin-right: 6px">权重</span>
            <a-input-number
              :value="getNumberParam(rule, 'weight', 0)"
              :min="0"
              :max="10"
              :step="0.1"
              size="small"
              style="width: 80px"
              :disabled="!rule.is_enabled"
              @change="(v: number | null) => saveRuleParam(rule, 'weight', v ?? 0)"
            />
          </div>
          <div v-operation="'toggle'" class="rule-toggle">
            <a-switch v-model:checked="rule.is_enabled" size="small" @change="toggleRule(rule)" />
          </div>
        </div>
      </a-card>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import {
  getScheduleRules,
  updateScheduleRule,
  toggleScheduleRule,
  type ScheduleRule,
  type ScheduleRuleParamValue,
} from '@/services/api'

const rules = ref<ScheduleRule[]>([])
const loading = ref(true)
const DEFAULT_WORK_START = '08:30'
const DEFAULT_WORK_END = '20:00'
const MINUTES_PER_DAY = 24 * 60

const decisionVars = computed(() => rules.value.filter(r => r.category === 'decision_variable'))
const constraints = computed(() => rules.value
  .filter(rule => rule.category === 'constraint')
  .sort((left, right) => left.sort_order - right.sort_order))
const objectives = computed(() => rules.value.filter(r => r.category === 'objective'))
const constraintEnabledCount = computed(() => constraints.value.filter(r => r.is_enabled).length)
const workStartOptions = computed(() => buildTimeOptions(0, MINUTES_PER_DAY - 30))
const workEndOptions = computed(() => buildTimeOptions(30, MINUTES_PER_DAY))

async function fetchRules() {
  loading.value = true
  try {
    rules.value = await getScheduleRules()
  } catch {
    message.error('加载排程规则失败')
  } finally {
    loading.value = false
  }
}

function getNumberParam(rule: ScheduleRule, key: string, fallback: number) {
  const value = rule.params?.[key]
  return typeof value === 'number' ? value : fallback
}

function getTimeParam(rule: ScheduleRule, key: string, fallback: string) {
  const value = rule.params?.[key]
  if (typeof value === 'string' && isHalfHourTime(value)) return value
  if (typeof value === 'number') return formatMinutes(value * 60)
  return fallback
}

function getBooleanParam(rule: ScheduleRule, key: string, fallback: boolean) {
  const value = rule.params?.[key]
  return typeof value === 'boolean' ? value : fallback
}

function isRuleLocked(rule: ScheduleRule) {
  return rule.params?.locked === true
}

async function saveRuleParam(rule: ScheduleRule, key: string, value: ScheduleRuleParamValue) {
  const newParams = { ...rule.params, [key]: value }
  try {
    await updateScheduleRule(rule.id, { params: newParams })
    rule.params = newParams
    message.success(`${rule.name} 已更新`)
  } catch {
    message.error('保存失败')
  }
}

async function saveWorkingTimeParam(rule: ScheduleRule, key: 'day_start' | 'day_end', value: string) {
  const start = key === 'day_start' ? value : getTimeParam(rule, 'day_start', DEFAULT_WORK_START)
  const end = key === 'day_end' ? value : getTimeParam(rule, 'day_end', DEFAULT_WORK_END)
  if (timeToMinutes(start) >= timeToMinutes(end)) {
    message.error('开始时间必须早于结束时间')
    return
  }
  await saveRuleParam(rule, key, value)
}

async function toggleRule(rule: ScheduleRule) {
  try {
    const updated = await toggleScheduleRule(rule.id)
    rule.is_enabled = updated.is_enabled
  } catch {
    rule.is_enabled = !rule.is_enabled
    message.error('切换失败')
  }
}

function buildTimeOptions(startMinutes: number, endMinutes: number) {
  const options: { label: string; value: string }[] = []
  for (let minutes = startMinutes; minutes <= endMinutes; minutes += 30) {
    const value = formatMinutes(minutes)
    options.push({ label: value, value })
  }
  return options
}

function formatMinutes(totalMinutes: number) {
  const hours = Math.floor(totalMinutes / 60)
  const minutes = totalMinutes % 60
  return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}`
}

function isHalfHourTime(value: string) {
  return /^([01]\d|2[0-4]):(00|30)$/.test(value) && timeToMinutes(value) <= MINUTES_PER_DAY
}

function timeToMinutes(value: string) {
  const [hours = '0', minutes = '0'] = value.split(':')
  return Number(hours) * 60 + Number(minutes)
}

onMounted(fetchRules)
</script>

<style scoped>
.rule-row {
  display: flex;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid #f1f5f9;
  gap: 16px;
}
.rule-row:last-child { border-bottom: none; }
.rule-disabled { opacity: 0.55; }
.rule-info { flex: 1; min-width: 0; }
.constraint-order {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  background: #f1f5f9;
  color: #475569;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 600;
}
.rule-name { font-size: 14px; font-weight: 500; margin-bottom: 2px; }
.rule-desc { font-size: 12px; color: #64748b; line-height: 1.4; }
.rule-params { display: flex; align-items: center; gap: 4px; white-space: nowrap; }
.param-divider {
  width: 1px;
  height: 16px;
  background: #e2e8f0;
  margin: 0 6px;
}
.rule-toggle { flex-shrink: 0; }
</style>
