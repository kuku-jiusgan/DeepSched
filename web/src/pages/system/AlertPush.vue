<template>
  <div class="alert-page">
    <header class="page-header alert-page-header">
      <div>
        <h2>智能预警推送</h2>
        <p>统一管理预警触发条件、接收对象与双通道发送结果。</p>
      </div>
    </header>

    <div v-if="loading" class="loading-panel">
      <a-skeleton active :paragraph="{ rows: 8 }" />
    </div>

    <template v-else>
      <section class="summary-strip" aria-label="预警状态总览">
        <div class="summary-item">
          <span>启用规则</span>
          <strong>{{ activeRuleCount }}<small>/{{ rules.length }}</small></strong>
        </div>
        <div class="summary-item">
          <span>推送通道</span>
          <strong>2<small>站内 + 企微</small></strong>
        </div>
        <div class="summary-item">
          <span>发送中</span>
          <strong :class="{ 'status-warning': pendingCount > 0 }">{{ pendingCount }}</strong>
        </div>
        <div class="summary-item">
          <span>发送失败</span>
          <strong :class="{ 'status-danger': failedCount > 0 }">{{ failedCount }}</strong>
        </div>
      </section>

      <a-tabs v-model:activeKey="activeTab" class="alert-tabs">
        <a-tab-pane key="rules" tab="预警规则">
          <section class="channel-panel">
            <div class="section-heading">
              <div>
                <div class="section-title-row">
                  <h3>推送通道</h3>
                  <a-tag :color="isWeComConfigured ? 'green' : 'orange'">
                    {{ isWeComConfigured ? '配置完整' : '待配置' }}
                  </a-tag>
                </div>
                <p>所有启用规则固定发送站内通知和企业微信，确保负责人及时收到。</p>
              </div>
              <div class="channel-badges" aria-label="固定推送通道">
                <span><span class="channel-dot channel-dot-site" />站内通知</span>
                <span><span class="channel-dot channel-dot-wecom" />企业微信</span>
              </div>
            </div>

            <a-alert
              v-if="!isWeComConfigured"
              type="warning"
              show-icon
              message="企业微信凭据尚未配置完整"
              description="站内通知可正常发送；企业微信消息会记录为发送失败，补齐配置后即可恢复。"
              class="channel-alert"
            />

            <a-form layout="vertical" class="wecom-form">
              <a-form-item label="CorpID">
                <a-input v-model:value="pushConfig.wecom_corp_id" placeholder="企业 ID" allow-clear />
              </a-form-item>
              <a-form-item label="AgentId">
                <a-input v-model:value="pushConfig.wecom_agent_id" placeholder="应用 AgentId" allow-clear />
              </a-form-item>
              <a-form-item label="Secret">
                <a-input-password
                  v-model:value="pushConfig.wecom_secret"
                  :placeholder="pushConfig.has_wecom_secret ? '已配置，留空则不修改' : '应用 Secret'"
                />
              </a-form-item>
              <a-form-item class="channel-save-item">
                <a-button v-operation="'edit_channel'" :loading="savingPushConfig" @click="savePushConfig">保存通道配置</a-button>
              </a-form-item>
            </a-form>
          </section>

          <section class="rules-panel">
            <div class="section-heading rules-heading">
              <div>
                <h3>规则清单</h3>
                <p>关闭规则后将停止对应预警；接收对象为空时不会产生通知。</p>
              </div>
              <div class="rules-heading-actions">
                <span class="rule-count">共 {{ rules.length }} 条</span>
                <a-button v-operation="'edit_rule'" type="primary" :loading="savingRules" @click="saveAll">
                  <SaveOutlined /> 保存规则
                </a-button>
              </div>
            </div>

            <div class="rule-list">
              <article v-for="rule in rules" :key="rule.id" class="rule-row" :class="{ 'is-disabled': !rule.enabled }">
                <div class="rule-identity">
                  <div class="rule-name-line">
                    <span class="rule-indicator" :class="`rule-indicator-${notificationTypeTone(rule.rule_type)}`" />
                    <strong>{{ rule.name }}</strong>
                  </div>
                  <p>{{ ruleDescription(rule.rule_type) }}</p>
                </div>

                <div class="rule-field rule-recipient-field">
                  <label>接收对象</label>
                  <a-select
                    v-model:value="rule._roles"
                    mode="multiple"
                    :options="roleOptions"
                    :max-tag-count="2"
                    placeholder="请选择接收对象"
                  />
                  <span v-if="!rule._roles.length" class="field-warning">未选择接收对象</span>
                </div>

                <div class="rule-field rule-trigger-field">
                  <label>触发条件</label>
                  <div v-if="rule.rule_type === 'task_start_delay'" class="inline-threshold">
                    <span>计划开始前</span>
                    <a-input-number v-model:value="rule.threshold_minutes" :min="5" :max="480" :step="5" />
                    <span>分钟提醒</span>
                  </div>
                  <div v-else-if="rule.rule_type === 'hours_exceeded'" class="inline-threshold">
                    <span>实际工时达到预计</span>
                    <a-input-number v-model:value="rule.threshold_percent" :min="100" :max="300" :step="10" />
                    <span>%</span>
                  </div>
                  <span v-else class="trigger-copy">{{ ruleTriggerText(rule.rule_type) }}</span>
                </div>

                <div class="rule-status">
                  <span>{{ rule.enabled ? '已启用' : '已停用' }}</span>
                  <a-switch v-model:checked="rule.enabled" />
                </div>
              </article>
            </div>
          </section>
        </a-tab-pane>

        <a-tab-pane key="history" tab="推送历史">
          <section class="history-panel">
            <div class="history-toolbar">
              <div>
                <h3>推送记录</h3>
                <p>展示最近 {{ history.length }} 条记录，可按类型、通道和发送结果快速排查。</p>
              </div>
              <div class="history-filters">
                <a-input v-model:value="historyKeyword" allow-clear placeholder="搜索接收人、标题或内容" class="history-search" />
                <a-select v-model:value="historyType" :options="historyTypeOptions" allow-clear placeholder="全部类型" />
                <a-select v-model:value="historyChannel" :options="channelOptions" allow-clear placeholder="全部通道" />
                <a-select v-model:value="historyStatus" :options="deliveryOptions" allow-clear placeholder="全部结果" />
              </div>
            </div>

            <a-table
              :dataSource="filteredHistory"
              :columns="historyColumns"
              rowKey="id"
              size="middle"
              :scroll="{ x: 980 }"
              :pagination="{ pageSize: 15, showSizeChanger: true, showTotal: (total: number) => `共 ${total} 条` }"
            >
              <template #bodyCell="{ column, record }">
                <template v-if="column.key === 'created_at'">
                  <span class="time-cell">{{ dayjs(record.created_at).format('YYYY-MM-DD HH:mm') }}</span>
                </template>
                <template v-else-if="column.key === 'type'">
                  <a-tag :color="notificationTypeColor(record.n_type)">{{ notificationTypeLabel(record.n_type) }}</a-tag>
                </template>
                <template v-else-if="column.key === 'channel'">
                  <span class="channel-cell">
                    <span class="channel-dot" :class="record.channel === 'wecom' ? 'channel-dot-wecom' : 'channel-dot-site'" />
                    {{ channelLabel(record.channel) }}
                  </span>
                </template>
                <template v-else-if="column.key === 'message'">
                  <div class="message-cell">
                    <strong>{{ record.title || '系统通知' }}</strong>
                    <span>{{ record.content || '-' }}</span>
                  </div>
                </template>
                <template v-else-if="column.key === 'delivery_status'">
                  <a-tooltip :title="record.error_message || ''">
                    <a-tag :color="deliveryStatusColor(record.delivery_status)">
                      {{ deliveryStatusLabel(record.delivery_status) }}
                    </a-tag>
                  </a-tooltip>
                </template>
                <template v-else-if="column.key === 'status'">
                  <span class="read-status" :class="{ 'is-read': record.is_read }">
                    {{ record.is_read ? '已读' : '未读' }}
                  </span>
                  <span v-if="record.is_confirmed !== null" class="confirm-status">
                    · {{ record.is_confirmed ? '已确认' : '待确认' }}
                  </span>
                </template>
              </template>
              <template #emptyText>
                <a-empty description="没有符合当前筛选条件的推送记录" />
              </template>
            </a-table>
          </section>
        </a-tab-pane>
      </a-tabs>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref, onMounted } from 'vue'
