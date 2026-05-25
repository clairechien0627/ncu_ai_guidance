# Dataset / Experiment / Evaluation 架構守則

> 目標：固定 Dataset、Experiment、Evaluation、Trace、Score 的命名與欄位邊界，避免同一份資料在不同表中用模糊名稱重複出現。

---

## 核心概念

這個模組不是另一套 observability model。核心觀測資料仍然是：

```text
TraceV2
└── Observation

Score
```

Dataset / Experiment / Evaluation 是圍繞 Trace 和 Score 的工作流層：

```text
Dataset          可重放測資集合
DatasetItem      一筆可重放測資
ExperimentRun    固定 Dataset + runtime config 的一次 controlled experiment
ExperimentRunItem 某筆 DatasetItem 在這次 Experiment 裡的執行結果
EvaluationRun    一次評分批次工作
EvaluationRunItem 某個 trace / experiment result 的一筆評分任務
Score            評分結果
```

基本規則：

```text
只要 agent / LLM / tool / evaluator 真正執行一次，就應產生 TraceV2 + Observations。
Run / RunItem 只保存批次管理狀態、關聯 id 與必要 preview/cache。
Score 是評分結果的唯一正式來源。
```

---

## 關係圖

```text
Dataset
└── DatasetItem
    ├── source_trace_id          optional: 從哪條真實 trace 擷取
    └── source_observation_id    optional: 從哪個 observation 擷取

ExperimentRun
└── ExperimentRunItem
    ├── dataset_item_id
    └── trace_id                 這次 experiment 產生的 output trace

TraceV2
└── Observation

EvaluationRun
└── EvaluationRunItem
    ├── trace_id                 被評分的 target trace
    ├── dataset_item_id          optional
    ├── experiment_item_id       optional
    └── score_ids

Score
├── trace_id                     被評分的 trace
├── observation_id               optional: 被評分的 observation
├── dataset_id / dataset_item_id
├── experiment_run_id / experiment_item_id
├── eval_run_id / eval_item_id
└── execution_trace_id           evaluator 自己執行產生的 trace，不能混用 eval_run_id
```

---

## 表責任

### Dataset

`datasets` 是測資集合，類似 test suite。

保留欄位：

```text
dataset_id
name
description
source
metadata
input_schema
expected_output_schema
is_archived
created_at
updated_at
```

欄位語意：

| 欄位 | 語意 |
|---|---|
| `source` | dataset 建立來源，例如 `manual`、`trace`、`low_quality`、`imported` |
| `metadata` | dataset-level 額外資訊，不放 item 細節 |
| `input_schema` | `DatasetItem.input` 的期望格式 |
| `expected_output_schema` | `DatasetItem.expected_output` 的期望格式 |

---

### DatasetItem

`dataset_items` 是一筆可重放測資。它不是 Observation 的同級，也不是 Trace 的 child；它是從 Trace / Observation / 人工資料抽出的測試案例。

目前欄位：

```text
dataset_item_id
dataset_id
input
output
expected_output
context
source_trace_id
source_observation_id
status
tags
metadata
is_archived
is_deleted
valid_from
valid_to
created_at
updated_at
```

建議語意：

| 欄位 | 語意 |
|---|---|
| `input` | 測試輸入，例如 user message、document_ids、payload |
| `expected_output` | 標準答案或期望答案；評分主要參考這個 |
| `output` | 來源 trace 的原始回答，只是 reference，不是 experiment output |
| `context` | sources、children、agent_name、原 trace 背景 |
| `source_trace_id` | 這筆測資從哪條 trace 擷取 |
| `source_observation_id` | 如果從特定 observation 擷取才填 |

長期建議改名：

```text
DatasetItem.output -> reference_output
```

原因：`output` 容易被誤解成某次 experiment 的輸出；實際 experiment 輸出在 `ExperimentRunItem.generated_output`，完整 source of truth 在 `TraceV2.output`。

