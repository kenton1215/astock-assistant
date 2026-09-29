# 📈 A股短线交易助手（智能体）

严格按**短线作手交易体系**开发的 A 股智能选股与持仓分析助手：

- 每个交易日 **6 个时段**自动运行：**10:00 早盘观察 / 10:30 盘中观察 / 11:30 午间复盘 / 14:00 午后观察 / 14:30 尾盘观察 / 15:00 收盘报告**
- 从沪深两市（不含北交所）筛选**最多 6 只**符合买进模式的股票，给出**买入理由 + 建议买入价格区间 + 止损位 + 目标位 + 仓位**
- 根据你的持仓（网页表格或 `holdings.json` 输入股票代码），逐只分析**卖出 / 减仓 / 继续持有**，给出原因与参考价格
- **Tushare 财务增强**（2000+积分自动启用，不足自动降级）：估值（PE/PB/PS）、财务指标（ROE/净利同比/营收同比/毛利率/负债率）、业绩预告、主力资金流向（近5日）、龙虎榜、官方连板/封单、**筹码获利盘（抛压）**、**北向资金**——候选基本面速览 + 持仓风险体检（亏损/暴雷/高负债/高估值/抛压重自动预警）
- **个股深度体检**：输入任意代码 → 技术面+资金面+基本面+消息面 + 大模型综合建议（支撑/压力/止损/风险点）
- 内置**情绪周期模型、板块效应、五维选股指标、五种买点模式、检查清单**（见 [docs/短线交易体系.md](docs/短线交易体系.md) 与 [docs/短线交易检查清单.md](docs/短线交易检查清单.md)）
- **多用户账户系统**：注册即送 5 次免费分析（注册规则宽松：用户名 2-30 字符支持中文、密码至少 4 位）；自带 DeepSeek Key 免费使用；激活码付费（支持 Streamlit Cloud 部署）

> ⚠️ **免责声明：本工具仅供量化研究参考，不构成任何投资建议，不保证盈利。股市有风险，入市需谨慎。**

---

## 一、功能架构

```
盘前预热 08:55（缓存历史日线）
        │
10:00 / 10:30 / 11:30 / 14:00 / 14:30 / 15:00  ← 每个交易日自动运行
        │
        ├─ Tushare 历史日线（增量缓存，最近70个交易日）
        ├─ 东方财富实时快照（当日行情/主力资金/板块，免费接口；被限流自动切换腾讯备源）
        ├─ Tushare 财务增强（估值/财务/资金流/龙虎榜/获利盘/北向，2000+积分启用）
        ├─ Tavily 联网资讯（政策/热点题材，可选）
        ├─ 情绪周期定位：涨停/跌停/炸板率/连板高度/昨涨停今日表现
        ├─ 板块效应：行业/概念涨幅榜 + 板块内涨停家数 + 主力净流入
        ├─ 规则初筛：市值/成交额/换手/量比/趋势/ST/一字板 硬性过滤 → 多因子打分 Top50
        ├─ DeepSeek 大模型：按作手体系+检查清单逐项复核 → 输出结构化分析
        └─ 生成 Markdown 报告 → reports/YYYY-MM-DD/ 目录
```

**容错设计**：大模型调用失败 → 自动降级"规则模式"仍输出完整报告；实时行情失败 → 回退最近交易日收盘数据；资讯失败 → 静默跳过。

---

## 二、安装

1. **Python ≥ 3.9**（Windows），安装依赖：

   ```bash
   pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
   ```