import { message } from 'ant-design-vue'
import { SaveOutlined } from '@ant-design/icons-vue'
import dayjs from 'dayjs'
import {
  getAlertRules,
  getNotificationHistory,
  getPushConfig,
  updateAlertRule,
  updatePushConfig,
  type AlertRule,
  type NotificationRecord,
  type PushChannelConfig,
} from '@/services/api'

interface RuleWithRoles extends AlertRule { _roles: string[] }
interface PushChannelConfigForm extends PushChannelConfig { wecom_secret: string }

const loading = ref(true)
const savingRules = ref(false)
const savingPushConfig = ref(false)
const rules = ref<RuleWithRoles[]>([])
const history = ref<NotificationRecord[]>([])
const activeTab = ref('rules')
const historyKeyword = ref('')
const historyType = ref<string | undefined>()
const historyChannel = ref<string | undefined>()
const historyStatus = ref<string | undefined>()
const pushConfig = reactive<PushChannelConfigForm>({
  id: 0,
  wecom_enabled: true,
  wecom_corp_id: '',
  wecom_agent_id: '',
  has_wecom_secret: false,
  wecom_secret: '',
})

const roleOptions = [
  { label: '系统管理员', value: '系统管理员' },
  { label: '项目管理员', value: '项目管理员' },
  { label: '分析所所长', value: '分析所所长' },
  { label: '技术组长', value: '技术组长' },
  { label: '项目负责人（项目指定）', value: '项目负责人' },
  { label: '任务负责人（任务指定）', value: '任务负责人' },
  { label: '技术员', value: '技术员' },
]