---

### ExperimentRun

`experiment_runs` 是在固定 Dataset 上，用一組 runtime config 執行 agent / pipeline 的一次 controlled experiment。

它不只是單純 replay；未來可以表示 prompt、model、agent、retrieval config 等變因測試。

目前欄位：

```text
experiment_run_id
dataset_id
name
status
target_agent
model
prompt_name
prompt_version
runtime_config
metadata
total_count
succeeded_count
failed_count
last_error
started_at
completed_at
created_at
updated_at
```

欄位語意：

| 欄位 | 語意 |
|---|---|
| `target_agent` | 這次實驗要執行的 agent / generator adapter |
| `model` | 使用者要求的 model override |
| `prompt_name` / `prompt_version` | 使用者要求的 prompt override；不保證 adapter 一定支援 |
| `runtime_config` | 可執行設定，例如 `use_mini`、temperature、retrieval config |
| `metadata.requested_config` | 使用者要求的設定 |
| `metadata.applied_config` | 實際生效的設定 |

重要規則：

```text
實際使用的 prompt/model/agent 以 generated trace 的 observations 或 metadata.applied_config 為準。
prompt_name / prompt_version 如果 adapter 不支援 override，應保持 null 或只放 requested_config。
```

---

### ExperimentRunItem

`experiment_run_items` 是某筆 DatasetItem 在某次 ExperimentRun 中的執行結果。

目前欄位：

```text
experiment_item_id
experiment_run_id
dataset_item_id
status
generated_output
generated_context
trace_id
eval_run_id
error
started_at
completed_at
created_at
updated_at
```

欄位語意：

| 欄位 | 語意 |
|---|---|
| `dataset_item_id` | 這次執行的測資 |
| `trace_id` | 這次 experiment 執行產生的 TraceV2 |
| `generated_output` | `TraceV2.output` 的 preview/cache，不是 source of truth |
| `generated_context` | sources、agent_name、prompt、applied_config 等摘要 |
| `eval_run_id` | 這批 experiment result 後續被哪個 EvaluationRun 評分 |

長期建議改名：

```text
ExperimentRunItem.trace_id -> output_trace_id
ExperimentRunItem.eval_run_id -> evaluation_run_id
```

原因：`trace_id` 在這張表中代表「這次 experiment 產生的 output trace」，不是來源 trace，也不是 evaluator trace。

---

### EvaluationRun

`evaluation_runs` 是一次評分批次工作。

目前支援的 scope：

```text
single_trace       單條 trace 評分
trace_batch        批次 trace 評分
dataset            直接評 dataset item
experiment         評 experiment 產生的結果
```

目前欄位：

```text
eval_run_id
name
status
scope
target_trace_ids
model
prompt_name
prompt_version
dataset_id
dataset_item_count
metadata
total_count
succeeded_count
failed_count
last_error
started_at
completed_at
created_at
updated_at
```

欄位語意：

| 欄位 | 語意 |
|---|---|
| `scope` | 這次評分批次的目標類型 |
| `target_trace_ids` | 對 `single_trace` / `trace_batch` 是主要目標；對 `dataset` / `experiment` 只是輔助索引 |
| `model` / `prompt_name` / `prompt_version` | evaluator 的設定，不是被評 agent 的設定 |
| `dataset_id` | 如果這次評分與 dataset 有關才填 |

長期可考慮改名：

```text
eval_run_id -> evaluation_run_id
```

---

### EvaluationRunItem

`evaluation_run_items` 是一次 EvaluationRun 中的一筆評分任務。

目前欄位：

```text
eval_item_id
eval_run_id
trace_id
dataset_item_id
experiment_item_id
status
score_ids
error
started_at
completed_at
created_at
updated_at
```

欄位語意：

