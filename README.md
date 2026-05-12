# Neuroadaptive

神经自适应 AI 辅助创意系统。

## Curry9 LSL 闭眼静息 IAF 第一阶段与完整实验流程

本仓库当前包含一个本地网页实验应用，用于在 macOS 本机通过 LSL 接收 Curry9 EEG 数据，完成 EEG 校准、IAF 测定、DAT 预试、练习试次、正式写作试次、试后评分和数据导出。

### 功能

- 入口页面填写被试编号、年龄、背景和资格字段。
- 导入并校验正式材料表，未导入有效材料时不能创建正式会话。
- Stage 1：DAT 预试，保存原始答题。
- Stage 2：睁眼屏幕基线 120 秒、闭眼 IAF 基线 120 秒。
- Stage 3：五个练习试次，每个条件一次。
- Stage 4：十五个正式试次，五条件各三次。
- Stage 5：结束评分、debrief 文案和 JSON/CSV 导出。
- 支持 No-AI、构思前 AI、构思后 AI、神经自适应 AI、安慰剂五种条件。
- 神经自适应控制器支持真实 EEG feature 输入和 deterministic simulation 开发模式。

IAF 算法参考 Corcoran 等人的 `restingIAF` 方法思路：Welch PSD、Savitzky-Golay 平滑、alpha 峰值频率和中心频率估计。代码为独立 Python 实现，没有复制 GPL MATLAB 源码。

### 安装与运行

```bash
python3 -m pip install -r requirements.txt
npm install
npm run build
uvicorn app.main:app --reload
```

打开浏览器访问：

```text
http://127.0.0.1:8000
```

### 材料表格式

通过网页入口导入 `.csv` 或 `.xlsx`。必需列：

```text
phase,prompt_id,theme,subpremise_id,premise_text,suggestion_text,suggestion_model,suggestion_generated_at,generation_prompt_version
```

可选列：

```text
difficulty,condition_slot,participant_slot,notes
```

`phase` 必须为 `practice` 或 `formal`。正式运行要求至少 5 条 practice 和 20 条 formal。

### Curry9 / LSL 配置

在 Curry9 Windows 电脑中开启 LSL 输出，并确认 Mac 与 Windows 位于同一网络且防火墙允许 LSL 发现和传输。

可通过环境变量调整采集参数：

```bash
export NEUROADAPTIVE_LSL_TYPE=EEG
export NEUROADAPTIVE_LSL_NAME="Curry9 EEG"
export NEUROADAPTIVE_TARGET_CHANNELS="P3,Pz,PO3,POz,PO4,O1,O2"
export NEUROADAPTIVE_POSTERIOR_CHANNELS="Pz,PO3,PO4,O1,O2"
export NEUROADAPTIVE_FRONTAL_CHANNELS="Fz,FCz,AFz"
export NEUROADAPTIVE_EYES_OPEN_SECONDS=120
export NEUROADAPTIVE_EYES_CLOSED_SECONDS=120
```

`NEUROADAPTIVE_LSL_NAME` 不设置时，会使用发现到的第一个 `type=EEG` 的 LSL stream。

### 本地模拟 LSL 流

没有 Curry9 设备时，可以用合成 10 Hz alpha 信号测试网页流程：

```bash
python3 tools/simulate_lsl.py
```

另开一个终端启动网页服务，再访问 `http://127.0.0.1:8000`。正式实验流程的 Neuroadaptive 条件也可以在创建会话时选择 `simulation` 控制器做无设备调试。

### 测试

```bash
pytest
```