const channelOptions = [
  { label: '站内通知', value: 'site' },
  { label: '企业微信', value: 'wecom' },
]

const deliveryOptions = [
  { label: '成功', value: 'success' },
  { label: '发送中', value: 'pending' },
  { label: '失败', value: 'failed' },
]

const historyColumns = [
  { title: '推送时间', key: 'created_at', width: 150 },
  { title: '类型', key: 'type', width: 125 },
  { title: '通道', key: 'channel', width: 105 },
  { title: '接收人', dataIndex: 'user_name', key: 'user', width: 120, ellipsis: true },
  { title: '消息内容', key: 'message', minWidth: 320 },
  { title: '发送结果', key: 'delivery_status', width: 100 },
  { title: '状态', key: 'status', width: 120 },
]

const isWeComConfigured = computed(() => Boolean(
  pushConfig.wecom_corp_id?.trim()
  && pushConfig.wecom_agent_id?.trim()
  && (pushConfig.has_wecom_secret || pushConfig.wecom_secret?.trim())
))
const activeRuleCount = computed(() => rules.value.filter(rule => rule.enabled).length)
const pendingCount = computed(() => history.value.filter(item => item.delivery_status === 'pending').length)
const failedCount = computed(() => history.value.filter(item => item.delivery_status === 'failed').length)
const historyTypeOptions = computed(() => [...new Set(history.value.map(item => item.n_type))]
  .map(type => ({ label: notificationTypeLabel(type), value: type })))
const filteredHistory = computed(() => {
  const keyword = historyKeyword.value.trim().toLocaleLowerCase()
  return history.value.filter(item => {
    const matchesKeyword = !keyword || [item.user_name, item.title, item.content]
      .some(value => value?.toLocaleLowerCase().includes(keyword))
    return matchesKeyword
      && (!historyType.value || item.n_type === historyType.value)
      && (!historyChannel.value || item.channel === historyChannel.value)
      && (!historyStatus.value || item.delivery_status === historyStatus.value)
  })
})

async function fetchData() {
  loading.value = true
  try {
    const [data, historyData, config] = await Promise.all([
      getAlertRules(),
      getNotificationHistory(),
      getPushConfig(),
    ])
    rules.value = data.map(rule => ({
      ...rule,
      enable_site: true,
      enable_wecom: true,
      _roles: parseRoles(rule.notify_roles),
    }))
    history.value = historyData
    Object.assign(pushConfig, config)
  } catch {
    message.error('加载预警配置失败')
  } finally {
    loading.value = false
  }
}