2. **配置 `.env`**（已包含所需 Key，核对即可；模板见 `.env.example`）：

   | 变量 | 说明 |
   |---|---|
   | `TUSHARE_TOKEN` | Tushare Pro Token，[tushare.pro](https://tushare.pro) 注册获取，**基础 120 积分即可**（本工具只用 daily/stock_basic/trade_cal 三个基础接口） |
   | `DEEPSEEK_API_KEY` | DeepSeek 平台 API Key |
   | `DEEPSEEK_BASE_URL` | 默认 `https://api.deepseek.com/v1` |
   | `DEEPSEEK_MODEL` | 默认 `deepseek-flash`（即 DeepSeek-V4.1-Flash），也可用 `deepseek-v4-pro` / `deepseek-chat` |
   | `TAVILY_API_KEY` | Tavily 联网搜索 Key（可选，不配则跳过资讯检索） |

3. **配置持仓**：编辑 `holdings.json`（格式参考 `holdings.example.json`）：

   ```json
   [
     {"ts_code": "600519.SH", "name": "贵州茅台", "cost": 1400.0,
      "volume": 100, "buy_date": "2026-08-10", "note": "备注"}
   ]
   ```

---

## 三、运行方式

### 方式一：网页版（推荐 · 交互友好）

双击 `start_web.bat`（或 `streamlit run app.py`），浏览器自动打开操作界面：

- **左侧输入**：账户登录/注册（送5次）、选择分析时段、勾选选项、点击「🚀 开始分析」（约1~2分钟）；持仓在表格中直接编辑保存（登录用户独立保存）
- **主界面输出**：情绪周期/涨停跌停/炸板率等指标卡 → 七个页签（买入候选 / 持仓分析 / 板块主线 / 报告概览 / 检查清单 / 今日资讯 / 个股体检）
- **一键下载**：Excel 报告（多Sheet：总览/市场情绪/板块/买入候选/持仓/检查清单/初筛池/明日计划）+ Markdown 原文

### 方式二：命令行手动运行（随时跑一次）

```bash
python main.py --time 15:00     # 指定时段：10:00 / 10:30 / 11:30 / 14:00 / 14:30 / 15:00
python main.py --time auto      # 按当前时间自动选择时段
python main.py --time 15:00 --force   # 非交易日也强制运行（测试用）
python main.py --no-llm         # 不调大模型（纯规则模式，测试用）
```

或直接双击 `run_now.bat`。

### 方式三：定时自动运行（无人值守）

双击 `start_scheduler.bat`（或 `python scheduler.py`），保持窗口开启。交易日自动执行：
**08:55 盘前预热 → 10:00 / 10:30 / 11:30 / 14:00 / 14:30 / 15:00 六个会话**；非交易日自动跳过。

> 需保证：①系统时区为北京时间；②窗口保持开启（或改用 Windows 任务计划程序，见下方）。

### 方式四：Windows 任务计划程序（不依赖常驻窗口）

管理员命令行执行（路径按实际修改）：

```bat
schtasks /Create /TN "ASTOCK_1000" /TR "\"C:\Python\python.exe\" \"F:\A股短线交易助手\main.py\" --time 10:00" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:00 /F
schtasks /Create /TN "ASTOCK_1030" /TR "\"C:\Python\python.exe\" \"F:\A股短线交易助手\main.py\" --time 10:30" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 10:30 /F
schtasks /Create /TN "ASTOCK_1130" /TR "\"C:\Python\python.exe\" \"F:\A股短线交易助手\main.py\" --time 11:30" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 11:30 /F
schtasks /Create /TN "ASTOCK_1400" /TR "\"C:\Python\python.exe\" \"F:\A股短线交易助手\main.py\" --time 14:00" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 14:00 /F
schtasks /Create /TN "ASTOCK_1430" /TR "\"C:\Python\python.exe\" \"F:\A股短线交易助手\main.py\" --time 14:30" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 14:30 /F
schtasks /Create /TN "ASTOCK_1500" /TR "\"C:\Python\python.exe\" \"F:\A股短线交易助手\main.py\" --time 15:00" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 15:00 /F
```

---

## 四、账户系统与收费方案（网页版）

| 用户类型 | 说明 | 限制 |
|---|---|---|
| **游客（未登录）** | 本地模式，使用本机 .env 的站长 Key | 无账户数据 |
| **注册用户（免费体验）** | 注册即送 5 次分析（用站长 Key） | 免费次数用完为止 |
| **自带 DeepSeek Key** | 在「我的API Key」配置自己的 Key（加密存储） | 免费、不扣次数，每日 30 次上限 |
| **付费用户（激活码）** | 用站长 Key 分析 | 每分析 1 次扣 1 次额度，每日 30 次上限 |

**注册规则（宽松）**：用户名 2~30 个字符（支持中文/字母/数字/下划线，不含空格）；密码至少 4 位。次数/限额等参数在 `config.py` 的 `AUTH` 字典中调整。

**激活码生成（站长）**：`python tools/gen_codes.py --count 10 --credits 20`（生成 10 个码、每个 20 次）。收费方式自选：发卡平台（如面包多/爱发电/卡密发卡）、微信/支付宝转账后手工发码。所有密钥参数在 `config.py` 的 `AUTH` 字典中调整。

> 🔒 安全说明：密码 PBKDF2 加盐哈希；用户自配的 API Key 用 SECRET_KEY 派生密钥加密存储；站长 Key 仅存于服务端 .env/secrets，对用户不可见。

---

## 五、部署到 Streamlit Cloud

### 1. 推送到 GitHub

```bash
cd /d F:\A股短线交易助手
git init -b main
git remote add origin https://github.com/你的用户名/astock-assistant.git
git add -A
git commit -m "init"
git push -u origin main        # 首次推送会弹浏览器登录授权
```

> 安全已内置：`.gitignore` 自动排除 `.env`（密钥）、`data_cache/`（用户库与激活码）、`reports/`、`logs/`——推送后到仓库页面确认看不到 `.env`。

### 2. Streamlit Cloud 创建应用

[streamlit.io/cloud](https://streamlit.io/cloud) → **Sign in with GitHub** → New app → 选择仓库 → Main file path 填 `app.py` → Deploy

### 3. 配置 Secrets（Settings → Secrets）

```toml
TUSHARE_TOKEN = "你的token"
DEEPSEEK_API_KEY = "sk-xxx"        # 站长Key（付费用户使用）
DEEPSEEK_MODEL = "deepseek-flash"
TAVILY_API_KEY = "tvly-xxx"
SECRET_KEY = "随机长字符串"          # 用于加密用户API Key，务必设置
```

### 4. 账户持久化（Supabase，免费）

Cloud 文件系统重启即清空，用户数据必须外置存储：

1. 注册 [supabase.com](https://supabase.com) → 创建项目 → **SQL Editor** 执行：

   ```sql
   create table users (username text primary key, salt text, pw text, credits int default 5,
     created text, llm_key text default '', llm_base text default '', llm_model text default '',
     holdings text default '[]', last_day text default '', daily_count int default 0);
   create table codes (code text primary key, credits int, created text, used_by text default '', used_at text default '');
   ```

2. Supabase **Settings → Data API** 页面复制两个值：Project URL 和 **service_role** 密钥（不要用 anon public）

3. Secrets 中追加：

   ```toml
   AUTH_BACKEND = "supabase"
   SUPABASE_URL = "https://你的项目id.supabase.co"
   SUPABASE_KEY = "eyJ...（service_role密钥，仅服务端使用，勿写入前端）"
   ```

4. App 页面 **Reboot** → 注册一个测试账号 → Supabase 的 Table Editor 中 `users` 表能看到该用户即打通

### 5. 后续更新代码

本地修改后：`git add -A && git commit -m "说明" && git push` → Streamlit Cloud 自动重新部署（1~2 分钟生效）。

> 本地单机部署则无需 Supabase（默认 `AUTH_BACKEND=local`，用户数据存 `data_cache/users.json`）。

> ⚠️ 注意：Tushare 高频调用有积分/频次限制，多用户共享一个 Token 时请注意额度；财务增强接口需要 2000 积分，不足时自动降级不影响核心筛选。

---

## 六、报告解读

报告保存在 `reports/2026-09-29/1500_收盘报告.md`（六个时段各一份），包含：

1. **大盘与市场情绪**：涨跌家数、涨停/跌停/炸板、连板梯队、昨涨停今日表现、北向资金（可用时） + 情绪周期定位与总仓位建议
2. **板块主线**：行业 Top15 / 概念 Top10（涨幅、主力净流入、板块内涨停家数、领涨股）
3. **买入候选（≤6只）**：模式类型、建议买入区间、介入方式、止损位、目标位、建议仓位、四维买入理由 + 基本面速览（PE/ROE/业绩预告/主力资金/龙虎榜/获利盘）
4. **持仓分析**：逐只给出 卖出/减仓/加仓/继续持有 + 原因 + 参考价格 + 基本面风险体检
5. **检查清单核对结论**：A~E 共 25 项的关键结论
6. **明日盘前计划**（收盘报告）
7. **附录**：今日资讯摘要、规则初筛原始 Top30 候选池（含基本面列）

---

## 七、数据源说明（为什么要这样组合）

| 数据 | 来源 | 原因 |
|---|---|---|
| 历史日线/均线/连板 | Tushare（120积分基础接口） | 权威、稳定、支持缓存 |
| **当日实时行情** | 东方财富公开接口（首选）→ 腾讯行情接口（备源） | Tushare 实时接口无法批量拉全市场；东财接口免费、一次拉全、含主力净流入/量比/换手/行业；东财被限流时自动切换腾讯排行榜（候选股用腾讯批量行情补齐盘中高开低收） |
| **财务/资金/龙虎榜增强** | Tushare Pro 高积分接口（2000+积分自动启用，不足自动降级）：daily_basic估值、fina_indicator财务指标、moneyflow主力资金、moneyflow_hsgt北向资金、top_list龙虎榜、limit_list_d官方连板封单、cyq_perf筹码获利盘 | 候选基本面速览 + 持仓风险体检（亏损/暴雷/高负债/高估值/抛压自动预警） |
| 板块行情 | 东方财富行业/概念板块（不可用时按个股行业自动聚合） | 实时板块强度与资金，直接服务"板块效应"原则 |
| 政策/热点资讯 | Tavily 搜索 | 补足消息面，与行情数据共振验证 |
| 交易决策 | DeepSeek 大模型（站长Key或用户自带Key） | 内嵌作手体系+检查清单，对初筛结果逐项复核 |

收盘后（15:00 会话）的实时快照即为当日最终行情；Tushare 当日日线通常晚间发布，次日 08:55 预热时自动补入缓存。

---

## 八、常见问题

- **Q：Tushare 提示积分不足？** 核心筛选（日线/基础信息/日历）注册即送的 120 积分即可；财务增强（估值/财务/龙虎榜/获利盘等）需 2000+ 积分，不足时自动降级跳过。若提示 401/402，请核对 Token。
- **Q：实时行情拉取失败？** 自动切换链路：东财 → 腾讯 → Tushare 最近收盘数据（报告会注明数据源）。东财对高频请求有风控，本工具每次运行只发少量请求，正常每日 6 次运行不会触发。
- **Q：大模型报模型不存在？** 把 `.env` 或 Secrets 中 `DEEPSEEK_MODEL` 改为平台实际可用的模型 ID（本环境实测可用：`deepseek-flash`、`deepseek-v4-pro`、`deepseek-chat`）。
- **Q：想调整筛选参数（市值/成交额/换手）？** 修改 `config.py` 中 `SCREEN` 字典。
- **Q：想调整注册赠送次数/每日限额/激活码额度？** 修改 `config.py` 中 `AUTH` 字典（`free_trial_credits`、`daily_limit`、`default_code_credits`）。
- **Q：财务增强没生效？** 需 Tushare 积分 ≥2000（daily_basic/moneyflow/top_list/limit_list_d 等接口）；积分不足时自动降级，不影响核心筛选。
- **Q：忘记管理员怎么办？** 本地模式直接编辑 `data_cache/users.json`（可删除用户/重置次数/发放积分）；Supabase 模式在 Supabase 控制台操作 users 表。
- **Q：用户 API Key 安全吗？** Key 用 SECRET_KEY 派生密钥加密存储（服务端），用户无法查看他人 Key；站长 Key 只存在服务端 secrets 中。
- **Q：非交易日运行？** 自动跳过；测试可用 `--force` 强制运行。

---

## 九、目录结构

```
├── app.py                 # 网页版入口（Streamlit，多用户）
├── main.py                # 命令行单次运行入口
├── scheduler.py           # 定时调度器（常驻）
├── start_web.bat          # 一键启动网页版
├── start_scheduler.bat    # 一键启动调度器
├── run_now.bat            # 一键立即运行
├── config.py              # 全局配置：密钥/筛选参数/账户规则
├── holdings.json          # 本地模式持仓（网页版登录用户独立保存）
├── .env                   # 密钥（不入库）；模板见 .env.example
├── .gitignore             # 排除 .env/data_cache/reports/logs
├── src/
│   ├── pipeline.py        # 核心流水线（CLI与网页共用）
│   ├── auth.py            # 账户系统（注册/登录/积分/激活码，本地+Supabase）
│   ├── analysis/          # 个股深度体检
│   ├── data/              # Tushare/东财/腾讯行情、指标、初筛、财务增强
│   ├── news/              # Tavily 资讯检索
│   ├── llm/               # DeepSeek 客户端（支持用户自带Key）
│   ├── prompts/           # 作手体系+检查清单+体检提示词
│   ├── portfolio/         # 持仓读取
│   └── report/            # Markdown/Excel 报告生成
├── tools/
│   └── gen_codes.py       # 激活码生成工具（站长）
├── docs/
│   ├── 短线交易体系.md      # 完善后的量化交易体系
│   └── 短线交易检查清单.md   # 盘前/盘中/盘后检查清单
├── reports/               # 每日报告输出（不入库）
├── data_cache/            # 历史日线/日历/资讯缓存 + 本地用户库（不入库）
└── logs/                  # 运行日志（不入库）
```

---

**只做强势股，只做模式内。以上内容不构成投资建议，股市有风险，入市需谨慎。**
