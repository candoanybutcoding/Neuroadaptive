# Neuroadaptive Experiment System — Agent Guide

## 项目概述

本项目是一个**神经自适应 AI 辅助创意写作实验系统**，用于在 macOS 本机通过 LSL（Lab Streaming Layer）接收 Curry9 EEG 数据，完成 EEG 校准、IAF（Individual Alpha Frequency）测定、DAT 预试、练习试次、正式写作试次、试后评分和数据导出。

实验流程包含 5 个阶段：
1. **DAT 预试**：被试输入 10 个语义距离远的中文词
2. **EEG 基线校准**：睁眼屏幕基线 120 秒 + 闭眼 IAF 基线 120 秒
3. **练习试次**：5 个试次，每个条件一次
4. **正式试次**：15 个试次，5 种条件各 3 次
5. **结束评分与导出**：问卷评分，支持 JSON/CSV 导出

支持的 5 种实验条件：
- `no_ai`：无 AI
- `fixed_early`：构思前 AI
- `fixed_delayed`：构思后 AI
- `neuroadaptive`：神经自适应 AI（基于 EEG 特征触发建议）
- `yoked_sham`：安慰剂（yoked 控制）

## 技术栈

- **后端**：Python 3 + FastAPI + SQLite（WAL 模式）
- **前端**：React 19 + Vite（ES Module）
- **科学计算**：NumPy、SciPy（信号处理、Welch PSD、Savitzky-Golay 滤波）
- **EEG 采集**：pylsl（Lab Streaming Layer）
- **测试**：pytest + fastapi.testclient.TestClient
- **构建工具**：Vite（前端）、uvicorn（后端 ASGI）

## 项目结构

```
├── app/                    # Python 后端
│   ├── main.py             # FastAPI 路由与入口
│   ├── config.py           # 环境变量配置（NEUROADAPTIVE_*）
│   ├── db.py               # SQLite 数据库连接、Schema、初始化
│   ├── experiment.py       # 会话生命周期、试次调度、事件存储、导出
│   ├── state_machine.py    # 实验条件、拉丁方设计、时间线生成
│   ├── calibration.py      # 睁眼/闭眼校准流程 + SSE 推送
│   ├── iaf.py              # IAF 算法（Welch PSD、PAF/CoG 估计）
│   ├── eeg.py              # 在线特征提取、伪迹剔除、神经自适应控制器
│   ├── controller.py       # 决策逻辑：真实 / 模拟 / yoked
│   ├── lsl.py              # LSL 流发现与采集
│   ├── materials.py        # 材料导入验证（CSV/XLSX）
│   ├── default_materials.py# 内置 20 条故事命题
│   ├── text_validation.py  # 四句续写验证
│   └── session.py          # （旧版）IAF 采集 SessionManager（保留）
├── src/                    # React 前端源码
│   ├── main.jsx            # 主应用组件（实验流程 UI）
│   ├── api.js              # fetch API 封装
│   └── styles.css          # 样式
├── static/                 # 兜底静态文件（开发时备用）
├── dist/                   # Vite 构建输出（FastAPI 直接挂载）
├── tests/                  # pytest 测试
│   ├── test_api.py         # API 端到端测试
│   ├── test_iaf.py         # IAF 算法测试
│   ├── test_state_machine.py# 调度与时间线测试
│   ├── test_materials.py   # 材料验证测试
│   └── test_eeg_controller.py # EEG 特征与控制器测试
├── tools/
│   └── simulate_lsl.py     # 本地合成 alpha 信号模拟 LSL 流
├── data/                   # SQLite 数据库（.gitignore）
├── requirements.txt        # Python 依赖
├── package.json            # Node 依赖与脚本
└── vite.config.js          # Vite 配置（proxy /api → :8000）
```

## 构建与运行

### 安装依赖

```bash
python3 -m pip install -r requirements.txt
npm install
```

### 构建前端

```bash
npm run build
```

构建产物输出到 `dist/`，FastAPI 在 `app/main.py` 中通过 `StaticFiles` 自动挂载 `dist/assets/` 和 `dist/index.html`。

### 启动后端服务

```bash
uvicorn app.main:app --reload
```

打开浏览器访问 `http://127.0.0.1:8000`。

### 开发模式（前端热更新）

```bash
npm run dev        # Vite 开发服务器，代理 /api 到 127.0.0.1:8000
# 另开终端
uvicorn app.main:app --reload
```

### 本地模拟 LSL 流（无设备调试）

```bash
python3 tools/simulate_lsl.py
```