async function saveAll() {
  savingRules.value = true
  try {
    await Promise.all(rules.value.map(rule => updateAlertRule(rule.id, {
      enabled: rule.enabled,
      enable_site: true,
      enable_wecom: true,
      notify_roles: JSON.stringify(rule._roles),
      threshold_minutes: rule.threshold_minutes,
      threshold_percent: rule.threshold_percent,
    })))
    message.success('预警规则已保存')
  } catch {
    message.error('预警规则保存失败')
  } finally {
    savingRules.value = false
  }
}

async function savePushConfig() {
  if (!isWeComConfigured.value) {
    message.warning('请完整填写 CorpID、AgentId 和 Secret')
    return
  }
  savingPushConfig.value = true
  try {
    const secret = pushConfig.wecom_secret?.trim()
    const data = await updatePushConfig({
      wecom_enabled: true,
      wecom_corp_id: pushConfig.wecom_corp_id,
      wecom_agent_id: pushConfig.wecom_agent_id,
      ...(secret ? { wecom_secret: secret } : {}),
    })
    Object.assign(pushConfig, data)
    pushConfig.wecom_secret = ''
    message.success('推送通道配置已保存')
  } catch {
    message.error('企业微信配置保存失败')
  } finally {
    savingPushConfig.value = false
  }
}

function parseRoles(value: string | null) {
  if (!value) return []
  try {
    const parsed: unknown = JSON.parse(value)
    return Array.isArray(parsed) ? parsed.filter((role): role is string => typeof role === 'string') : []
  } catch {
    return []
  }
}

function ruleDescription(type: string) {
  const descriptions: Record<string, string> = {
    task_start_delay: '任务到达计划开始时间前，提醒对应任务负责人做好准备。',
    task_end_delay: '已开始任务超过计划结束时间仍未点击结束。',
    schedule_changed: '重排或插单改变任务原定时间。',
    task_schedule_advanced: '任何排程操作使任务计划开始时间提前。',
    task_schedule_delayed: '排程调整导致任务计划开始时间被动后移。',
    hours_exceeded: '任务实际投入工时超过预计工时。',
    instrument_fault_reschedule: '仪器故障导致相关任务后移。',
    instrument_fault_schedule_conflict: '故障处理后无法生成无冲突排程。',
    approval_pending: '方案已完成，等待提交客户签批。',
    approval_due: '方案签批临近预计时间或已经超期。',
    approval_schedule_result: '客户签批后反馈后续任务排程结果。',
  }
  return descriptions[type] || '满足规则条件时发送预警通知。'
}

function ruleTriggerText(type: string) {
  const triggers: Record<string, string> = {
    task_end_delay: '到达计划结束时间仍未结束',
    schedule_changed: '任务排程发生变更',
    task_schedule_advanced: '任务计划时间提前',
    task_schedule_delayed: '任务计划时间被动后移',
    instrument_fault_reschedule: '故障重排已应用',
    instrument_fault_schedule_conflict: '故障重排出现冲突',
    approval_pending: '方案进入待提交状态',
    approval_due: '签批临近或超过预计时间',
    approval_schedule_result: '签批后的排程计算完成',
  }
  return triggers[type] || '事件发生时立即触发'
}

function notificationTypeLabel(type: string) {
  const labels: Record<string, string> = {
    instrument_fault_reschedule: '故障重排',
    instrument_fault_schedule_conflict: '故障冲突',
    schedule_changed: '排程变更',
    task_start_delay: '开始提醒',
    task_end_delay: '结束提醒',
    hours_exceeded: '工时超标',
    task_schedule_advanced: '任务前移',
    task_schedule_delayed: '任务被动后移',
    approval_pending: '方案待提交',
    approval_due: '签批提醒',
    approval_schedule_result: '签批排程',
  }
  return labels[type] || type
}

function notificationTypeColor(type: string) {
  const colors: Record<string, string> = {
    instrument_fault_schedule_conflict: 'red',
    instrument_fault_reschedule: 'orange',
    schedule_changed: 'blue',
    task_start_delay: 'gold',
    task_end_delay: 'volcano',
    hours_exceeded: 'purple',
    task_schedule_advanced: 'cyan',
    approval_pending: 'default',
    approval_due: 'orange',
    approval_schedule_result: 'green',
  }
  return colors[type] || 'default'
}

