# AGENTS.md — CRO仪器排程系统

## 技术栈
- 后端: Python 3.12 + FastAPI + SQLAlchemy + MySQL/SQLite
- 前端: Vue 3 + TypeScript + Ant Design Vue 4 + 图表组件 + vis-timeline
- 排程引擎: OR-Tools CP-SAT（Phase 2 引入）

---

## 后端规则

### 分层架构
```
api/        → 路由层：解析请求、调用一个 service、返回响应
services/   → 业务层：编排逻辑，不 import FastAPI/HTTPException
models/     → 数据层：SQLAlchemy ORM 模型
schemas/    → 接口层：Pydantic 请求/响应模型
core/       → 配置层：config + database 连接
```

### 路由层 (api/)
- 每个 handler ≤ 15 行
- 只做三件事：解析输入 → 调用 service → 返回响应
- 不在路由里写 SQL 或业务逻辑
- 每个端点声明 `response_model=`

### 业务层 (services/)
- 不 import `fastapi.HTTPException`
- 不 import `sqlalchemy`（那是 models 的事）
- 抛业务异常，由路由层转 HTTP 状态码

### 文件大小
- 单文件超过 400 行 → 计划拆分
- 单文件超过 600 行 → 必须先拆分再继续

---

## 前端规则

### 组件
- 使用 Vue SFC（`.vue`）和 `<script setup lang="ts">`
- 状态尽量靠近使用处，不提前提升
- 派生状态用 `computed`，副作用用 `watch` / 生命周期钩子
- 提取重复逻辑到 composable（`useXxx.ts`）

### TypeScript
- 对象定义用 `interface`，联合类型用 `type`
- 禁止 `any`，用 `unknown` 或具体类型
- Props 接口以 `Props` 结尾（如 `GanttProps`）
- `tsconfig.json` 保持 `strict: true`

### 命名
- 组件文件: PascalCase（`InstrumentGantt.vue`）
- 工具/composable 文件: camelCase（`useSchedule.ts`）
- 变量/函数: camelCase，布尔值加 `is/has` 前缀

### 状态管理
- 单页面状态用 `ref` / `reactive`
- 跨组件共享用 provide/inject 或轻量 composable（不引入 Redux）
- API 请求结果直接存在调用组件中

---

## 通用规则

### 反过度设计
- 只改用户要求的部分，不改无关代码
- 先上最简单的方案，确实需要再抽象
- 不为"以后可能"的需求写代码

### 代码质量
- 魔法数字用命名常量替代
- 函数只做一件事
- 重复代码提取为公共函数
- 变量名自解释，不加废话注释

### 提交
- 小步提交，一个改动一个 commit
- commit message 用中文描述做了什么

## 通用开发规则

### 代码体积与职责
- 单个文件不得超过 600 行；超过 400 行必须建立拆分计划。
- 文件保持单一职责；主函数只负责流程编排，数据处理和校验下沉到独立函数。
- 名称包含“与”或 `And` 的多职责函数应拆分为原子函数。

### API 与数据一致性
- 前端根据后端返回结构或元数据动态渲染，不在组件内硬编码业务提取规则。
- API 请求和数据类型集中在 `services/`、`api/` 或 `types/`，组件不得散落 HTTP 请求。
- 字段名、枚举、可空性和错误结构调整时，必须同步更新前后端调用方。

### 稳健性与日志
- 异步请求必须显式处理 loading、success、error 三种状态。
- 异常必须转换为清晰的中文提示，不允许页面无响应或只写控制台错误。
- 关键业务节点保留必要日志；日志不得输出密码、Token、密钥或完整敏感数据。

### 增量修改与验证
- 只修改目标模块，保留用户既有工作区变更，不新增未经允许的第三方依赖。
- 前端修改至少运行 TypeScript 检查或生产构建；后端修改至少运行目标测试。
- 提交前运行 `git diff --check`，确认没有空白错误、冲突标记或意外修改。
