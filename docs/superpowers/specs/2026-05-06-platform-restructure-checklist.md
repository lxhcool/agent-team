# Platform Restructure Checklist

日期：2026-05-06  
状态：执行中

## 目标

把当前产品从“多个并列功能入口”收口成“以 Workspace 为主线的 Agent Team 平台”。

## 结构原则

- 首页是平台入口
- Workspace 是唯一主任务容器
- Planning/Sessions 不再作为独立产品线保留
- Roundtable 弱化为辅助能力
- CLI 暂不在这轮改造中处理

## 改造清单

### 一、主结构调整

- [x] 明确产品主线：Workspace
- [x] 明确弱化对象：Roundtable
- [x] 明确退出主舞台对象：Sessions / Planning
- [x] 首页改成平台入口，不再并列暴露 Sessions / Roundtable
- [x] 主导航去掉 Sessions 一级入口
- [x] Roundtable 不再占首页主版面
- [x] 头部保留 Roundtable 菜单入口

### 二、首页 `/`

- [x] 移除 Workspace / Roundtable 双模式主切换
- [x] 主 CTA 收口为“创建 Workspace / 开始一个任务”
- [x] 文案从“项目工作区”调整为“平台任务入口”
- [x] 保留最近 Workspace
- [x] Roundtable 从首页移除，仅保留头部菜单入口
- [x] 移除首页对 Planning 的任何主路径暗示

### 三、Workspace 列表 `/workspaces`

- [x] 从“产品项目列表”改成“任务工作区列表”
- [x] 文案弱化固定开发阶段叙述
- [x] 强调目标、进展、更新时间、本地绑定状态
- [x] 新建 Workspace 文案支持更通用任务表达

### 四、Workspace 详情 `/workspaces/[id]`

- [x] 保持双栏主工作台方向
- [x] 持续收口成平台通用任务语言
- [x] 让“规划能力”作为 Workspace 内能力存在
- [x] 执行结果内联进 Workspace，独立执行页只保留详细记录
- [ ] 为后续吸收 Roundtable 预留位置

### 五、Sessions / Planning

- [x] 从主导航移除
- [x] 从首页移除
- [x] 不再继续强化为独立产品线
- [ ] 后续将有价值的规划能力逐步并入 Workspace

### 六、Roundtable

- [x] 不再占首页主入口位置
- [x] 头部保留菜单入口
- [x] 保留页面，但定位为辅助讨论能力
- [x] 让讨论结果可以沉淀到 Workspace

### 七、验证

- [x] 前端类型检查通过
- [x] 首页信息架构符合平台入口定位
- [x] 首页主线只剩平台入口 + Workspace，Roundtable 仅保留头部菜单入口
- [x] Sessions / Roundtable 不再抢主线

## 本轮范围说明

本轮优先做信息架构与可见层改造，不先重构后端任务模型，不处理 CLI。