function notificationTypeTone(type: string) {
  if (['instrument_fault_schedule_conflict', 'task_end_delay'].includes(type)) return 'danger'
  if (['instrument_fault_reschedule', 'task_start_delay', 'approval_due'].includes(type)) return 'warning'
  if (['approval_schedule_result'].includes(type)) return 'success'
  return 'accent'
}

function channelLabel(channel: string) {
  return channel === 'wecom' ? '企业微信' : '站内'
}

function deliveryStatusLabel(status: string) {
  if (status === 'pending') return '发送中'
  return status === 'success' ? '成功' : '失败'
}

function deliveryStatusColor(status: string) {
  if (status === 'pending') return 'orange'
  return status === 'success' ? 'green' : 'red'
}

onMounted(fetchData)
</script>

<style scoped>
.alert-page { min-width: 0; }
.alert-page-header { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-md); }
.alert-page-header h2 { margin: 0; }
.alert-page-header p { color: var(--color-text-secondary); }
.loading-panel { padding: var(--space-lg); background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-md); }

.summary-strip { display: flex; margin-bottom: var(--space-md); overflow: hidden; background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.summary-item { flex: 1 1 0; min-width: 130px; padding: 12px 18px; border-right: 1px solid var(--color-border-light); }
.summary-item:last-child { border-right: 0; }
.summary-item span { display: block; color: var(--color-text-secondary); font-size: 12px; }
.summary-item strong { display: flex; align-items: baseline; gap: 6px; margin-top: 3px; color: var(--color-text-primary); font-size: 22px; line-height: 1.2; }
.summary-item small { color: var(--color-text-tertiary); font-size: 11px; font-weight: 500; }
.summary-item .status-warning { color: var(--color-warning); }
.summary-item .status-danger { color: var(--color-danger); }

.alert-tabs :deep(.ant-tabs-nav) { margin-bottom: var(--space-md); }
.channel-panel, .rules-panel, .history-panel { background: var(--color-surface); border: 1px solid var(--color-border); border-radius: var(--radius-md); }
.channel-panel { margin-bottom: var(--space-md); padding: var(--space-md) var(--space-lg); }
.section-heading { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-md); }
.section-heading h3, .history-toolbar h3 { margin: 0; color: var(--color-text-primary); font-size: 16px; }
.section-heading p, .history-toolbar p { margin: 3px 0 0; color: var(--color-text-secondary); font-size: 12px; }
.section-title-row { display: flex; align-items: center; gap: var(--space-sm); }
.channel-badges { display: flex; flex-wrap: wrap; gap: var(--space-md); color: var(--color-text-secondary); font-size: 12px; }
.channel-badges > span, .channel-cell { display: inline-flex; align-items: center; gap: 6px; }
.channel-dot { width: 7px; height: 7px; flex: 0 0 auto; border-radius: 999px; }
.channel-dot-site { background: var(--color-accent); }
.channel-dot-wecom { background: #0891b2; }
.channel-alert { margin-top: var(--space-md); }
.wecom-form { display: grid; grid-template-columns: minmax(160px, 1fr) minmax(140px, 0.75fr) minmax(200px, 1.25fr) auto; align-items: end; gap: var(--space-md); margin-top: var(--space-md); }
.wecom-form :deep(.ant-form-item) { min-width: 0; margin-bottom: 0; }
.channel-save-item :deep(.ant-form-item-control-input-content) { display: flex; }

.rules-heading { padding: var(--space-md) var(--space-lg); border-bottom: 1px solid var(--color-border-light); }
.rules-heading-actions { display: flex; flex-shrink: 0; align-items: center; gap: var(--space-md); }
.rule-count { color: var(--color-text-tertiary); font-size: 12px; }
.rule-list { min-width: 0; }
.rule-row { display: grid; grid-template-columns: minmax(210px, 1.05fr) minmax(260px, 1.2fr) minmax(260px, 1.15fr) 82px; align-items: center; gap: var(--space-lg); padding: 14px var(--space-lg); border-bottom: 1px solid var(--color-border-light); transition: background-color 160ms ease-out; }
.rule-row:last-child { border-bottom: 0; }
.rule-row:hover { background: var(--color-bg); }
.rule-row.is-disabled { background: #fafafa; }
.rule-row.is-disabled .rule-identity, .rule-row.is-disabled .rule-field { opacity: 0.62; }
.rule-name-line { display: flex; align-items: center; gap: var(--space-sm); color: var(--color-text-primary); }
.rule-indicator { width: 8px; height: 8px; flex: 0 0 auto; border-radius: 999px; background: var(--color-accent); }
.rule-indicator-warning { background: var(--color-warning); }
.rule-indicator-danger { background: var(--color-danger); }
.rule-indicator-success { background: var(--color-success); }
.rule-identity p { margin: 4px 0 0 16px; color: var(--color-text-secondary); font-size: 12px; line-height: 1.45; }
.rule-field { min-width: 0; }
.rule-field label { display: block; margin-bottom: 5px; color: var(--color-text-tertiary); font-size: 11px; }
.rule-field :deep(.ant-select) { width: 100%; }
.field-warning { display: block; margin-top: 3px; color: var(--color-warning); font-size: 11px; }
.inline-threshold { display: flex; align-items: center; gap: var(--space-xs); color: var(--color-text-secondary); font-size: 12px; white-space: nowrap; }
.inline-threshold :deep(.ant-input-number) { width: 82px; }
.trigger-copy { display: block; min-height: 32px; padding: 6px 10px; color: var(--color-text-secondary); background: var(--color-bg); border-radius: var(--radius-sm); font-size: 12px; line-height: 20px; }
.rule-status { display: flex; flex-direction: column; align-items: flex-end; gap: 5px; }
.rule-status span { color: var(--color-text-tertiary); font-size: 11px; }

.history-panel { overflow: hidden; }
.history-toolbar { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-lg); padding: var(--space-md) var(--space-lg); border-bottom: 1px solid var(--color-border-light); }
.history-filters { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: var(--space-sm); }
.history-filters :deep(.ant-select) { width: 120px; }
.history-search { width: 240px; }
.history-panel :deep(.ant-table-wrapper) { padding: 0 var(--space-md) var(--space-md); }
.history-panel :deep(.ant-table-cell) { vertical-align: middle; }
.message-cell { min-width: 220px; }
.message-cell strong, .message-cell span { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.message-cell strong { color: var(--color-text-primary); font-size: 13px; font-weight: 600; }
.message-cell span { margin-top: 2px; color: var(--color-text-secondary); font-size: 11px; }
.time-cell, .channel-cell, .read-status, .confirm-status { color: var(--color-text-secondary); font-size: 12px; }
.read-status:not(.is-read) { color: var(--color-accent); font-weight: 600; }

@media (max-width: 1180px) {
  .rule-row { grid-template-columns: minmax(210px, 1fr) minmax(250px, 1.2fr) 82px; gap: var(--space-md); }
  .rule-trigger-field { grid-column: 2; }
  .rule-status { grid-column: 3; grid-row: 1 / span 2; }
  .wecom-form { grid-template-columns: repeat(3, minmax(150px, 1fr)); }
  .channel-save-item { grid-column: 1 / -1; }
  .history-toolbar { flex-direction: column; }
  .history-filters { width: 100%; justify-content: flex-start; }
}

@media (max-width: 760px) {
  .alert-page-header { align-items: stretch; flex-direction: column; }
  .summary-strip { flex-wrap: wrap; }
  .summary-item { flex: 1 1 50%; border-bottom: 1px solid var(--color-border-light); }
  .summary-item:nth-child(2) { border-right: 0; }
  .summary-item:nth-last-child(-n + 2) { border-bottom: 0; }
  .section-heading { flex-direction: column; }
  .rules-heading-actions { width: 100%; justify-content: space-between; }
  .wecom-form { grid-template-columns: 1fr; }
  .channel-save-item { grid-column: auto; }
  .rule-row { grid-template-columns: 1fr auto; align-items: start; }
  .rule-recipient-field, .rule-trigger-field { grid-column: 1 / -1; }
  .rule-status { grid-column: 2; grid-row: 1; }
  .history-filters > *, .history-search, .history-filters :deep(.ant-select) { width: 100%; }
}

@media (prefers-reduced-motion: reduce) {
  .rule-row { transition: none; }
}
</style>