合成 10 Hz alpha 信号，可在创建会话时选择 `simulation` 控制器做无设备调试。

## 测试

```bash
pytest
```

测试覆盖：
- `test_api.py`：会话创建、试次流转、材料调度、导出流程的 API 端到端测试
- `test_iaf.py`：合成 alpha 信号的 IAF 估计精度、缺失通道错误、采样率检查
- `test_state_machine.py`：拉丁方平衡、正式被试排布、时间线分段、四句验证
- `test_materials.py`：CSV/XLSX 材料解析与验证规则
- `test_eeg_controller.py`：在线特征提取、伪迹剔除、联合下降触发器、模拟决策确定性

测试使用 `tmp_path` 创建隔离的 SQLite 数据库，通过 `fastapi.testclient.TestClient` 调用 API。

## 代码规范

- 所有 Python 文件顶部写 `from __future__ import annotations`
- 使用类型注解（PEP 484），`dict[str, Any]`、`list[dict]` 等
- 用户可见文本以中文为主；代码变量名、API 路由、错误码为英文
- API 错误码使用全大写下划线命名，如 `PARTICIPANT_ID_OUT_OF_SCHEDULE_RANGE`、`FOUR_SENTENCE_REQUIREMENT_NOT_MET`
- Pydantic `BaseModel` 定义请求体；`Field` 标注约束
- JSON 序列化禁用 ASCII 转义：`json.dumps(..., ensure_ascii=False, allow_nan=False)`
- 数据库操作使用 `with conn:` 事务块

## 配置（环境变量）

所有配置通过 `NEUROADAPTIVE_*` 环境变量读取，默认值在 `app/config.py` 中定义：

| 变量 | 说明 | 默认值 |
|---|---|---|
| `NEUROADAPTIVE_DB_PATH` | SQLite 数据库路径 | `data/experiment.db` |
| `NEUROADAPTIVE_LSL_TYPE` | LSL 流类型 | `EEG` |
| `NEUROADAPTIVE_LSL_NAME` | LSL 流名称（可选） | `None` |
| `NEUROADAPTIVE_LSL_TIMEOUT` | LSL 发现超时（秒） | `10` |
| `NEUROADAPTIVE_EYES_OPEN_SECONDS` | 睁眼基线时长 | `120` |
| `NEUROADAPTIVE_EYES_CLOSED_SECONDS` | 闭眼 IAF 时长 | `120` |
| `NEUROADAPTIVE_TRIM_START_SECONDS` | 去头时长 | `4` |
| `NEUROADAPTIVE_TRIM_END_SECONDS` | 去尾时长 | `4` |
| `NEUROADAPTIVE_TARGET_CHANNELS` | IAF 目标通道 | `P3,Pz,PO3,POz,PO4,O1,O2` |
| `NEUROADAPTIVE_POSTERIOR_CHANNELS` | alpha 特征通道 | `Pz,PO3,PO4,O1,O2` |
| `NEUROADAPTIVE_FRONTAL_CHANNELS` | theta 特征通道 | `Fz,FCz,AFz` |
| `NEUROADAPTIVE_MAINS_FREQUENCY_HZ` | 工频（陷波） | `50` |
| `NEUROADAPTIVE_CONTROLLER_MODE` | 默认控制器模式 | `simulation` |

## 数据库

SQLite，启用 WAL 模式（`PRAGMA journal_mode = WAL`）和外键约束。核心表：

- `participants` / `sessions`：被试与会话
- `materials` / `trial_schedule`：材料与试次排布
- `trials`：试次状态与写作结果
- `baseline_runs` / `iaf_results`：校准运行与 IAF 结果
- `phase_events` / `keystroke_events` / `suggestion_events`：事件日志
- `controller_windows` / `controller_decisions`：控制器窗口与决策
- `ratings`：评分数据
- `system_logs`：系统日志

数据库连接为全局单例（`get_db()`），测试中通过 `db._connection` 替换为隔离连接。

## 部署注意事项

- 当前为本地单机部署，服务绑定 `127.0.0.1:8000`
- Vite 开发服务器代理 `/api` 到后端；生产环境由 FastAPI 直接托管静态文件
- CORS 允许 `http://127.0.0.1:5173` 和 `http://localhost:5173`
- Curry9 与 macOS 需在同一网络，且防火墙允许 LSL 组播发现
- 正式实验要求被试编号为 `1`–`20`，对应 `state_machine.py` 中的 `_OFFICIAL_SCHEDULE_ROWS`
