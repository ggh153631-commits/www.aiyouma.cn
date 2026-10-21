# 欧意现货：每周动量轮动 + 波动率控仓

这是一个 Freqtrade 策略，交易欧意 USDT 现货。默认模拟盘，配置里没有 API 密钥。

研究对应关系：

- Liu、Tsyvinski、Wu 在 *Journal of Finance*（2022）的加密货币三因子：过去 1 到 4 周的赢家下周还是赢家，3 周动量最强，每周重新排序。25 个左右的流动性币里，最强的一组大约是 5 个。
- 波动率管理（Barroso、Santa-Clara，以及 2025 年 *Financial Markets and Portfolio Management* 上的加密货币动量论文）：波动大时把仓位缩小，崩盘会轻一些。这里做成每个币自己的仓位上限，现货不加杠杆。

论文里的因子是多空两边都做。欧意现货不能做空，所以这个策略只买赢家，输家直接不持有。比特币近 3 周收益为负时，新开仓减半；低于 -8% 时，下一次周换仓把持仓清空。这两条是风控，不是论文里的规则。

## 买卖规则

每个币用日线。信号写在周日那根已经走完的 K 线上，下一根 K 线开盘成交，也就是周一开盘。

买入，四个条件同时成立：

1. 当天是周日换仓日。
2. 过去 21 天涨幅大于 0，并且在全池里排名前 3。
3. 过去 28 天日收益波动率大于 0。
4. 比特币过去 21 天涨幅不低于 -8%。

卖出，周日换仓时只要下面任何一条成立：

- 排名掉出前 3，或者 21 天涨幅小于等于 0。
- 比特币 21 天涨幅低于 -8%，或者比特币数据缺失。

另外有一条周内灾难止损：浮亏到 -30% 就砍。平时不因为浮盈出场。`minimal_roi` 设成 100，等于关掉止盈。-20% 在两段回测里都会把反弹前的仓位砍掉，所以放宽到 -30%。

仓位：可用资金分成 3 份。每个币再乘一个波动率系数，目标是把该币的年化波动压到 50% 附近。系数限制在 0.25 到 1 之间，波动大的币买得少，波动小的币也不会加杠杆。比特币 21 天收益在 -8% 和 0 之间时，新开仓再乘 0.5。已经持有的仓位不会在周中改大小。最多同时持有 3 个。

## 调参数

参数都在 `user_data/strategies/WeeklyMomentumVolStrategy.py` 类顶部。

| 参数 | 默认 | 作用 |
| --- | --- | --- |
| `momentum_days` | 21 | 动量回看天数。论文里 7、14、21、28 天都有效，21 天最强。 |
| `vol_days` | 28 | 波动率回看天数。 |
| `top_n` | 3 | 每周持有几个赢家。论文里约 25 个币的第一组是 5 个；改成 3 个后，两段回测的收益更高、回撤更低。 |
| `exit_rank` | 3 | 排名掉到这个数之外才卖。改成 8 可以减少换手，同时要把 `max_open_trades` 至少改成 8。 |
| `target_ann_vol` | 0.50 | 单个币的年化波动目标。调低会整体更轻仓。 |
| `min_vol_scale` / `max_vol_scale` | 0.25 / 1.0 | 波动率系数的下限和上限。 |
| `btc_reduce_below` | 0.0 | 比特币动量低于这个数，新仓减半。 |
| `btc_block_below` | -0.08 | 比特币动量低于这个数，周日清仓并且不开新仓。 |
| `reduced_regime_scale` | 0.5 | 减仓时乘的系数。 |
| `stoploss` | -0.30 | 周内灾难止损。配置文件里的 `stoploss` 会覆盖这里，两处要一起改。 |

`config.json` 和 `config.backtest.json` 里的 `max_open_trades` 要和策略类上的 `max_open_trades` 保持一致。策略类上的值会覆盖配置。

## 安装

```bash
cd quant/okx-momentum
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -r requirements.txt
```

这台机器上的虚拟环境不会进 git。代码在仓库里，换机器后按上面的命令重装。

## 更新可交易币池

回测币池是脚本当时扫到的欧意加密货币 USDT 现货成交额前 25 名。只留欧意 `instCategory = 1` 的币，去掉稳定币、杠杆代币、代币化股票（例如 XSOXL、XTSLA）和黄金代币（XAUT）。上线不足 60 天的不要，24 小时成交额低于 200 万 USDT 的不要。名单里保证有 BTC 和 ETH。

```bash
cd quant/okx-momentum
.venv/bin/python scripts/refresh_okx_universe.py
```

这个名单是「现在流动性好的币」，然后拿去回测整段历史。死掉的旧币不在里面，刚变热门的币会被算进它们还没那么热门的时期。所以回测偏乐观，不能当成严格的样本外研究。

模拟盘配置 `config.json` 不使用这份冻结名单。它每次按 24 小时成交额取前 40，再去掉上线不足 60 天的币，然后策略仍然只持有动量前 3。

## 回测

手续费按吃单 0.1% 计算。起始资金 10000 USDT。

```bash
cd quant/okx-momentum
bash scripts/backtest.sh
```

也可以分成两步：

```bash
.venv/bin/freqtrade download-data --userdir user_data --config config.backtest.json --timeframe 1d --timerange 20230101-
.venv/bin/freqtrade backtesting --userdir user_data --config config.backtest.json --strategy WeeklyMomentumVolStrategy --timeframe 1d --timerange 20230101- --fee 0.001 --breakdown month
```

K 线数据在 `user_data/data/`，不进 git。

## 模拟盘

不要把密钥写进配置文件。密钥用环境变量：

- `FREQTRADE__EXCHANGE__KEY`
- `FREQTRADE__EXCHANGE__SECRET`
- `FREQTRADE__EXCHANGE__PASSWORD`（欧意的 Passphrase）

模拟盘不需要密钥，直接：

```bash
cd quant/okx-momentum
.venv/bin/freqtrade trade --userdir user_data --config config.json --strategy WeeklyMomentumVolStrategy
```

配置里 `dry_run` 是 `true`。改成实盘之前先看完一轮周日换仓，并确认 `dry_run` 仍是你想要的状态。这个目录不包含自动下实盘的开关。

## 回测结果

当前规则是每周持有前 3 名，止损 -30%。下面两段都用这个规则重跑过。起始 10000 USDT，欧意日线，手续费按吃单 0.1%。币池仍是 2026-10-21 的成交额前 25 名，套在整段历史上，已经归零的币不在里面，结果偏乐观。只拿 3 个币，单币出错时账户波动更大。

### 2023-02-10 到 2026-10-20

221 笔。结束资金 32829 USDT，+228.3%，年化 38.0%。账户最大回撤 29.2%（按钱包曲线是 32.2%）。同期一直持有比特币到 38094 USDT，+280.9%，最大回撤 53.0%。

长周期仍然跑不赢一直拿着比特币。持有前 5 名、止损 -30% 时，同样区间结束资金是 28556 USDT，回撤 32.1%。

### 只看 2026 年

2026-01-01 到 2026-10-20，重新从 10000 USDT 起算，44 笔。

| | 前 3 名 | 前 5 名、止损 -30% | 一直持有比特币 |
| --- | --- | --- | --- |
| 结束资金 | 13910 USDT | 11364 USDT | 9474 USDT |
| 总收益 | +39.1% | +13.6% | -5.3% |
| 最大回撤 | 16.3% | 18.9% | 39.5% |

手续费加滑点按 0.2% 再跑一遍，前 3 名长周期结束资金是 30870 USDT，今年是 13759 USDT，仍然高于前 5 名。