| 欄位 | 語意 |
|---|---|
| `trace_id` | 被評分的 trace |
| `dataset_item_id` | 這次評分對應的測資，可選 |
| `experiment_item_id` | 如果評的是 experiment result，指向 ExperimentRunItem |
| `score_ids` | 這筆評分任務產生的 scores |

長期建議改名：

```text
EvaluationRunItem.trace_id -> target_trace_id
EvaluationRunItem.experiment_item_id -> experiment_item_id 保留，或改成 experiment_run_item_id
eval_item_id -> evaluation_item_id
eval_run_id -> evaluation_run_id
```

原因：`trace_id` 在這張表中代表「被評分的 target trace」，不是 evaluator 自己執行產生的 trace。

---

### Score

`scores` 是評分結果的唯一正式來源。

建議語意：

| 欄位 | 語意 |
|---|---|
| `trace_id` | 被評的 trace |
| `observation_id` | 被評的 observation，可選 |
| `dataset_id` / `dataset_item_id` | 分數對應的測資 |
| `experiment_run_id` / `experiment_item_id` | 分數對應的 experiment result |
| `eval_run_id` / `eval_item_id` | 哪次 evaluation job 產生 |
| `execution_trace_id` | evaluator 自己執行產生的 trace |
| `environment` | 執行環境 |
| `thread_id` | 對話/session id |
| `name` | score name，例如 `overall`、`grounding`、`feedback` |
| `value` | numeric score |
| `string_value` | textual/categorical score |
| `comment` | 補充說明 |
| `metadata` | 非標準額外資訊 |

重要規則：

```text
Score.trace_id = 被評對象。
Score.execution_trace_id = 產生 score 的 evaluator trace。
Score.eval_run_id = 評分批次。

不要把 eval_run_id 長期塞進 execution_trace_id。
```

---

## Source Of Truth

| 資料 | Source of truth |
|---|---|
| 執行過程 | `TraceV2` + `Observation` |
| replay 實際輸出 | `ExperimentRunItem.trace_id -> TraceV2.output` |
| replay 列表 preview | `ExperimentRunItem.generated_output` |
| 測資 reference answer | `DatasetItem.output`，長期改名 `reference_output` |
| 標準答案 / 期望答案 | `DatasetItem.expected_output` |
| 評分結果 | `Score` |
| 評分任務狀態 | `EvaluationRunItem` |

---

## 命名收斂建議

先不立即改 DB，可以先在文件、API alias、前端命名上收斂。

優先改名候選：

```text
DatasetItem.output                 -> reference_output
ExperimentRunItem.trace_id         -> output_trace_id
ExperimentRunItem.eval_run_id      -> evaluation_run_id
EvaluationRunItem.trace_id         -> target_trace_id
EvaluationRun.eval_run_id          -> evaluation_run_id
EvaluationRunItem.eval_item_id     -> evaluation_item_id
```

不建議現在改名：

```text
ExperimentRun -> ReplayRun
ExperimentRunItem -> ReplayRunItem
```

原因：專題未來可能會走向 Langfuse-like Experiment，也就是比較 agent / prompt / model / retrieval config 的 controlled experiment。保留 Experiment 命名較有延展性。

---

## 建議落地順序

### Phase 1：文件化

新增此文件，固定表責任與欄位語意。

不動 DB schema。

### Phase 2：API alias

API 同時回傳舊名與新語意名，保持相容：

```text
DatasetItem.output                 + reference_output
ExperimentRunItem.trace_id         + output_trace_id
ExperimentRunItem.eval_run_id      + evaluation_run_id
EvaluationRunItem.trace_id         + target_trace_id
EvaluationRun.eval_run_id          + evaluation_run_id
EvaluationRunItem.eval_item_id     + evaluation_item_id
```

新程式碼優先讀新語意名。

### Phase 3：DB rename migration

確認前後端都改讀新語意名後，用 Alembic 做欄位 rename。

### Phase 4：移除相容舊名

API 與前端不再依賴舊欄位名後，移除 alias。

