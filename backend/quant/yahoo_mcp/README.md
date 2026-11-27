# Stock information MCP server (Yahoo Finance)

The tools behind the quant system's **stock information** agent: prices, history, news, statements, holders, options, recommendations, targets and projections from Yahoo Finance (via `yfinance`, about 15 minutes delayed).

Based on [Alex2Yang97/yahoo-finance-mcp](https://github.com/Alex2Yang97/yahoo-finance-mcp) (MIT, see `LICENSE`), extended for this app: P/E ratios, target price, news sentiment and five-year projection tools; the app's logging and configuration; and every tool runs in a thread pool so one slow Yahoo request never holds up other users.

## Running

Started with the other MCP servers by `scripts/start_mcp_servers.sh` or, in Docker, by the API container. By hand, from this folder with `PYTHONPATH` pointing at `backend/`:

```bash
PYTHONPATH=../.. python server.py        # streamable HTTP on 127.0.0.1:8565, path /mcp
```

| Setting | Default | Meaning |
|---|---|---|
| `MCP_STOCK_INFO_URL` | `http://127.0.0.1:8565/mcp` | Where the agents find this server |
| `MCP_STOCK_INFO_BIND` | `127.0.0.1` | Address it listens on (it has no login of its own; keep it on loopback or a private network) |
| `MCP_STOCK_INFO_ENABLED` | `true` | Whether the agents use it |
| `MCP_TOOL_THREADS` | `32` | Concurrent Yahoo calls |

## Tools

`get_historical_stock_prices` · `get_stock_info` · `get_yahoo_finance_news` · `get_stock_actions` · `get_financial_statement` · `get_holder_info` · `get_option_expiration_dates` · `get_option_chain` · `get_recommendations` · `get_target_price` · `get_news_sentiment_and_price_prediction` · `get_stock_5_year_projection` · `get_pe_ratios`
